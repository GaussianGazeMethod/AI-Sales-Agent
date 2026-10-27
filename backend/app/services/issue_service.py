"""闭环三 · 台账与投诉服务（P2-b）。

 《产品问题与承诺台账》= 痛点 C19 的落点：登记 → 派单（产研/销售）→ 跟踪 → **回复客户** → 统计。
   它同时服务：投诉管理、承诺兑现、续费风险提示、产品改进输入。

投诉口径（spec/04 §9）：**无独立流程** —— 谁接到谁第一时间处理 + 登记；原因是台账的"爆发形态"。
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import dicts
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.models.delivery import Contract
from app.models.enums import (
    ComplaintReason,
    ComplaintStatus,
    HandlerSide,
    IssueSource,
    IssueStatus,
    IssueType,
    UserRole,
)
from app.models.lead import Lead
from app.models.operation import Complaint, FaqItem, ProductIssue
from app.models.user import User
from app.schemas import (
    ComplaintClose,
    ComplaintCreate,
    ComplaintUpdate,
    FaqCreate,
    FaqUpdate,
    IssueAssign,
    IssueCreate,
    IssueReply,
    IssueResolve,
    IssueUpdate,
)
from app.services import audit_service

logger = logging.getLogger(__name__)

#: 默认处理时限（天）：产研修问题 / 销售兑现承诺
DEFAULT_DUE_DAYS = {HandlerSide.RND: 7, HandlerSide.SALES: 14}


# ══════════════ 产品问题与承诺台账（3.7）══════════════


async def list_issues(
    db: AsyncSession,
    *,
    contract_id: str | None = None,
    lead_id: str | None = None,
    type: str | None = None,  # noqa: A002 - 与 spec 字段名保持一致
    status: str | None = None,
    handler_side: str | None = None,
    assignee_id: str | None = None,
    is_renewal_risk: bool | None = None,
    keyword: str | None = None,
    owner_scope: str | None = None,
    offset: int = 0,
    limit: int = 20,
) -> list[ProductIssue]:
    stmt = select(ProductIssue)
    if contract_id:
        stmt = stmt.where(ProductIssue.contract_id == contract_id)
    if lead_id:
        stmt = stmt.where(ProductIssue.lead_id == lead_id)
    if type:
        stmt = stmt.where(ProductIssue.type == type)
    if status:
        stmt = stmt.where(ProductIssue.status == status)
    if handler_side:
        stmt = stmt.where(ProductIssue.handler_side == handler_side)
    if assignee_id:
        stmt = stmt.where(ProductIssue.assignee_id == assignee_id)
    if is_renewal_risk is not None:
        stmt = stmt.where(ProductIssue.is_renewal_risk.is_(is_renewal_risk))
    if keyword:
        kw = f"%{keyword.strip()}%"
        stmt = stmt.where(ProductIssue.content.like(kw) | ProductIssue.customer_name.like(kw))
    if owner_scope:
        stmt = stmt.where(
            ProductIssue.contract_id.in_(
                select(Contract.id).where(Contract.delivery_owner_id == owner_scope)
            )
        )
    return list(
        (
            await db.execute(
                stmt.order_by(ProductIssue.status, ProductIssue.registered_at.desc())
                .offset(offset)
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )


async def count_issues(db: AsyncSession, **filters) -> int:
    stmt = select(func.count()).select_from(ProductIssue)
    for key in ("contract_id", "lead_id", "type", "status", "handler_side", "assignee_id"):
        if filters.get(key):
            stmt = stmt.where(getattr(ProductIssue, key) == filters[key])
    return int((await db.execute(stmt)).scalar() or 0)


async def create_issue(db: AsyncSession, *, operator: User, payload: IssueCreate) -> ProductIssue:
    lead = (
        await db.execute(select(Lead).where(Lead.id == payload.lead_id, Lead.is_deleted.is_(False)))
    ).scalar_one_or_none()
    if lead is None:
        raise NotFoundError("客户不存在")

    if payload.type == IssueType.PROMISE and not payload.promised_date:
        raise ValidationError("销售承诺必须填写承诺兑现时间")

    contract_id = payload.contract_id
    if not contract_id:
        from app.services import delivery_service

        contract = await delivery_service.get_contract_by_lead(db, lead.id)
        contract_id = contract.id if contract else None

    side = HandlerSide.SALES if payload.type == IssueType.PROMISE else HandlerSide.RND
    row = ProductIssue(
        lead_id=lead.id,
        contract_id=contract_id,
        customer_name=lead.name,
        type=payload.type,
        content=payload.content,
        source=payload.source,
        proposer=payload.proposer,
        registrar_id=operator.id,
        registrar_name=operator.name,
        registered_at=datetime.now(),
        handler_side=side,
        due_date=payload.promised_date
        or (date.today() + timedelta(days=DEFAULT_DUE_DAYS.get(side, 7))),
        status=IssueStatus.PENDING,
        promised_date=payload.promised_date,
        is_renewal_risk=payload.is_renewal_risk,
        remark=payload.remark,
    )
    db.add(row)
    await db.flush()
    await audit_service.write_log(
        db,
        operator=operator,
        action="issue_create",
        target_type="lead",
        target_id=lead.id,
        target_name=lead.name,
        detail=(
            f"登记{dicts.label_of(dicts.ISSUE_TYPE_LABELS, payload.type)}：{payload.content[:60]}"
        ),
    )
    return row


async def get_issue(db: AsyncSession, issue_id: str) -> ProductIssue:
    row = (
        await db.execute(select(ProductIssue).where(ProductIssue.id == issue_id))
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError("台账记录不存在")
    return row


async def update_issue(
    db: AsyncSession, *, operator: User, row: ProductIssue, payload: IssueUpdate
) -> ProductIssue:
    for key, value in payload.model_dump(exclude_unset=True).items():
        if value is not None:
            setattr(row, key, value)
    return row


async def assign_issue(
    db: AsyncSession, *, operator: User, row: ProductIssue, payload: IssueAssign
) -> ProductIssue:
    """派单：派给产研（问题修复）或销售（承诺兑现）。"""
    if row.status in {IssueStatus.RESOLVED, IssueStatus.REPLIED}:
        raise ConflictError("已解决/已回复的记录不能再派单")

    row.handler_side = payload.handler_side
    if payload.assignee_id:
        assignee = (
            await db.execute(select(User).where(User.id == payload.assignee_id))
        ).scalar_one_or_none()
        if assignee is None:
            raise NotFoundError("处理人不存在")
        row.assignee_id = assignee.id
        row.assignee_name = assignee.name
        await audit_service.notify(
            db,
            user_id=assignee.id,
            title=f"新派单：{row.customer_name}",
            content=f"{dicts.label_of(dicts.ISSUE_TYPE_LABELS, row.type)}：{row.content[:80]}",
            type_="issue_assigned",
            event="issue_assigned",
            context={
                "lead_name": row.customer_name,
                "due_date": row.due_date.isoformat() if row.due_date else "未定",
                "content": row.content[:120],
            },
            biz_type="product_issue",
            biz_id=row.id,
        )
    if payload.due_date:
        row.due_date = payload.due_date
    elif not row.due_date:
        row.due_date = date.today() + timedelta(days=DEFAULT_DUE_DAYS.get(payload.handler_side, 7))
    if payload.remark:
        row.remark = payload.remark
    row.status = IssueStatus.PROCESSING

    await audit_service.write_log(
        db,
        operator=operator,
        action="issue_assign",
        target_type="product_issue",
        target_id=row.id,
        target_name=row.customer_name,
        detail=(
            f"派单给{dicts.label_of(dicts.HANDLER_SIDE_LABELS, payload.handler_side)}"
            f"（{row.assignee_name or '未指定'}），时限 {row.due_date}"
        ),
    )
    return row


async def resolve_issue(
    db: AsyncSession, *, operator: User, row: ProductIssue, payload: IssueResolve
) -> ProductIssue:
    """标记已解决（记解决时间，供"解决时长"统计）。"""
    if not payload.solution.strip():
        raise ValidationError("必须填写解决方案")
    row.status = IssueStatus.RESOLVED
    row.solution = payload.solution
    row.resolved_at = datetime.now()
    if payload.remark:
        row.remark = payload.remark
    await audit_service.write_log(
        db,
        operator=operator,
        action="issue_resolve",
        target_type="product_issue",
        target_id=row.id,
        target_name=row.customer_name,
        detail=f"已解决：{payload.solution[:60]}",
    )
    return row


async def reply_issue(
    db: AsyncSession, *, operator: User, row: ProductIssue, payload: IssueReply
) -> ProductIssue:
    """回复客户（C19 的关键一步：客户是否收到回复必须留痕）。"""
    if row.status == IssueStatus.PENDING:
        raise ConflictError("请先派单处理，再回复客户")
    row.status = IssueStatus.REPLIED
    row.reply_note = payload.reply_note
    row.reply_channel = payload.reply_channel
    row.reply_target = payload.reply_target
    row.reply_evidence = payload.reply_evidence
    row.replied_at = datetime.now()
    if payload.remark:
        row.remark = payload.remark
    if payload.is_renewal_risk is not None:
        row.is_renewal_risk = payload.is_renewal_risk

    await audit_service.write_log(
        db,
        operator=operator,
        action="issue_reply",
        target_type="product_issue",
        target_id=row.id,
        target_name=row.customer_name,
        detail=(
            f"已回复客户（{payload.reply_channel or '未填渠道'}／"
            f"{payload.reply_target or '未填对象'}）：{payload.reply_note[:60]}"
            f"；证据：{payload.reply_evidence[:60]}"
        ),
    )
    return row


async def open_issues_for_contract(db: AsyncSession, contract_id: str) -> list[ProductIssue]:
    return await list_issues(
        db,
        contract_id=contract_id,
        status=IssueStatus.PROCESSING,
        limit=100,
    )


async def issue_stats(
    db: AsyncSession, *, start: date | None = None, end: date | None = None
) -> dict:
    """台账统计：类型/状态/来源分布 + 解决时长 + 超期 + 影响续费 TOP。"""
    stmt = select(ProductIssue)
    if start:
        stmt = stmt.where(
            ProductIssue.registered_at >= datetime.combine(start, datetime.min.time())
        )
    if end:
        stmt = stmt.where(ProductIssue.registered_at <= datetime.combine(end, datetime.max.time()))
    rows = list((await db.execute(stmt)).scalars().all())

    def group(field: str, labels: dict) -> list[dict]:
        agg: dict[str, int] = {}
        for row in rows:
            value = getattr(row, field)
            agg[value] = agg.get(value, 0) + 1
        return sorted(
            [
                {"key": key, "label": dicts.label_of(labels, key), "count": count}
                for key, count in agg.items()
            ],
            key=lambda x: x["count"],
            reverse=True,
        )

    resolved = [r for r in rows if r.resolved_at]
    hours = [(r.resolved_at - r.registered_at).total_seconds() / 3600 for r in resolved]
    overdue = [
        r
        for r in rows
        if r.due_date
        and r.status in {IssueStatus.PENDING, IssueStatus.PROCESSING}
        and r.due_date < date.today()
    ]

    by_customer: dict[str, dict] = {}
    for row in rows:
        item = by_customer.setdefault(
            row.customer_name, {"customer_name": row.customer_name, "count": 0, "open": 0}
        )
        item["count"] += 1
        if row.status in {IssueStatus.PENDING, IssueStatus.PROCESSING}:
            item["open"] += 1

    top_customers = sorted(by_customer.values(), key=lambda x: x["count"], reverse=True)[:10]

    return {
        "start": start.isoformat() if start else None,
        "end": end.isoformat() if end else None,
        "total": len(rows),
        "open_count": sum(
            1 for r in rows if r.status in {IssueStatus.PENDING, IssueStatus.PROCESSING}
        ),
        "resolved_count": len(resolved),
        "replied_count": sum(1 for r in rows if r.replied_at),
        "renewal_risk_count": sum(1 for r in rows if r.is_renewal_risk),
        "overdue_count": len(overdue),
        "overdue_items": [issue_out(r) for r in overdue[:20]],
        "avg_resolve_hours": round(sum(hours) / len(hours), 1) if hours else 0.0,
        "by_type": group("type", dicts.ISSUE_TYPE_LABELS),
        "by_status": group("status", dicts.ISSUE_STATUS_LABELS),
        "by_source": group("source", dicts.ISSUE_SOURCE_LABELS),
        "by_handler_side": group("handler_side", dicts.HANDLER_SIDE_LABELS),
        "top_customers": top_customers,
    }


def issue_out(row: ProductIssue) -> dict:
    now = datetime.now()
    is_overdue = bool(
        row.due_date
        and row.status in {IssueStatus.PENDING, IssueStatus.PROCESSING}
        and row.due_date < now.date()
    )
    resolve_hours = (
        round((row.resolved_at - row.registered_at).total_seconds() / 3600, 1)
        if row.resolved_at
        else None
    )
    return {
        "id": row.id,
        "lead_id": row.lead_id,
        "contract_id": row.contract_id,
        "customer_name": row.customer_name,
        "type": row.type,
        "type_label": dicts.label_of(dicts.ISSUE_TYPE_LABELS, row.type),
        "content": row.content,
        "source": row.source,
        "source_label": dicts.label_of(dicts.ISSUE_SOURCE_LABELS, row.source),
        "proposer": row.proposer,
        "registrar_name": row.registrar_name,
        "registered_at": row.registered_at.isoformat() if row.registered_at else None,
        "handler_side": row.handler_side,
        "handler_side_label": dicts.label_of(dicts.HANDLER_SIDE_LABELS, row.handler_side),
        "assignee_id": row.assignee_id,
        "assignee_name": row.assignee_name,
        "due_date": row.due_date.isoformat() if row.due_date else None,
        "status": row.status,
        "status_label": dicts.label_of(dicts.ISSUE_STATUS_LABELS, row.status),
        "promised_date": row.promised_date.isoformat() if row.promised_date else None,
        "resolved_at": row.resolved_at.isoformat() if row.resolved_at else None,
        "replied_at": row.replied_at.isoformat() if row.replied_at else None,
        "resolve_hours": resolve_hours,
        "is_overdue": is_overdue,
        "is_renewal_risk": row.is_renewal_risk,
        "solution": row.solution,
        "reply_note": row.reply_note,
        "reply_channel": row.reply_channel,
        "reply_target": row.reply_target,
        "reply_evidence": row.reply_evidence,
        "remark": row.remark,
    }


# ══════════════ 投诉管理（3.8）══════════════


async def list_complaints(
    db: AsyncSession,
    *,
    contract_id: str | None = None,
    lead_id: str | None = None,
    status: str | None = None,
    reason: str | None = None,
    owner_scope: str | None = None,
    offset: int = 0,
    limit: int = 20,
) -> list[Complaint]:
    stmt = select(Complaint)
    if contract_id:
        stmt = stmt.where(Complaint.contract_id == contract_id)
    if lead_id:
        stmt = stmt.where(Complaint.lead_id == lead_id)
    if status:
        stmt = stmt.where(Complaint.status == status)
    if reason:
        stmt = stmt.where(Complaint.reason == reason)
    if owner_scope:
        stmt = stmt.where(
            Complaint.contract_id.in_(
                select(Contract.id).where(Contract.delivery_owner_id == owner_scope)
            )
        )
    return list(
        (
            await db.execute(
                stmt.order_by(Complaint.status, Complaint.received_at.desc())
                .offset(offset)
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )


async def count_complaints(db: AsyncSession, **filters) -> int:
    stmt = select(func.count()).select_from(Complaint)
    for key in ("contract_id", "lead_id", "status", "reason"):
        if filters.get(key):
            stmt = stmt.where(getattr(Complaint, key) == filters[key])
    return int((await db.execute(stmt)).scalar() or 0)


async def create_complaint(
    db: AsyncSession, *, operator: User, payload: ComplaintCreate
) -> Complaint:
    lead = (
        await db.execute(select(Lead).where(Lead.id == payload.lead_id, Lead.is_deleted.is_(False)))
    ).scalar_one_or_none()
    if lead is None:
        raise NotFoundError("客户不存在")

    contract_id = payload.contract_id
    if not contract_id:
        from app.services import delivery_service

        contract = await delivery_service.get_contract_by_lead(db, lead.id)
        contract_id = contract.id if contract else None

    handler_id = payload.handler_id or operator.id
    handler = (await db.execute(select(User).where(User.id == handler_id))).scalar_one_or_none()

    row = Complaint(
        lead_id=lead.id,
        contract_id=contract_id,
        customer_name=lead.name,
        complainant=payload.complainant,
        receiver_id=handler_id,
        receiver_name=(handler.name if handler else operator.name),
        received_at=payload.received_at or datetime.now(),
        content=payload.content,
        reason=payload.reason,
        status=ComplaintStatus.PENDING,
    )
    db.add(row)
    await db.flush()

    # 投诉是台账的"爆发形态"：产品问题/承诺没兑现自动进台账（C19 统一收口）
    if payload.reason in {ComplaintReason.PRODUCT, ComplaintReason.COMMITMENT}:
        issue_type = (
            IssueType.PROMISE
            if payload.reason == ComplaintReason.COMMITMENT
            else IssueType.PRODUCT_ISSUE
        )
        side = HandlerSide.SALES if issue_type == IssueType.PROMISE else HandlerSide.RND
        db.add(
            ProductIssue(
                lead_id=lead.id,
                contract_id=contract_id,
                customer_name=lead.name,
                type=issue_type,
                content=f"（来自投诉）{payload.content}",
                source=IssueSource.COMPLAINT,
                proposer=payload.complainant,
                registrar_id=operator.id,
                registrar_name=operator.name,
                registered_at=datetime.now(),
                handler_side=side,
                due_date=date.today() + timedelta(days=DEFAULT_DUE_DAYS.get(side, 7)),
                status=IssueStatus.PENDING,
                is_renewal_risk=True,
            )
        )

    await audit_service.write_log(
        db,
        operator=operator,
        action="complaint_create",
        target_type="lead",
        target_id=lead.id,
        target_name=lead.name,
        detail=f"登记投诉（{dicts.label_of(dicts.COMPLAINT_REASON_LABELS, payload.reason)}）："
        f"{payload.content[:60]}",
    )
    await audit_service.notify_many(
        db,
        user_ids=await audit_service.manager_ids(db),
        title=f"客户投诉：{lead.name}",
        content=payload.content[:120],
        type_="complaint",
        event="complaint",
        context={
            "lead_name": lead.name,
            "content": payload.content[:120],
            "receiver": operator.name,
        },
        biz_type="complaint",
        biz_id=row.id,
    )
    return row


async def get_complaint(db: AsyncSession, complaint_id: str) -> Complaint:
    row = (
        await db.execute(select(Complaint).where(Complaint.id == complaint_id))
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError("投诉记录不存在")
    return row


async def update_complaint(
    db: AsyncSession, *, operator: User, row: Complaint, payload: ComplaintUpdate
) -> Complaint:
    for key, value in payload.model_dump(exclude_unset=True).items():
        if value is not None:
            setattr(row, key, value)
    if row.status == ComplaintStatus.PENDING:
        row.status = ComplaintStatus.PROCESSING
    return row


async def close_complaint(
    db: AsyncSession, *, operator: User, row: Complaint, payload: ComplaintClose
) -> Complaint:
    if row.status == ComplaintStatus.CLOSED:
        raise ConflictError("该投诉已闭环")
    if not payload.handle_result.strip():
        raise ValidationError("必须填写处理结果")
    row.status = ComplaintStatus.CLOSED
    row.handle_result = payload.handle_result
    row.closed_at = datetime.now()
    if payload.remark:
        row.remark = payload.remark
    await audit_service.write_log(
        db,
        operator=operator,
        action="complaint_close",
        target_type="complaint",
        target_id=row.id,
        target_name=row.customer_name,
        detail=f"投诉闭环：{payload.handle_result[:60]}",
    )
    return row


async def escalate_complaint(
    db: AsyncSession, *, operator: User, row: Complaint, remark: str | None = None
) -> Complaint:
    """投诉升级：拉市场/产研专项会（先把客户完整记录汇总给管理层）。"""
    row.escalated = True
    if row.status == ComplaintStatus.PENDING:
        row.status = ComplaintStatus.PROCESSING

    targets = await audit_service.manager_ids(db)
    rnd_ids = list(
        (
            await db.execute(
                select(User.id).where(User.role == UserRole.RND, User.is_active.is_(True))
            )
        )
        .scalars()
        .all()
    )
    await audit_service.notify_many(
        db,
        user_ids=[*targets, *rnd_ids],
        title=f"投诉升级：{row.customer_name}",
        content=f"{row.content[:100]}｜{remark or ''}",
        type_="complaint_escalated",
        biz_type="complaint",
        biz_id=row.id,
        event="complaint_escalated",
        context={
            "lead_name": row.customer_name,
            "content": row.content[:120],
        },
    )
    await audit_service.write_log(
        db,
        operator=operator,
        action="complaint_escalate",
        target_type="complaint",
        target_id=row.id,
        target_name=row.customer_name,
        detail=f"投诉升级，拉市场/产研专项会；{remark or ''}",
    )
    return row


async def complaint_stats(db: AsyncSession) -> dict:
    rows = list((await db.execute(select(Complaint))).scalars().all())
    by_reason: dict[str, int] = {}
    for row in rows:
        by_reason[row.reason] = by_reason.get(row.reason, 0) + 1
    return {
        "total": len(rows),
        "open_count": sum(
            1 for r in rows if r.status in {ComplaintStatus.PENDING, ComplaintStatus.PROCESSING}
        ),
        "closed_count": sum(1 for r in rows if r.status == ComplaintStatus.CLOSED),
        "escalated_count": sum(1 for r in rows if r.escalated),
        "by_reason": [
            {
                "key": reason.value,
                "label": dicts.label_of(dicts.COMPLAINT_REASON_LABELS, reason.value),
                "count": by_reason.get(reason.value, 0),
                "rate": round(by_reason.get(reason.value, 0) / len(rows), 4) if rows else 0.0,
            }
            for reason in ComplaintReason
        ],
    }


def complaint_out(row: Complaint) -> dict:
    now = datetime.now()
    handle_hours = (
        round((row.closed_at - row.received_at).total_seconds() / 3600, 1)
        if row.closed_at
        else None
    )
    return {
        "id": row.id,
        "lead_id": row.lead_id,
        "contract_id": row.contract_id,
        "customer_name": row.customer_name,
        "complainant": row.complainant,
        "receiver_id": row.receiver_id,
        "receiver_name": row.receiver_name,
        "received_at": row.received_at.isoformat() if row.received_at else None,
        "content": row.content,
        "reason": row.reason,
        "reason_label": dicts.label_of(dicts.COMPLAINT_REASON_LABELS, row.reason),
        "status": row.status,
        "status_label": dicts.label_of(dicts.COMPLAINT_STATUS_LABELS, row.status),
        "handle_result": row.handle_result,
        "closed_at": row.closed_at.isoformat() if row.closed_at else None,
        "handle_hours": handle_hours,
        "is_overdue": bool(
            row.status in {ComplaintStatus.PENDING, ComplaintStatus.PROCESSING}
            and (now - row.received_at).days >= 3
        ),
        "escalated": row.escalated,
        "remark": row.remark,
    }


# ══════════════ FAQ（3.6 服务支持）══════════════


async def list_faqs(
    db: AsyncSession, *, category: str | None = None, keyword: str | None = None, limit: int = 100
) -> list[FaqItem]:
    stmt = select(FaqItem)
    if category:
        stmt = stmt.where(FaqItem.category == category)
    if keyword:
        kw = f"%{keyword.strip()}%"
        stmt = stmt.where(FaqItem.question.like(kw) | FaqItem.answer.like(kw))
    return list(
        (
            await db.execute(
                stmt.order_by(FaqItem.view_count.desc(), FaqItem.created_at.desc()).limit(limit)
            )
        )
        .scalars()
        .all()
    )


async def create_faq(db: AsyncSession, *, operator: User, payload: FaqCreate) -> FaqItem:
    row = FaqItem(
        question=payload.question,
        answer=payload.answer,
        category=payload.category,
        is_standard=payload.is_standard,
        tags=payload.tags or [],
        updated_by_name=operator.name,
    )
    db.add(row)
    await db.flush()
    return row


async def get_faq(db: AsyncSession, faq_id: str) -> FaqItem:
    row = (await db.execute(select(FaqItem).where(FaqItem.id == faq_id))).scalar_one_or_none()
    if row is None:
        raise NotFoundError("FAQ 不存在")
    return row


async def update_faq(
    db: AsyncSession, *, operator: User, row: FaqItem, payload: FaqUpdate
) -> FaqItem:
    for key, value in payload.model_dump(exclude_unset=True).items():
        if value is not None:
            setattr(row, key, value)
    row.updated_by_name = operator.name
    return row


def faq_out(row: FaqItem) -> dict:
    return {
        "id": row.id,
        "question": row.question,
        "answer": row.answer,
        "category": row.category,
        "is_standard": row.is_standard,
        "tags": row.tags or [],
        "view_count": row.view_count,
        "updated_by_name": row.updated_by_name,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }
