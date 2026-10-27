"""跟进服务：跟进记录 + 有效性自动判定（S1-4）。

有效性标准（已确认）：**客户信息是否核实正确 且 有意愿**
（同意下一步汇报交流 / 微信交流看我方资料）→ 有效商机；其他一律无效。
"""

from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.dicts import NEXT_ACTION_LABELS, label_of
from app.core.errors import ConflictError, ValidationError
from app.models.activity import FollowUp
from app.models.enums import LeadStatus, NextAction
from app.models.lead import Lead
from app.models.user import User
from app.schemas import FollowUpCreate
from app.services import audit_service, calendar_service, judgement_service, lead_service

settings = get_settings()

logger = logging.getLogger(__name__)

# 视为"有意愿"的下一步动作
INTENT_ACTIONS = {NextAction.REPORT_MEETING, NextAction.WECHAT_MATERIAL}


def judge_validity(
    *, info_verified: bool | None, declared: str | None, next_action: str | None
) -> tuple[bool, str]:
    """系统自动判定有效/无效，并给出人类可读的理由（spec/02 §7）。

     `info_verified=None`（未说明）按**未核实**处理，而不是默认成"已核实"：
    「目标客户信息正确」是判有效的前提，默认成真是把前提凭空补上。
    """
    if info_verified is None:
        return False, "未说明客户信息是否核实正确 → 无效（信息正确是判有效的前提）"
    if not info_verified:
        return False, "客户信息未核实正确 → 无效"
    if declared == "invalid":
        return False, "跟进人判定为无效 → 无效"
    if next_action in INTENT_ACTIONS:
        return (
            True,
            f"客户信息正确且有意愿（{label_of(NEXT_ACTION_LABELS, next_action)}）→ 有效商机",
        )
    return False, "未取得客户意愿（下一步动作非汇报交流/微信交流看我方资料）→ 无效"


async def create_followup(
    db: AsyncSession, *, user: User, lead: Lead, payload: FollowUpCreate
) -> tuple[FollowUp, dict]:
    """写跟进记录，并驱动状态机与【编排 O1】。"""
    if lead.status in (LeadStatus.SIGNED,):
        raise ConflictError("已签约客户请到闭环三记录运营回访")

    followed_at = payload.followed_at or datetime.now()

    follow = FollowUp(
        lead_id=lead.id,
        user_id=user.id,
        user_name=user.name,
        method=payload.method,
        followed_at=followed_at,
        content=payload.content,
        is_valid=payload.is_valid,
        next_action=payload.next_action,
        next_follow_at=payload.next_follow_at,
        stage=lead.current_stage,
        info_verified=payload.info_verified,
        source="manual",
    )
    db.add(follow)

    # ── 时间戳与 SLA 重置 ──
    if lead.first_follow_at is None:
        lead.first_follow_at = followed_at
    lead.last_follow_at = followed_at
    if lead.status == LeadStatus.ASSIGNED:
        lead.status = LeadStatus.FOLLOWING

    if lead.owner_id:
        lead.recycle_deadline = await calendar_service.deadline_after_working_days(
            db, followed_at, settings.LEAD_RECYCLE_WORKING_DAYS
        )

    validity: dict
    if lead.status in (LeadStatus.UNASSIGNED, LeadStatus.ASSIGNED, LeadStatus.FOLLOWING):
        valid, reason = judge_validity(
            info_verified=payload.info_verified,
            declared=payload.is_valid,
            next_action=payload.next_action,
        )
        validity = {"is_valid": valid, "reason": reason}
        if valid:
            #  闭环一出口 = 闭环二入口（有效商机 = 环节一）
            await lead_service.mark_as_opportunity(db, lead=lead, user=user)
        else:
            await lead_service.mark_invalid(db, lead=lead, user=user)
    else:
        # 已在闭环二：跟进只记录，不改状态
        validity = {"is_valid": True, "reason": "已进入闭环二，跟进仅记录不改变状态"}

    # 下次跟进到点提醒
    if payload.next_follow_at:
        from app.models.activity import Reminder
        from app.models.enums import ReminderType

        db.add(
            Reminder(
                lead_id=lead.id,
                lead_name=lead.name,
                type=ReminderType.NEXT_FOLLOW_DUE,
                content=f"下次跟进到点：{lead.name}",
                remind_at=payload.next_follow_at,
                receiver_id=user.id,
                receiver_name=user.name,
                dedup_key=(
                    f"next_follow:{lead.id}:{payload.next_follow_at.isoformat()}"
                    f":{datetime.now().isoformat()}"
                ),
            )
        )

    await audit_service.write_log(
        db,
        operator=user,
        action="followup",
        target_type="lead",
        target_id=lead.id,
        target_name=lead.name,
        detail=f"跟进（{payload.method}）：{validity['reason']}",
    )

    #  跟进写入成功后**自动**跑一次「按跟进信息判定环节标准」。
    # 只排后台任务：判定要调模型（5–20 秒），绝不能拖慢跟进接口；
    # 判定结果只是待确认草稿，「采纳」这一步仍由项目负责人人工确认（spec/10 第 191 行）。
    await schedule_standard_judgement(db, lead=lead, user=user, follow=follow)
    return follow, validity


