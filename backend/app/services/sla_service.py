"""SLA 服务：待办提醒扫描 + 7 个工作日未跟进自动回收（S1-5）。

由进程内定时任务（app/scheduler.py）周期调用，也可由管理员手工触发（POST /reminders/scan）。
所有生成都用 `dedup_key` 幂等，重复扫描不会产生重复待办。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.activity import Assignment, Reminder
from app.models.enums import AssignmentAction, LeadStatus, ReminderStatus, ReminderType
from app.models.lead import Lead
from app.models.user import User
from app.services import audit_service, calendar_service

settings = get_settings()

# 提前多久提醒（小时）
DUE_SOON_HOURS = 24


async def _already(db: AsyncSession, dedup_key: str) -> bool:
    row = (
        await db.execute(select(Reminder.id).where(Reminder.dedup_key == dedup_key))
    ).scalar_one_or_none()
    return row is not None


async def _add_reminder(
    db: AsyncSession,
    *,
    lead: Lead,
    type_: str,
    content: str,
    receiver_id: str,
    receiver_name: str,
    remind_at: datetime,
    dedup_key: str,
) -> bool:
    if await _already(db, dedup_key):
        return False
    db.add(
        Reminder(
            lead_id=lead.id,
            lead_name=lead.name,
            type=type_,
            content=content,
            remind_at=remind_at,
            receiver_id=receiver_id,
            receiver_name=receiver_name,
            dedup_key=dedup_key,
        )
    )
    return True


async def scan(db: AsyncSession) -> dict:
    """扫描并按需生成提醒 / 执行自动回收。返回统计。"""
    now = datetime.now()
    today = now.strftime("%Y%m%d")
    created = 0
    recycled = 0

    rows = (
        (
            await db.execute(
                select(Lead).where(
                    Lead.is_deleted.is_(False),
                    Lead.owner_id.is_not(None),
                    Lead.status.in_([LeadStatus.ASSIGNED, LeadStatus.FOLLOWING]),
                    # 已进入闭环二的不再自动回收
                    Lead.current_stage.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )

    from app.models.dossier import AssistantAction

    for lead in rows:
        if lead.owner_id is None:
            continue

        appointments = (
            (
                await db.execute(
                    select(AssistantAction).where(
                        AssistantAction.lead_id == lead.id,
                        AssistantAction.owner_id == lead.owner_id,
                        AssistantAction.customer_confirmed.is_(True),
                        AssistantAction.due_at.is_not(None),
                        AssistantAction.status == "pending",
                    )
                )
            )
            .scalars()
            .all()
        )
        waiting = [a for a in appointments if a.due_at >= now]
        if waiting:
            # 真实客户预约等待期间不生成回收提醒，不删除已联系事实。
            continue
        missed = [
            a
            for a in appointments
            if a.due_at < now and (lead.last_follow_at is None or lead.last_follow_at < a.due_at)
        ]
        if missed:
            anchor = max(a.due_at for a in missed)
            deadline = await calendar_service.deadline_after_working_days(
                db, anchor, settings.LEAD_RECYCLE_WORKING_DAYS
            )
            if lead.recycle_deadline is None or lead.recycle_deadline < deadline:
                lead.recycle_deadline = deadline
        # ── 7 个工作日未跟进 → 自动回收 ──
        if lead.recycle_deadline and now > lead.recycle_deadline:
            await _auto_recycle(db, lead, now)
            recycled += 1
            continue

        # ── 24 小时（1 个工作日）未首次跟进 ──
        if lead.first_follow_at is None and lead.first_follow_deadline:
            if now > lead.first_follow_deadline:
                created += await _add_reminder(
                    db,
                    lead=lead,
                    type_=ReminderType.FIRST_FOLLOW_OVERDUE,
                    content=f"已超时：{lead.name} 指派后超过 1 个工作日未首次跟进",
                    receiver_id=lead.owner_id,
                    receiver_name=lead.owner_name or "",
                    remind_at=now,
                    dedup_key=f"overdue:{lead.id}:{today}",
                )
            elif lead.first_follow_deadline - now <= timedelta(hours=DUE_SOON_HOURS):
                created += await _add_reminder(
                    db,
                    lead=lead,
                    type_=ReminderType.FIRST_FOLLOW_DUE,
                    content=f"即将超时：{lead.name} 请在 1 个工作日内完成首次跟进",
                    receiver_id=lead.owner_id,
                    receiver_name=lead.owner_name or "",
                    remind_at=now,
                    dedup_key=f"due:{lead.id}:{today}",
                )

        # ── 即将回收提醒（回收前 1 个工作日） ──
        if lead.recycle_deadline:
            elapsed = lead.recycle_deadline - now
            if timedelta(0) < elapsed <= timedelta(hours=DUE_SOON_HOURS * 2):
                created += await _add_reminder(
                    db,
                    lead=lead,
                    type_=ReminderType.RECYCLE_DUE,
                    content=f"即将回收：{lead.name} 已接近 7 个工作日未跟进",
                    receiver_id=lead.owner_id,
                    receiver_name=lead.owner_name or "",
                    remind_at=now,
                    dedup_key=f"recycle_due:{lead.id}:{today}",
                )

    # 助手行动到期提醒与实际行动分开，未知时间不生成虚假到期提醒。
    due_actions = (
        (
            await db.execute(
                select(AssistantAction).where(
                    AssistantAction.status == "pending",
                    AssistantAction.due_at <= now,
                )
            )
        )
        .scalars()
        .all()
    )
    for action in due_actions:
        lead = await db.get(Lead, action.lead_id)
        if not lead or lead.is_deleted or lead.owner_id != action.owner_id:
            continue
        created += await _add_reminder(
            db,
            lead=lead,
            type_=ReminderType.NEXT_FOLLOW_DUE,
            content="销售行动到期：" + action.content[:230],
            receiver_id=action.owner_id,
            receiver_name=lead.owner_name or "",
            remind_at=action.due_at,
            dedup_key=f"assistant_action:{action.id}:{action.due_at.isoformat()}",
        )

    # ── 下次跟进到点 ──
    # 这类待办在写跟进记录时就已经落库并保持 pending，到点即为"待办"，
    # 无需额外处理；这里只统计一下，便于日志与接口返回观测。
    due_next_count = int(
        (
            await db.execute(
                select(func.count())
                .select_from(Reminder)
                .where(
                    Reminder.type == ReminderType.NEXT_FOLLOW_DUE,
                    Reminder.status == ReminderStatus.PENDING,
                    Reminder.remind_at <= now,
                )
            )
        ).scalar()
        or 0
    )

    await db.flush()
    return {
        "created": created,
        "recycled": recycled,
        "scanned": len(rows),
        "next_follow_due": due_next_count,
    }


async def _auto_recycle(db: AsyncSession, lead: Lead, now: datetime) -> None:
    """自动回收至公海，并通知本人与管理人员。"""
    from_owner_id, from_owner_name = lead.owner_id, lead.owner_name

    lead.owner_id = None
    lead.owner_name = None
    lead.status = LeadStatus.UNASSIGNED
    lead.assign_at = None
    lead.first_follow_deadline = None
    lead.recycle_deadline = None
    lead.is_suspect_duplicate = lead.is_suspect_duplicate

    db.add(
        Assignment(
            lead_id=lead.id,
            action=AssignmentAction.RECYCLE,
            from_owner_id=from_owner_id,
            from_owner_name=from_owner_name,
            to_owner_id=None,
            to_owner_name=None,
            operator_id="system",
            operator_name="系统自动回收",
            remark=f"{settings.LEAD_RECYCLE_WORKING_DAYS} 个工作日未跟进，自动回收至公海",
        )
    )

    receivers = [from_owner_id] + await audit_service.manager_ids(db)
    await audit_service.notify_many(
        db,
        user_ids=[r for r in receivers if r],
        title="线索已自动回收",
        content=(
            f"{lead.name} 超过 {settings.LEAD_RECYCLE_WORKING_DAYS} 个工作日未跟进，已回收至公海"
        ),
        type_="lead_recycled",
        biz_type="lead",
        biz_id=lead.id,
    )

    if from_owner_id:
        await _add_reminder(
            db,
            lead=lead,
            type_=ReminderType.RECYCLED,
            content=f"已回收：{lead.name} 超出 {settings.LEAD_RECYCLE_WORKING_DAYS} 个工作日未跟进",
            receiver_id=from_owner_id,
            receiver_name=from_owner_name or "",
            remind_at=now,
            dedup_key=f"recycled:{lead.id}",
        )

    await audit_service.write_log(
        db,
        operator=User(id="system", username="system", name="系统自动回收", role="admin"),
        action="auto_recycle",
        target_type="lead",
        target_id=lead.id,
        target_name=lead.name,
        detail=f"{settings.LEAD_RECYCLE_WORKING_DAYS} 个工作日未跟进",
    )


async def lead_sla_snapshot(db: AsyncSession, lead: Lead) -> dict:
    """单条线索的 SLA 快照（详情页展示倒计时用）。"""
    now = datetime.now()
    result = {
        "first_follow_overdue": False,
        "recycle_overdue": False,
        "first_follow_remaining_hours": None,
        "recycle_remaining_hours": None,
        "workdays_since_assign": None,
    }
    if lead.assign_at:
        result["workdays_since_assign"] = await calendar_service.elapsed_working_days(
            db, lead.assign_at, now
        )
    if lead.first_follow_at is None and lead.first_follow_deadline:
        result["first_follow_overdue"] = now > lead.first_follow_deadline
        result["first_follow_remaining_hours"] = round(
            (lead.first_follow_deadline - now).total_seconds() / 3600, 1
        )
    if lead.recycle_deadline:
        result["recycle_overdue"] = now > lead.recycle_deadline
        result["recycle_remaining_hours"] = round(
            (lead.recycle_deadline - now).total_seconds() / 3600, 1
        )
    return result
