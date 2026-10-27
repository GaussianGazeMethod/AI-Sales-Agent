"""分配服务：指派 / 改派 / 回收 / 公海领取 / 负载视图，全程留痕（S1-3、S1-5）。

SLA 规则（已确认）：指派后启动 24 小时（1 个工作日）首次跟进计时，
7 个工作日未跟进自动回收。进入闭环二后不再自动回收。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core import dicts
from app.core.errors import ConflictError, NotFoundError
from app.models.activity import Assignment, Reminder
from app.models.enums import AssignmentAction, LeadStatus, ReminderStatus, UserRole
from app.models.lead import Lead
from app.models.user import User
from app.schemas import AssignRequest, RecycleRequest
from app.services import audit_service, calendar_service

settings = get_settings()


async def setup_owner_sla(
    db: AsyncSession, lead: Lead, owner_id: str, *, operator: User, remark: str | None = None
) -> None:
    """设置归属人并启动 SLA 计时（指派/改派/复活 共用）。"""
    now = datetime.now()
    lead.owner_id = owner_id
    owner = (await db.execute(select(User).where(User.id == owner_id))).scalar_one_or_none()
    lead.owner_name = owner.name if owner else None
    lead.assign_at = now
    lead.status = LeadStatus.ASSIGNED
    lead.first_follow_deadline = await calendar_service.deadline_after_working_days(
        db, now, settings.LEAD_FIRST_FOLLOW_WORKING_DAYS
    )
    lead.recycle_deadline = await calendar_service.deadline_after_working_days(
        db, now, settings.LEAD_RECYCLE_WORKING_DAYS
    )

    await audit_service.notify(
        db,
        user_id=owner_id,
        title="新线索已指派给你",
        content=f"{lead.name}｜请在 1 个工作日内完成首次跟进",
        type_="lead_assigned",
        biz_type="lead",
        biz_id=lead.id,
        event="lead_assigned",
        context={
            "lead_name": lead.name,
            "source": dicts.label_of(dicts.LEAD_SOURCE_LABELS, lead.source),
            "city": lead.city or "",
        },
    )
    if remark:
        lead.remark = lead.remark or remark


async def _record(
    db: AsyncSession,
    *,
    lead: Lead,
    action: str,
    from_owner_id: str | None,
    from_owner_name: str | None,
    to_owner_id: str | None,
    to_owner_name: str | None,
    operator: User,
    remark: str | None,
) -> None:
    db.add(
        Assignment(
            lead_id=lead.id,
            action=action,
            from_owner_id=from_owner_id,
            from_owner_name=from_owner_name,
            to_owner_id=to_owner_id,
            to_owner_name=to_owner_name,
            operator_id=operator.id,
            operator_name=operator.name,
            remark=remark,
        )
    )


async def change_owner(
    db: AsyncSession,
    *,
    lead: Lead,
    new_owner_id: str | None,
    operator: User,
    remark: str | None = None,
) -> None:
    """改派或回收（new_owner_id=None 表示回收至公海）。"""
    from_id, from_name = lead.owner_id, lead.owner_name

    if new_owner_id is None:
        lead.owner_id = None
        lead.owner_name = None
        lead.status = LeadStatus.UNASSIGNED
        lead.assign_at = None
        lead.first_follow_deadline = None
        lead.recycle_deadline = None
        action = AssignmentAction.RECYCLE
    else:
        await setup_owner_sla(db, lead, new_owner_id, operator=operator, remark=remark)
        action = AssignmentAction.REASSIGN if from_id else AssignmentAction.ASSIGN

    await _record(
        db,
        lead=lead,
        action=action,
        from_owner_id=from_id,
        from_owner_name=from_name,
        to_owner_id=lead.owner_id,
        to_owner_name=lead.owner_name,
        operator=operator,
        remark=remark,
    )
    await audit_service.write_log(
        db,
        operator=operator,
        action=action,
        target_type="lead",
        target_id=lead.id,
        target_name=lead.name,
        detail=(
            f"归属人 {from_name or '公海'} → {lead.owner_name or '公海'}；备注：{remark or '无'}"
        ),
    )


async def assign_many(db: AsyncSession, *, user: User, payload: AssignRequest) -> int:
    """批量指派 / 改派。"""
    owner = (await db.execute(select(User).where(User.id == payload.owner_id))).scalar_one_or_none()
    if owner is None:
        raise NotFoundError("目标归属人不存在")

    leads = (
        (
            await db.execute(
                select(Lead).where(Lead.id.in_(payload.lead_ids), Lead.is_deleted.is_(False))
            )
        )
        .scalars()
        .all()
    )

    for lead in leads:
        await change_owner(
            db, lead=lead, new_owner_id=payload.owner_id, operator=user, remark=payload.remark
        )
    return len(leads)


async def recycle_many(db: AsyncSession, *, user: User, payload: RecycleRequest) -> int:
    """手工回收至公海（管理人员）。"""
    leads = (
        (
            await db.execute(
                select(Lead).where(Lead.id.in_(payload.lead_ids), Lead.is_deleted.is_(False))
            )
        )
        .scalars()
        .all()
    )
    for lead in leads:
        if lead.current_stage:
            raise ConflictError(f"{lead.name} 已进入闭环二，不再自动回收；请使用改派")
        from_owner_id = lead.owner_id
        from_owner_name = lead.owner_name
        await change_owner(db, lead=lead, new_owner_id=None, operator=user, remark=payload.remark)
        # spec/02 §8「回收之后…通知本人 + 管理人员」：手工回收同样要让原归属人知道
        # （否则他会继续跟进一条已经改不动、也不属于他的线索）
        receivers = [r for r in [from_owner_id, *await audit_service.manager_ids(db)] if r]
        if receivers:
            await audit_service.notify_many(
                db,
                user_ids=receivers,
                title=f"线索已回收至公海：{lead.name}",
                content=(
                    f"原归属人：{from_owner_name or '未指派'}｜操作人：{user.name}"
                    f"｜原因：{payload.remark or '未填写'}"
                ),
                type_="lead_recycled",
                biz_type="lead",
                biz_id=lead.id,
                event="recycle_due",
                context={
                    "lead_name": lead.name,
                    "days": 0,
                    "workdays": settings.LEAD_RECYCLE_WORKING_DAYS,
                },
            )
    return len(leads)


async def claim(db: AsyncSession, *, user: User, lead: Lead) -> Lead:
    """公海重新领取（S1-5：可见不可编辑、可重新领取）。"""
    if lead.owner_id:
        raise ConflictError("该线索已有归属人，不能重复领取")
    await setup_owner_sla(db, lead, user.id, operator=user, remark="公海重新领取")
    await _record(
        db,
        lead=lead,
        action=AssignmentAction.CLAIM,
        from_owner_id=None,
        from_owner_name=None,
        to_owner_id=user.id,
        to_owner_name=user.name,
        operator=user,
        remark="公海领取",
    )
    await audit_service.write_log(
        db,
        operator=user,
        action="claim",
        target_type="lead",
        target_id=lead.id,
        target_name=lead.name,
        detail="从公海领取",
    )
    return lead


async def load_view(db: AsyncSession) -> list[dict]:
    """负载视图：各市场专员手上各状态条数。"""
    rows = (
        await db.execute(
            select(User.id, User.name, Lead.status, func.count(Lead.id))
            .select_from(User)
            .outerjoin(
                Lead,
                (Lead.owner_id == User.id) & (Lead.is_deleted.is_(False)),
            )
            .where(
                User.role.in_([UserRole.MARKET_STAFF, UserRole.MARKET_MANAGER]),
                User.is_active.is_(True),
            )
            .group_by(User.id, User.name, Lead.status)
        )
    ).all()

    agg: dict[str, dict] = {}
    for uid, uname, status, count in rows:
        item = agg.setdefault(
            uid,
            {
                "user_id": uid,
                "name": uname,
                "unassigned_count": 0,
                "assigned_count": 0,
                "following_count": 0,
                "valid_count": 0,
                "total_count": 0,
            },
        )
        if status == LeadStatus.ASSIGNED:
            item["assigned_count"] += count
        elif status == LeadStatus.FOLLOWING:
            item["following_count"] += count
        elif status in (LeadStatus.VALID, LeadStatus.SIGNED):
            item["valid_count"] += count
        item["total_count"] += count

    result = list(agg.values())
    result.sort(key=lambda x: x["total_count"], reverse=True)
    return result


async def unassigned_count(db: AsyncSession) -> int:
    return int(
        (
            await db.execute(
                select(func.count())
                .select_from(Lead)
                .where(Lead.is_deleted.is_(False), Lead.owner_id.is_(None))
            )
        ).scalar()
        or 0
    )


async def pending_recycle_reminders(db: AsyncSession) -> int:
    return int(
        (
            await db.execute(
                select(func.count())
                .select_from(Reminder)
                .where(Reminder.status == ReminderStatus.PENDING)
            )
        ).scalar()
        or 0
    )