async def schedule_standard_judgement(
    db: AsyncSession, *, lead: Lead, user: User, follow: FollowUp
) -> None:
    """把「自动判定」排进后台任务（`create_followup` / `add_followup_from_ai` 共用）。

     这里所有异常都必须吞掉：判定是**附加价值**，任何情况下都不能
    让跟进写入失败、也不能让接口返回 5xx。
    """
    if not lead.current_stage:
        return  # 还没进入闭环二（没有「当前环节标准」可判）
    try:
        # 后台任务要靠 id 等「这条跟进已提交可见」，所以先 flush 拿到 id
        await db.flush()
        judgement_service.schedule_auto_judgement(
            lead_id=lead.id, user_id=user.id, followup_id=follow.id
        )
    except Exception as exc:  # noqa: BLE001 - 判定排期失败绝不影响跟进
        logger.warning(
            "排「自动判定环节标准」任务失败（已忽略，不影响跟进写入）：lead=%s %s",
            lead.id,
            exc,
        )


async def list_followups(db: AsyncSession, lead_id: str) -> list[FollowUp]:
    rows = (
        (
            await db.execute(
                select(FollowUp)
                .where(FollowUp.lead_id == lead_id)
                .order_by(FollowUp.followed_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


async def add_followup_from_ai(
    db: AsyncSession,
    *,
    user: User,
    lead: Lead,
    method: str,
    content: str,
    followed_at: datetime | None = None,
    next_action: str | None = None,
    is_valid: str | None = None,
    standard_id: str | None = None,
) -> FollowUp:
    """AI 草稿确认后落库的跟进记录（标注来源，便于区分"AI 生成·已确认"）。"""
    if not content:
        raise ValidationError("跟进内容不能为空")
    follow = FollowUp(
        lead_id=lead.id,
        user_id=user.id,
        user_name=user.name,
        method=method or "wechat",
        followed_at=followed_at or datetime.now(),
        content=content,
        is_valid=is_valid,
        next_action=next_action,
        stage=lead.current_stage,
        related_standard_id=standard_id,
        source="ai_confirmed",
    )
    db.add(follow)
    lead.last_follow_at = follow.followed_at
    if lead.first_follow_at is None:
        lead.first_follow_at = follow.followed_at
    if lead.status == LeadStatus.ASSIGNED:
        lead.status = LeadStatus.FOLLOWING
    # flush 以便调用方立刻拿到 id（AI 草稿确认时要回写 apply_result）
    await db.flush()
    #  AI 草稿确认后落库的跟进同样**自动**触发判定（与手工跟进一条链路）
    await schedule_standard_judgement(db, lead=lead, user=user, follow=follow)
    return follow
