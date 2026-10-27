"""闭环三 · 验收与续费服务（P2-c）+ 运营复盘（页面 4.6）。

口径（spec/04 §7/§8）：
- **验收**：到期前 **15 个工作日**提交验收申请 → 验收会 + 现场核查 + 功能测试
  → 客户签《交付验收报告确认书》
- **续费**：到期前 **90 天**启动评估（结合验收结果 + 活跃度 + 满意度）→ 分档方案 → 向核心负责人汇报
-  已确认：**续费机会不进系统管理，先只看续费率**（所以本模块只做到期清单 + 轻量评估 + 续费率）
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import dicts
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.models.delivery import AcceptanceRecord, Contract, RenewalAssessment
from app.models.enums import (
    AcceptanceStatus,
    ContractStatus,
    IssueStatus,
    RenewalStatus,
)
from app.models.operation import OperationMetric, ProductIssue
from app.models.user import User
from app.schemas import (
    AcceptanceCreate,
    AcceptancePass,
    AcceptanceUpdate,
    RenewalConclude,
    RenewalCreate,
    RenewalUpdate,
)
from app.services import audit_service, calendar_service, delivery_stage_service

logger = logging.getLogger(__name__)

#: 行业续费基准（SaaS 85%），用于复盘对比
RENEWAL_BASELINE = 0.85


# ══════════════ 验收管理（3.11）══════════════


async def list_acceptances(
    db: AsyncSession,
    *,
    contract_id: str | None = None,
    status: str | None = None,
    owner_scope: str | None = None,
    offset: int = 0,
    limit: int = 20,
) -> list[AcceptanceRecord]:
    stmt = select(AcceptanceRecord)
    if contract_id:
        stmt = stmt.where(AcceptanceRecord.contract_id == contract_id)
    if status:
        stmt = stmt.where(AcceptanceRecord.status == status)
    if owner_scope:
        stmt = stmt.where(
            AcceptanceRecord.contract_id.in_(
                select(Contract.id).where(Contract.delivery_owner_id == owner_scope)
            )
        )
    return list(
        (
            await db.execute(
                stmt.order_by(AcceptanceRecord.created_at.desc()).offset(offset).limit(limit)
            )
        )
        .scalars()
        .all()
    )


async def count_acceptances(db: AsyncSession, **filters) -> int:
    stmt = select(func.count()).select_from(AcceptanceRecord)
    for key in ("contract_id", "status"):
        if filters.get(key):
            stmt = stmt.where(getattr(AcceptanceRecord, key) == filters[key])
    return int((await db.execute(stmt)).scalar() or 0)


async def ensure_acceptance(db: AsyncSession, contract: Contract) -> AcceptanceRecord:
    """为合同建/取验收记录（幂等）。"""
    row = (
        (
            await db.execute(
                select(AcceptanceRecord)
                .where(AcceptanceRecord.contract_id == contract.id)
                .order_by(AcceptanceRecord.created_at.desc())
            )
        )
        .scalars()
        .first()
    )
    if row is not None:
        return row
    row = AcceptanceRecord(
        contract_id=contract.id,
        lead_id=contract.lead_id,
        customer_name=contract.customer_name,
        status=AcceptanceStatus.PENDING,
    )
    db.add(row)
    await db.flush()
    return row


async def create_acceptance(
    db: AsyncSession, *, operator: User, payload: AcceptanceCreate
) -> AcceptanceRecord:
    contract = (
        await db.execute(select(Contract).where(Contract.id == payload.contract_id))
    ).scalar_one_or_none()
    if contract is None:
        raise NotFoundError("合同不存在")

    row = await ensure_acceptance(db, contract)
    for key, value in payload.model_dump(exclude_unset=True, exclude={"contract_id"}).items():
        if value is not None:
            setattr(row, key, value)
    if row.apply_date:
        row.status = AcceptanceStatus.APPLIED
    await audit_service.write_log(
        db,
        operator=operator,
        action="acceptance_save",
        target_type="contract",
        target_id=contract.id,
        target_name=contract.customer_name,
        detail=f"验收登记（状态 {row.status}）",
    )
    return row


async def get_acceptance(db: AsyncSession, acceptance_id: str) -> AcceptanceRecord:
    row = (
        await db.execute(select(AcceptanceRecord).where(AcceptanceRecord.id == acceptance_id))
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError("验收记录不存在")
    return row


async def update_acceptance(
    db: AsyncSession, *, operator: User, row: AcceptanceRecord, payload: AcceptanceUpdate
) -> AcceptanceRecord:
    for key, value in payload.model_dump(exclude_unset=True).items():
        if value is not None:
            setattr(row, key, value)
    return row


async def submit_acceptance(
    db: AsyncSession, *, operator: User, row: AcceptanceRecord, remark: str | None = None
) -> AcceptanceRecord:
    """提交验收申请（SOP：到期前 15 个工作日）。"""
    if row.status not in {AcceptanceStatus.PENDING, AcceptanceStatus.FAILED}:
        raise ConflictError("当前状态不能提交验收申请")
    row.status = AcceptanceStatus.APPLIED
    row.apply_date = date.today()
    if remark:
        row.remark = remark
    await audit_service.write_log(
        db,
        operator=operator,
        action="acceptance_submit",
        target_type="contract",
        target_id=row.contract_id,
        target_name=row.customer_name,
        detail="提交验收申请",
    )
    return row


async def pass_acceptance(
    db: AsyncSession, *, operator: User, row: AcceptanceRecord, payload: AcceptancePass
) -> AcceptanceRecord:
    """验收通过：客户签署《交付验收报告确认书》→ 合同状态转已验收。"""
    if row.status not in {AcceptanceStatus.APPLIED, AcceptanceStatus.MEETING}:
        raise ConflictError("请先提交验收申请")
    if not payload.signed_date:
        raise ValidationError("必须填写客户签署确认书的日期")

    row.status = AcceptanceStatus.PASSED
    row.result = "pass"
    row.meeting_date = payload.meeting_date or row.meeting_date
    row.signed_date = payload.signed_date
    if payload.attachment is not None:
        row.attachment = payload.attachment
    if payload.remark:
        row.remark = payload.remark

    contract = (
        await db.execute(select(Contract).where(Contract.id == row.contract_id))
    ).scalar_one_or_none()
    if contract is not None and contract.status == ContractStatus.EXECUTING:
        contract.status = ContractStatus.ACCEPTED

    await audit_service.write_log(
        db,
        operator=operator,
        action="acceptance_pass",
        target_type="contract",
        target_id=row.contract_id,
        target_name=row.customer_name,
        detail=f"验收通过，客户签署确认书（{row.signed_date}）",
    )
    return row


async def fail_acceptance(
    db: AsyncSession, *, operator: User, row: AcceptanceRecord, rectify_note: str
) -> AcceptanceRecord:
    if not rectify_note.strip():
        raise ValidationError("未通过必须填写整改说明")
    row.status = AcceptanceStatus.FAILED
    row.result = "fail"
    row.rectify_note = rectify_note
    await audit_service.write_log(
        db,
        operator=operator,
        action="acceptance_fail",
        target_type="contract",
        target_id=row.contract_id,
        target_name=row.customer_name,
        detail=f"验收未通过：{rectify_note[:60]}",
    )
    return row


async def acceptance_due_list(db: AsyncSession, *, workdays: int | None = None) -> list[dict]:
    """验收临近清单：服务期到期前 N 个工作日（默认取配置，SOP 为 15）。"""
    from app.core.workday import count_working_days
    from app.services import risk_service

    workdays = workdays or int(await risk_service.get_rule(db, "acceptance_due_workdays", 15) or 15)
    today = date.today()
    holidays, makeups = await calendar_service.load_calendar(db)

    contracts = list(
        (
            await db.execute(
                select(Contract).where(
                    Contract.status.in_([ContractStatus.EXECUTING, ContractStatus.ACCEPTED]),
                    Contract.service_end.is_not(None),
                )
            )
        )
        .scalars()
        .all()
    )

    result: list[dict] = []
    for contract in contracts:
        # 剩余工作日：已过期返回 0（仍要暴露"到期未验收"）
        workdays_left = count_working_days(
            datetime.combine(today, time.min),
            datetime.combine(contract.service_end, time.max),
            holidays,
            makeups,
        )
        if contract.service_end < today:
            workdays_left = 0
        if workdays_left > workdays:
            continue

        acceptance = (
            (
                await db.execute(
                    select(AcceptanceRecord)
                    .where(AcceptanceRecord.contract_id == contract.id)
                    .order_by(AcceptanceRecord.created_at.desc())
                )
            )
            .scalars()
            .first()
        )
        if acceptance is not None and acceptance.status == AcceptanceStatus.PASSED:
            continue

        status = acceptance.status if acceptance else AcceptanceStatus.PENDING
        result.append(
            {
                "contract_id": contract.id,
                "contract_no": contract.contract_no,
                "customer_name": contract.customer_name,
                "delivery_owner_name": contract.delivery_owner_name,
                "service_end": contract.service_end.isoformat(),
                "days_left": (contract.service_end - today).days,
                "workdays_left": workdays_left,
                "status": status,
                "status_label": dicts.label_of(dicts.ACCEPTANCE_STATUS_LABELS, status),
                "apply_date": acceptance.apply_date.isoformat()
                if acceptance and acceptance.apply_date
                else None,
                "acceptance_id": acceptance.id if acceptance else None,
                "urgent": workdays_left <= 5,
            }
        )
    result.sort(key=lambda x: x["workdays_left"])
    return result


def acceptance_out(row: AcceptanceRecord, contract: Contract | None = None) -> dict:
    service_end = contract.service_end if contract else None
    return {
        "id": row.id,
        "contract_id": row.contract_id,
        "contract_no": contract.contract_no if contract else None,
        "customer_name": row.customer_name,
        "status": row.status,
        "status_label": dicts.label_of(dicts.ACCEPTANCE_STATUS_LABELS, row.status),
        "apply_date": row.apply_date.isoformat() if row.apply_date else None,
        "meeting_date": row.meeting_date.isoformat() if row.meeting_date else None,
        "result": row.result,
        "signed_date": row.signed_date.isoformat() if row.signed_date else None,
        "attachment": row.attachment,
        "rectify_note": row.rectify_note,
        "service_end": service_end.isoformat() if service_end else None,
        "days_to_expire": (service_end - date.today()).days if service_end else None,
        "remark": row.remark,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


# ══════════════ 续费与增购（3.12）══════════════


async def list_renewals(
    db: AsyncSession,
    *,
    contract_id: str | None = None,
    status: str | None = None,
    owner_scope: str | None = None,
    offset: int = 0,
    limit: int = 20,
) -> list[RenewalAssessment]:
    stmt = select(RenewalAssessment)
    if contract_id:
        stmt = stmt.where(RenewalAssessment.contract_id == contract_id)
    if status:
        stmt = stmt.where(RenewalAssessment.status == status)
    if owner_scope:
        stmt = stmt.where(
            RenewalAssessment.contract_id.in_(
                select(Contract.id).where(Contract.delivery_owner_id == owner_scope)
            )
        )
    return list(
        (
            await db.execute(
                stmt.order_by(
                    RenewalAssessment.service_end.is_(None), RenewalAssessment.service_end
                )
                .offset(offset)
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )


async def count_renewals(db: AsyncSession, **filters) -> int:
    stmt = select(func.count()).select_from(RenewalAssessment)
    for key in ("contract_id", "status"):
        if filters.get(key):
            stmt = stmt.where(getattr(RenewalAssessment, key) == filters[key])
    return int((await db.execute(stmt)).scalar() or 0)


async def get_renewal(db: AsyncSession, renewal_id: str) -> RenewalAssessment:
    row = (
        await db.execute(select(RenewalAssessment).where(RenewalAssessment.id == renewal_id))
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError("续费评估记录不存在")
    return row


async def create_renewal(
    db: AsyncSession, *, operator: User, payload: RenewalCreate
) -> RenewalAssessment:
    contract = (
        await db.execute(select(Contract).where(Contract.id == payload.contract_id))
    ).scalar_one_or_none()
    if contract is None:
        raise NotFoundError("合同不存在")

    row = RenewalAssessment(
        contract_id=contract.id,
        lead_id=contract.lead_id,
        customer_name=contract.customer_name,
        owner_name=contract.delivery_owner_name,
        service_end=contract.service_end,
        assess_date=payload.assess_date or date.today(),
        activity_note=payload.activity_note,
        satisfaction=payload.satisfaction,
        acceptance_result=payload.acceptance_result,
        willingness=payload.willingness,
        plan_note=payload.plan_note,
        conclusion=payload.conclusion,
        expected_amount_fen=payload.expected_amount_fen,
        status=payload.status,
        assessed_by=operator.id,
        assessed_by_name=operator.name,
    )
    db.add(row)
    await db.flush()
    await audit_service.write_log(
        db,
        operator=operator,
        action="renewal_create",
        target_type="contract",
        target_id=contract.id,
        target_name=contract.customer_name,
        detail=(
            "续费评估（意愿 "
            f"{dicts.label_of(dicts.RENEWAL_WILLINGNESS_LABELS, payload.willingness)}）"
        ),
    )
    return row


async def update_renewal(
    db: AsyncSession, *, operator: User, row: RenewalAssessment, payload: RenewalUpdate
) -> RenewalAssessment:
    for key, value in payload.model_dump(exclude_unset=True).items():
        if value is not None:
            setattr(row, key, value)
    row.assessed_by = operator.id
    row.assessed_by_name = operator.name
    return row


async def conclude_renewal(
    db: AsyncSession, *, operator: User, row: RenewalAssessment, payload: RenewalConclude
) -> RenewalAssessment:
    """闭环：已续费 / 已流失 / 推进中。"""
    row.status = payload.status
    if payload.conclusion:
        row.conclusion = payload.conclusion
    if payload.expected_amount_fen is not None:
        row.expected_amount_fen = payload.expected_amount_fen
    row.assessed_by = operator.id
    row.assessed_by_name = operator.name

    contract = (
        await db.execute(select(Contract).where(Contract.id == row.contract_id))
    ).scalar_one_or_none()
    if contract is not None and payload.status == RenewalStatus.RENEWED:
        contract.status = ContractStatus.RENEWED

    await audit_service.write_log(
        db,
        operator=operator,
        action="renewal_conclude",
        target_type="contract",
        target_id=row.contract_id,
        target_name=row.customer_name,
        detail=f"续费结论：{dicts.label_of(dicts.RENEWAL_STATUS_LABELS, payload.status)}；"
        f"{payload.conclusion or ''}",
    )
    return row


async def renewal_due_list(db: AsyncSession, *, days: int | None = None) -> list[dict]:
    """到期前 N 天（默认 90）续费清单：把评估所需的事实摆在一起（活跃度/验收结果/意愿）。"""
    from app.services import risk_service

    days = days or int(await risk_service.get_rule(db, "renewal_due_days", 90) or 90)
    today = date.today()
    contracts = list(
        (
            await db.execute(
                select(Contract).where(
                    Contract.status.in_([ContractStatus.EXECUTING, ContractStatus.ACCEPTED]),
                    Contract.service_end.is_not(None),
                )
            )
        )
        .scalars()
        .all()
    )

    result: list[dict] = []
    for contract in contracts:
        days_left = (contract.service_end - today).days
        if days_left > days:
            continue

        latest_metric = (
            (
                await db.execute(
                    select(OperationMetric)
                    .where(OperationMetric.contract_id == contract.id)
                    .order_by(OperationMetric.period.desc())
                )
            )
            .scalars()
            .first()
        )
        acceptance = (
            (
                await db.execute(
                    select(AcceptanceRecord)
                    .where(AcceptanceRecord.contract_id == contract.id)
                    .order_by(AcceptanceRecord.created_at.desc())
                )
            )
            .scalars()
            .first()
        )
        open_issues = int(
            (
                await db.execute(
                    select(func.count())
                    .select_from(ProductIssue)
                    .where(
                        ProductIssue.contract_id == contract.id,
                        ProductIssue.status.in_([IssueStatus.PENDING, IssueStatus.PROCESSING]),
                    )
                )
            ).scalar()
            or 0
        )
        renewal = (
            (
                await db.execute(
                    select(RenewalAssessment)
                    .where(RenewalAssessment.contract_id == contract.id)
                    .order_by(RenewalAssessment.created_at.desc())
                )
            )
            .scalars()
            .first()
        )

        result.append(
            {
                "contract_id": contract.id,
                "contract_no": contract.contract_no,
                "customer_name": contract.customer_name,
                "owner_name": contract.delivery_owner_name,
                "service_end": contract.service_end.isoformat(),
                "days_left": days_left,
                "amount_fen": contract.amount_fen,
                "activity_rate": latest_metric.activity_rate if latest_metric else None,
                "acceptance_status": acceptance.status if acceptance else AcceptanceStatus.PENDING,
                "acceptance_status_label": dicts.label_of(
                    dicts.ACCEPTANCE_STATUS_LABELS,
                    acceptance.status if acceptance else AcceptanceStatus.PENDING,
                ),
                "open_issue_count": open_issues,
                "risk_count": open_issues,
                "willingness": renewal.willingness if renewal else None,
                "willingness_label": dicts.label_of(
                    dicts.RENEWAL_WILLINGNESS_LABELS, renewal.willingness
                )
                if renewal
                else None,
                "status": renewal.status if renewal else RenewalStatus.PENDING,
                "status_label": dicts.label_of(
                    dicts.RENEWAL_STATUS_LABELS,
                    renewal.status if renewal else RenewalStatus.PENDING,
                ),
                "renewal_id": renewal.id if renewal else None,
                "expected_amount_fen": renewal.expected_amount_fen if renewal else 0,
                "urgent": days_left <= 30,
            }
        )
    result.sort(key=lambda x: x["days_left"])
    return result


async def renewal_stats(db: AsyncSession) -> dict:
    """续费率统计：本期只看到期与续费结果（不做商机管道）。"""
    rows = list((await db.execute(select(RenewalAssessment))).scalars().all())
    total = len(rows)
    renewed = sum(1 for r in rows if r.status == RenewalStatus.RENEWED)
    lost = sum(1 for r in rows if r.status == RenewalStatus.LOST)
    decided = renewed + lost
    return {
        "assessed_count": total,
        "renewed_count": renewed,
        "lost_count": lost,
        "in_progress_count": sum(1 for r in rows if r.status == RenewalStatus.IN_PROGRESS),
        "pending_count": sum(1 for r in rows if r.status == RenewalStatus.PENDING),
        "renew_rate": round(renewed / decided, 4) if decided else 0.0,
        "baseline": RENEWAL_BASELINE,
        "gap": round((renewed / decided if decided else 0.0) - RENEWAL_BASELINE, 4),
        "expected_amount_fen": sum(r.expected_amount_fen for r in rows),
    }


def renewal_out(row: RenewalAssessment) -> dict:
    return {
        "id": row.id,
        "contract_id": row.contract_id,
        "lead_id": row.lead_id,
        "customer_name": row.customer_name,
        "owner_name": row.owner_name,
        "service_end": row.service_end.isoformat() if row.service_end else None,
        "days_to_expire": (row.service_end - date.today()).days if row.service_end else None,
        "assess_date": row.assess_date.isoformat() if row.assess_date else None,
        "activity_note": row.activity_note,
        "satisfaction": row.satisfaction,
        "acceptance_result": row.acceptance_result,
        "willingness": row.willingness,
        "willingness_label": dicts.label_of(dicts.RENEWAL_WILLINGNESS_LABELS, row.willingness),
        "plan_note": row.plan_note,
        "conclusion": row.conclusion,
        "expected_amount_fen": row.expected_amount_fen,
        "status": row.status,
        "status_label": dicts.label_of(dicts.RENEWAL_STATUS_LABELS, row.status),
        "assessed_by_name": row.assessed_by_name,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


# ══════════════ 运营复盘（4.6）══════════════


async def operation_review(db: AsyncSession, *, period: str | None = None) -> dict:
    """运营复盘：续费率 + 流失原因 + 投诉原因 + 产品问题统计 + 交付延期。"""
    from app.services import issue_service, operation_service

    period = period or date.today().strftime("%Y-%m")

    renewal = await renewal_stats(db)
    complaints = await issue_service.complaint_stats(db)
    issues = await issue_service.issue_stats(db)

    # 流失/丢单原因（闭环二丢单 + 续费流失）
    from app.models.enums import LeadStatus, RiskType
    from app.models.lead import Lead

    lost_leads = list(
        (
            await db.execute(
                select(Lead).where(Lead.status == LeadStatus.LOST, Lead.is_deleted.is_(False))
            )
        )
        .scalars()
        .all()
    )
    churn: dict[str, int] = {}
    for lead in lost_leads:
        for reason in lead.lost_reason_types or []:
            churn[reason] = churn.get(reason, 0) + 1
    churn_reasons = [
        {
            "key": risk.value,
            "label": dicts.label_of(dicts.RISK_TYPE_LABELS, risk.value),
            "count": churn.get(risk.value, 0),
            "rate": round(churn.get(risk.value, 0) / len(lost_leads), 4) if lost_leads else 0.0,
        }
        for risk in RiskType
    ]
    churn_reasons.sort(key=lambda x: x["count"], reverse=True)

    # 交付延期（阶段停留超期）—— 基准未设时为空
    delivery_delay: list[dict] = []
    contracts = list(
        (await db.execute(select(Contract).where(Contract.status == ContractStatus.EXECUTING)))
        .scalars()
        .all()
    )
    for contract in contracts:
        info = await delivery_stage_service.contract_stage_info(db, contract)
        if info["overdue"]:
            delivery_delay.append(
                {
                    "contract_id": contract.id,
                    "customer_name": contract.customer_name,
                    "phase": contract.current_phase,
                    "phase_label": dicts.DELIVERY_PHASE_LABELS.get(contract.current_phase, ""),
                    "stay_days": info["stay_days"],
                    "base_days": info["base_days"],
                }
            )

    low_activity = await operation_service.low_activity_customers(db, period_type="month")
    latest_metrics = await operation_service.list_metrics(db, period_type="month", limit=200)
    total_accounts = sum(m.total_accounts for m in latest_metrics)
    total_login = sum(m.login_count for m in latest_metrics)

    return {
        "period": period,
        "renewal": renewal,
        "churn_reasons": churn_reasons,
        "lost_lead_count": len(lost_leads),
        "complaints": complaints,
        "product_issues": {
            key: issues[key]
            for key in (
                "total",
                "open_count",
                "resolved_count",
                "replied_count",
                "renewal_risk_count",
                "overdue_count",
                "avg_resolve_hours",
                "by_type",
                "by_source",
                "by_handler_side",
                "top_customers",
                "overdue_items",
            )
        },
        "delivery_delay": delivery_delay,
        "delivery_delay_note": (
            "阶段停留时长基准本期未设，配好基准后（6.6）本清单自动生效"
            if not delivery_delay
            else ""
        ),
        "activity": {
            "customer_count": len(latest_metrics),
            "avg_activity_rate": operation_service.calc_activity_rate(total_login, total_accounts),
            "low_activity": low_activity,
            "note": "活跃率暂不设阈值预警，仅展示趋势与排名（已确认）",
        },
    }
