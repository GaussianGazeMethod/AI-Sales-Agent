"""待办中心、通知中心、目标接口。"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import (
    get_current_user,
    require_lead_target,
    require_lead_view,
    require_lead_write,
)
from app.core.errors import ConflictError, NotFoundError, PermissionDeniedError
from app.database import get_db
from app.models.activity import Reminder
from app.models.enums import ReminderStatus, ReminderType
from app.models.system import Notification
from app.models.user import User
from app.schemas import (
    IdsRequest,
    SalesActionCreate,
    SalesActionFeedback,
    TargetBatchSet,
    TargetSet,
)
from app.serializers import followup_out, notification_out, reminder_out, target_out
from app.services import lead_service, sales_action_service, sla_service, target_service

router = APIRouter(tags=["待办 / 通知 / 目标"])


# ══════════════ 待办 ══════════════


@router.get("/reminders", summary="待办列表")
async def list_reminders(
    status: str | None = ReminderStatus.PENDING,
    type: str | None = None,
    page: int = 1,
    page_size: int = 20,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    stmt = select(Reminder).where(Reminder.receiver_id == user.id)
    if status:
        stmt = stmt.where(Reminder.status == status)
    if type:
        stmt = stmt.where(Reminder.type == type)
    total = int((await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar() or 0)
    rows = (
        (
            await db.execute(
                stmt.order_by(Reminder.remind_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .all()
    )
    return {
        "items": [reminder_out(r) for r in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.post("/reminders/{reminder_id}/done", summary="完成单条待办")
async def done_reminder(
    reminder_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    from datetime import datetime

    row = (
        await db.execute(select(Reminder).where(Reminder.id == reminder_id))
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError("待办不存在")
    if row.receiver_id != user.id:
        raise PermissionDeniedError("只能处理本人待办")
    if row.type == ReminderType.SALES_ACTION:
        raise ConflictError("请填写实际执行结果与客户反馈后完成销售行动")
    row.status = ReminderStatus.DONE
    row.done_at = datetime.now()
    row.done_by = user.id
    return {"ok": True}


@router.post("/reminders/done-batch", summary="批量完成待办")
async def done_reminders_batch(
    payload: IdsRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    from datetime import datetime

    rows = (
        (
            await db.execute(
                select(Reminder).where(
                    Reminder.id.in_(payload.ids), Reminder.receiver_id == user.id
                )
            )
        )
        .scalars()
        .all()
    )
    if any(row.type == ReminderType.SALES_ACTION for row in rows):
        raise ConflictError("销售行动需要逐条填写执行反馈，不能批量完成")
    for row in rows:
        row.status = ReminderStatus.DONE
        row.done_at = datetime.now()
        row.done_by = user.id
    return {"ok": True, "affected": len(rows)}


@router.post("/reminders/scan", summary="手工触发 SLA 扫描（回收 + 提醒）")
async def scan_reminders(
    user: User = Depends(require_lead_view), db: AsyncSession = Depends(get_db, scope="function")
) -> dict:
    result = await sla_service.scan(db)
    return {"ok": True, **result}


# ══════════════ 通知 ══════════════


@router.get("/notifications", summary="通知列表")
async def list_notifications(
    unread_only: bool = False,
    page: int = 1,
    page_size: int = 20,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    stmt = select(Notification).where(Notification.user_id == user.id)
    if unread_only:
        stmt = stmt.where(Notification.is_read.is_(False))
    total = int((await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar() or 0)
    unread = int(
        (
            await db.execute(
                select(func.count())
                .select_from(Notification)
                .where(Notification.user_id == user.id, Notification.is_read.is_(False))
            )
        ).scalar()
        or 0
    )
    rows = (
        (
            await db.execute(
                stmt.order_by(Notification.created_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .all()
    )
    return {
        "items": [notification_out(n) for n in rows],
        "total": total,
        "unread": unread,
        "page": page,
        "page_size": page_size,
    }


@router.post("/notifications/{notification_id}/read", summary="标记已读")
async def read_notification(
    notification_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    row = (
        await db.execute(
            select(Notification).where(
                Notification.id == notification_id, Notification.user_id == user.id
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError("通知不存在")
    row.is_read = True
    return {"ok": True}


@router.post("/notifications/read-all", summary="全部标记已读")
async def read_all_notifications(
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db, scope="function")
) -> dict:
    rows = (
        (
            await db.execute(
                select(Notification).where(
                    Notification.user_id == user.id, Notification.is_read.is_(False)
                )
            )
        )
        .scalars()
        .all()
    )
    for row in rows:
        row.is_read = True
    return {"ok": True, "affected": len(rows)}


# ══════════════ 市场目标 ══════════════


@router.get("/targets", summary="目标列表（含达成）")
async def list_targets(
    period: str | None = None,
    user_id: str | None = None,
    user: User = Depends(require_lead_view),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    # 专员只能看自己的目标
    scope = user_id
    if user.role not in {"admin", "market_manager", "executive"}:
        scope = user.id
    rows = await target_service.list_targets(db, period=period, user_id=scope)
    return {
        "items": [target_out(t, actual) for t, actual in rows],
        "total": len(rows),
        "page": 1,
        "page_size": len(rows),
    }


@router.post("/targets", summary="设定目标（管理人员）")
async def set_target(
    payload: TargetSet,
    user: User = Depends(require_lead_target),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    target = await target_service.set_target(db, operator=user, payload=payload)
    actual = await target_service.actual_value(
        db, user_id=target.user_id, metric=target.metric, period=target.period
    )
    return {"item": target_out(target, actual)}


@router.post("/targets/batch", summary="批量设定目标")
async def set_targets_batch(
    payload: TargetBatchSet,
    user: User = Depends(require_lead_target),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    items = []
    for item in payload.items:
        item.period = payload.period
        target = await target_service.set_target(db, operator=user, payload=item)
        actual = await target_service.actual_value(
            db, user_id=target.user_id, metric=target.metric, period=target.period
        )
        items.append(target_out(target, actual))
    return {"items": items, "total": len(items)}


@router.get("/targets/achievement", summary="目标达成看板")
async def achievement(
    period: str | None = None,
    user: User = Depends(require_lead_view),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    period = period or target_service.current_period()
    scope = None if user.role in {"admin", "market_manager", "executive"} else user.id
    items = await target_service.achievement(db, period=period, scope_user_id=scope)
    return {"period": period, "items": items}


@router.get("/sales-actions", summary="项目的销售行动与执行反馈")
async def list_sales_actions(
    lead_id: str,
    user: User = Depends(require_lead_view),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    from app.models.activity import FollowUp

    lead = await lead_service.get_lead(db, lead_id)
    lead_service.assert_can_view(user, lead)
    rows = (
        (
            await db.execute(
                select(Reminder)
                .where(Reminder.lead_id == lead_id, Reminder.type == ReminderType.SALES_ACTION)
                .order_by(Reminder.status.desc(), Reminder.remind_at.asc())
            )
        )
        .scalars()
        .all()
    )
    items = []
    for row in rows:
        item = reminder_out(row)
        follow = (
            await db.get(FollowUp, row.completion_followup_id)
            if row.completion_followup_id
            else None
        )
        item["feedback"] = followup_out(follow) if follow else None
        items.append(item)
    return {"items": items, "total": len(items)}


@router.post("/sales-actions", summary="人工确认下一步销售行动")
async def create_sales_action(
    payload: SalesActionCreate,
    user: User = Depends(require_lead_write),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    row = await sales_action_service.create(db, user, payload)
    return reminder_out(row)


@router.post("/sales-actions/{action_id}/feedback", summary="执行反馈写入跟进并完成行动")
async def feedback_sales_action(
    action_id: str,
    payload: SalesActionFeedback,
    user: User = Depends(require_lead_write),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    row, follow = await sales_action_service.feedback(db, user, action_id, payload)
    return {"action": reminder_out(row), "followup": followup_out(follow)}
