"""销售行动：人工确认建议 → 待办 → 实际反馈形成跟进；不自动推进环节。"""

from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BizError, ConflictError, NotFoundError, PermissionDeniedError
from app.models.activity import FollowUp, Reminder
from app.models.ai import AiDiagnosis
from app.models.enums import LeadStatus, ReminderStatus, ReminderType
from app.models.user import User
from app.schemas import FollowUpCreate, SalesActionCreate, SalesActionFeedback
from app.services import followup_service, lead_service


async def create(db: AsyncSession, user: User, payload: SalesActionCreate) -> Reminder:
    lead = await lead_service.get_lead(db, payload.lead_id)
    lead_service.assert_can_edit(user, lead)
    if lead.owner_id != user.id:
        raise PermissionDeniedError("请由项目负责人确认自己的下一步行动")
    if not lead.current_stage or lead.status in (LeadStatus.SIGNED, LeadStatus.LOST):
        raise ConflictError("仅进行中的商机可以创建销售行动")
    suggestion = None
    if (payload.diagnosis_id is None) != (payload.action_index is None):
        raise BizError("建议出处不完整，请重新选择建议", status_code=422)
    if payload.diagnosis_id:
        diagnosis = await db.get(AiDiagnosis, payload.diagnosis_id)
        if diagnosis is None or diagnosis.lead_id != lead.id:
            raise BizError("诊断与当前项目不匹配", status_code=422)
        actions = (diagnosis.structured or {}).get("suggested_actions") or []
        if payload.action_index >= len(actions):
            raise BizError("诊断建议不存在，请刷新诊断", status_code=422)
        suggestion = actions[payload.action_index]
    key = (
        f"sales_action:{lead.id}:{payload.diagnosis_id}:{payload.action_index}"
        if payload.diagnosis_id
        else f"sales_action:{user.id}:{payload.request_id}"
    )
    existing = (
        await db.execute(select(Reminder).where(Reminder.dedup_key == key))
    ).scalar_one_or_none()
    if existing:
        if existing.lead_id != lead.id:
            raise ConflictError("请求已用于另一个项目，请重新创建行动")
        return existing
    action = Reminder(
        lead_id=lead.id,
        lead_name=lead.name,
        type=ReminderType.SALES_ACTION,
        content=payload.content,
        remind_at=payload.remind_at,
        receiver_id=user.id,
        receiver_name=user.name,
        dedup_key=key,
        source_diagnosis_id=payload.diagnosis_id,
        source_action_index=payload.action_index,
        source_suggestion=suggestion,
    )
    db.add(action)
    await db.flush()
    return action


async def feedback(db: AsyncSession, user: User, action_id: str, payload: SalesActionFeedback):
    action = await db.get(Reminder, action_id)
    if action is None or action.type != ReminderType.SALES_ACTION:
        raise NotFoundError("销售行动不存在")
    if action.receiver_id != user.id:
        raise PermissionDeniedError("只能反馈本人确认的销售行动")
    lead = await lead_service.get_lead(db, action.lead_id)
    lead_service.assert_can_edit(user, lead)
    if action.completion_followup_id:
        return action, await db.get(FollowUp, action.completion_followup_id)
    # 条件更新争取完成权，避免重复提交/并发请求创建重复跟进；失败会整体回滚。
    claimed = await db.execute(
        update(Reminder)
        .where(Reminder.id == action.id, Reminder.status == ReminderStatus.PENDING)
        .values(status=ReminderStatus.DONE)
    )
    if not claimed.rowcount:
        raise ConflictError("行动已处理，请刷新查看结果")
    follow, _ = await followup_service.create_followup(
        db,
        user=user,
        lead=lead,
        payload=FollowUpCreate(
            method=payload.method,
            content=payload.content,
            followed_at=payload.followed_at,
            next_follow_at=payload.next_follow_at,
            is_valid="valid",
            next_action="other",
        ),
    )
    action.completion_followup_id = follow.id
    action.done_at = datetime.now()
    action.done_by = user.id
    await db.flush()
    return action, follow
