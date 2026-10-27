"""AI 销售助手接口（5.1 对话与录入 / 5.2 我的待确认 / 5.3 诊断历史）。

 核心规则：AI 只产出草稿，**必须由项目负责人确认后才生效**（未确认不生效）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.runtime import describe_ai
from app.config import get_settings
from app.core.deps import get_current_user, require_lead_view, require_lead_write
from app.core.errors import BizError
from app.database import get_db
from app.models.enums import AiDraftStatus, IntakeParseStatus
from app.models.system import ImMessage
from app.models.user import User
from app.schemas import (
    AiDiagnoseRequest,
    AiDraftBatchConfirmRequest,
    AiDraftConfirmRequest,
    AiIntakeRequest,
    ImIntakeRequest,
)
from app.serializers import ai_diagnosis_out, ai_draft_out, lead_out
from app.services import ai_service, lead_service

router = APIRouter(prefix="/ai", tags=["AI 销售助手"])
settings = get_settings()


@router.get("/status", summary="AI 能力状态（provider / 模型别名 / 失败策略）")
async def ai_status(user: User = Depends(get_current_user)) -> dict:
    """返回 Agently provider 的非密投影。

    `fallback_mode` 恒为 `none`：AI 不可用时直接失败，
    不做规则降级。
    """
    info = describe_ai()
    return {
        "llm_configured": info.get("enabled", False),
        "model": info.get("model"),
        "fallback_mode": "none",
        "degrade_policy": info.get("degrade_policy", "fail_closed"),
        "provider": info.get("provider"),
        "settings_file": info.get("settings_file"),
        "profiles": info.get("profiles"),
        "note": info.get("note")
        or "AI 不可用时直接返回错误（不做规则降级）：录入草稿与诊断都需要模型参与",
    }


@router.post("/intake", summary="5.1 发结构化信息 → 生成更新草稿")
async def intake(
    payload: AiIntakeRequest,
    user: User = Depends(require_lead_write),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    draft = await ai_service.parse_intake(db, user=user, payload=payload)
    return ai_draft_out(draft)


@router.get("/drafts", summary="5.2 我的待确认（草稿列表）")
async def list_drafts(
    status: str | None = AiDraftStatus.PENDING,
    mine_only: bool = True,
    lead_id: str | None = Query(None, description="只看某个商机的草稿（2.2 用）"),
    page: int = 1,
    page_size: int = 20,
    user: User = Depends(require_lead_view),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    rows, total = await ai_service.list_drafts(
        db,
        user=user,
        status=status,
        mine_only=mine_only,
        lead_id=lead_id,
        offset=(page - 1) * page_size,
        limit=page_size,
    )
    return {
        "items": [ai_draft_out(d) for d in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/drafts/{draft_id}", summary="草稿详情")
async def get_draft(
    draft_id: str,
    user: User = Depends(require_lead_view),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    draft = await ai_service.get_draft(db, draft_id)
    return ai_draft_out(draft)


@router.post("/drafts/{draft_id}/confirm", summary="⭐ 确认草稿（确认后才落库）")
async def confirm_draft(
    draft_id: str,
    payload: AiDraftConfirmRequest | None = None,
    user: User = Depends(require_lead_write),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    draft = await ai_service.get_draft(db, draft_id)
    result = await ai_service.confirm_draft(
        db,
        user=user,
        draft=draft,
        payload_override=(payload.payload_override if payload else None),
        remark=(payload.remark if payload else None),
    )
    return {
        "draft": ai_draft_out(result["draft"]),
        "applied": result["applied"],
        "notes": result["notes"],
        "lead": lead_out(result["lead"]),
    }


@router.post("/drafts/{draft_id}/ignore", summary="忽略草稿")
async def ignore_draft(
    draft_id: str,
    payload: AiDraftConfirmRequest | None = None,
    user: User = Depends(require_lead_write),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    draft = await ai_service.get_draft(db, draft_id)
    await ai_service.ignore_draft(
        db, user=user, draft=draft, remark=(payload.remark if payload else None)
    )
    return {"ok": True}


@router.post("/drafts/confirm-batch", summary="批量确认草稿")
async def confirm_batch(
    payload: AiDraftBatchConfirmRequest,
    user: User = Depends(require_lead_write),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    result = await ai_service.confirm_many(db, user=user, ids=payload.ids)
    return {"ok": True, **result}


@router.post("/diagnose", summary="5.1/S2-4 项目诊断（销售专家）")
async def diagnose(
    payload: AiDiagnoseRequest,
    user: User = Depends(require_lead_view),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    lead = await lead_service.get_lead(db, payload.lead_id)
    diagnosis = await ai_service.diagnose(db, user=user, lead=lead, question=payload.question)
    return ai_diagnosis_out(diagnosis)


@router.get("/diagnoses", summary="5.3 诊断报告历史")
async def list_diagnoses(
    lead_id: str | None = None,
    page: int = 1,
    page_size: int = 20,
    user: User = Depends(require_lead_view),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    rows, total = await ai_service.list_diagnoses(
        db, user=user, lead_id=lead_id, offset=(page - 1) * page_size, limit=page_size
    )
    return {
        "items": [ai_diagnosis_out(d) for d in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.post("/im-intake", summary="IM 归集预留接口（本期只落原始消息 + 生成草稿）")
async def im_intake(
    payload: ImIntakeRequest,
    user: User = Depends(require_lead_write),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    exists = (
        await db.execute(select(ImMessage).where(ImMessage.message_id == payload.message_id))
    ).scalar_one_or_none()
    if exists:
        return {"ok": True, "duplicated": True, "message_id": payload.message_id}

    # 先落原始消息：IM 归集的第一职责是"不丢消息"，解析失败也要留下原文
    message = ImMessage(
        platform=payload.platform,
        message_id=payload.message_id,
        group_id=payload.group_id,
        sender_id=payload.sender_id,
        sender_name=payload.sender_name,
        raw_text=payload.text,
        parse_status=IntakeParseStatus.UNPARSED,
        parse_result=None,
    )
    db.add(message)
    await db.flush()

    try:
        draft = await ai_service.parse_intake(
            db, user=user, payload=AiIntakeRequest(message=payload.text, channel="im")
        )
    except BizError as exc:
        # AI 不可用：原文已落库并标记 unparsed，明确失败；不留半成品（无草稿、无 lead_id）
        message.parse_status = IntakeParseStatus.UNPARSED
        message.parse_result = {"error": exc.message}
        await db.commit()
        raise

    message.parse_status = (
        IntakeParseStatus.SUCCESS if draft.lead_id else IntakeParseStatus.PENDING_SUPPLEMENT
    )
    message.parse_result = {"draft_id": draft.id, "summary": draft.summary}
    message.lead_id = draft.lead_id
    draft.im_message_id = message.id
    return {"ok": True, "duplicated": False, "draft": ai_draft_out(draft)}
