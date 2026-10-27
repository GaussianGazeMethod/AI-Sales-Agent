"""闭环二接口：环节推进（硬门槛）、金额滚动、风险、签订。"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, require_admin, require_lead_view, require_lead_write
from app.core.errors import NotFoundError
from app.database import get_db
from app.models.stage import RiskAlert, StageStandard
from app.models.user import User
from app.schemas import (
    AmountChangeRequest,
    RiskCreateRequest,
    RiskUpdateRequest,
    SalesStageConfirmationRequest,
    StageAdvanceRequest,
    StandardAutoApplyRequest,
    StandardToggleRequest,
)
from app.serializers import (
    amount_change_out,
    followup_out,
    lead_out,
    leads_with_metadata,
    risk_out,
    stage_completion_out,
    stage_standard_out,
    stage_transition_out,
)
from app.services import (
    ai_service,
    dossier_service,
    followup_service,
    judgement_service,
    lead_service,
    stage_confirmation_service,
    stage_service,
)

router = APIRouter(tags=["闭环二 · 商机"])


def _progress_out(progress: dict) -> dict:
    return {
        "stage": progress["stage"],
        "stage_label": progress["stage_label"],
        "standards": [
            {
                "standard": stage_standard_out(item["standard"]),
                "completion": stage_completion_out(item["completion"]),
            }
            for item in progress["standards"]
        ],
        "hard_gate_passed": progress["hard_gate_passed"],
        "missing_hard_gates": progress["missing_hard_gates"],
        "stay_days": progress["stay_days"],
        "base_days": progress["base_days"],
        "overdue": progress["overdue"],
        "done_count": progress["done_count"],
        "total_count": progress["total_count"],
    }


def _stage_view_out(view: dict) -> dict:
    """指定环节进度（点步骤条看历史/未来环节）→ 接口载荷。"""
    return {
        "stage": view["stage"],
        "stage_label": view["stage_label"],
        "is_current": view["is_current"],
        "entered_at": view["entered_at"].isoformat() if view["entered_at"] else None,
        "left_at": view["left_at"].isoformat() if view["left_at"] else None,
        "stay_days": view["stay_days"],
        "base_days": view["base_days"],
        "overdue": view["overdue"],
        "done_count": view["done_count"],
        "total_count": view["total_count"],
        "hard_gate_passed": view["hard_gate_passed"],
        "missing_hard_gates": view["missing_hard_gates"],
        "standards": [
            {
                "standard": stage_standard_out(item["standard"]),
                "completion": stage_completion_out(item["completion"]),
            }
            for item in view["standards"]
        ],
        "transitions": [stage_transition_out(t) for t in view["transitions"]],
    }


@router.get("/opportunities/{lead_id}", summary="商机详情（环节进度/金额/风险/跟进）")
async def opportunity_detail(
    lead_id: str,
    user: User = Depends(require_lead_view),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    lead = await lead_service.get_lead(db, lead_id)
    # 归属校验：专员只能看本人负责的商机（与列表页数据范围、与新接口
    # stages/{stage}/progress 保持一致；否则"能看详情、看不了环节证据"自相矛盾，
    # 且专员可猜 id 读到别人的金额与风险）
    lead_service.assert_can_view(user, lead)
    progress = await stage_service.stage_progress(db, lead) if lead.current_stage else None
    return {
        "lead": (await leads_with_metadata(db, [lead]))[0],
        "stage_progress": _progress_out(progress) if progress else None,
        "amount_changes": [
            amount_change_out(a) for a in await stage_service.list_amount_changes(db, lead_id)
        ],
        "risks": [risk_out(r) for r in await stage_service.list_risks(db, lead_id)],
        "transitions": [
            stage_transition_out(t) for t in await stage_service.list_transitions(db, lead_id)
        ],
        "followups": [followup_out(f) for f in await followup_service.list_followups(db, lead_id)],
    }


@router.get(
    "/opportunities/{lead_id}/stages/{stage}/progress",
    summary="指定环节的进度与达成依据（点步骤条看已完成/未开始环节）",
)
async def stage_progress_detail(
    lead_id: str,
    stage: str,
    user: User = Depends(require_lead_view),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    lead = await lead_service.get_lead(db, lead_id)
    # 专员只能看本人负责的商机（管理人员不受限）→ 否则 403
    lead_service.assert_can_view(user, lead)
    # 环节 code 非法 → 422（ValidationError）；商机不存在 → 404
    view = await stage_service.stage_detail(db, lead=lead, stage=stage)
    return _stage_view_out(view)


@router.post(
    "/opportunities/{lead_id}/standards/{standard_id}/toggle",
    summary="勾选完成标准（依据可自动从跟进信息带出，无需手打证据）",
)
async def toggle_standard(
    lead_id: str,
    standard_id: str,
    payload: StandardToggleRequest,
    user: User = Depends(require_lead_write),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    lead = await lead_service.get_lead(db, lead_id)
    lead_service.assert_can_edit(user, lead)
    completion = await stage_service.toggle_standard(
        db,
        user=user,
        lead=lead,
        standard_id=standard_id,
        is_done=payload.is_done,
        evidence=payload.evidence,
        remark=payload.remark,
    )
    progress = await stage_service.stage_progress(db, lead)
    return {
        "completion": stage_completion_out(completion),
        "stage_progress": _progress_out(progress),
    }


# ══════════════ 按跟进信息自动判定 / 采纳══════════════
#
#  业务约束：「四 预算确认完成标准 0/2，不是由 AI 根据提交的进度信息来判断是否满足？
#   为什么还要提交证据；我们开发的原则『简化事、赋能人』」。
# 因此判定与采纳拆成两步：auto-judge **只判定不写库**（还要逐字核对引文），
# auto-apply 采纳后才落库（evidence 直接来自跟进原文，用户不需要手打）。
#
#  业务规则：（本轮的缺陷修复）：「项目阶段已显示到阶段四，为啥还需要人工
#   确认点击 AI 分析？」→ **触发全自动**：跟进写入成功后系统自动判一次并把结果落进
#   `biz_standard_judgement`（草稿）；专员进页面直接看到结果，只在不对时重判/忽略。
#   但「采纳落库」**保持人工确认**（spec/10 第 191 行）。


@router.post(
    "/opportunities/{lead_id}/standards/auto-judge",
    summary="按跟进信息自动判定环节标准（只判定、不写业务数据；结果落待确认草稿）",
)
async def auto_judge_standards(
    lead_id: str,
    payload: dict | None = None,  # noqa: ARG001 - 契约要求空请求体；显式声明便于前端传 {}
    user: User = Depends(require_lead_write),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    lead = await lead_service.get_lead(db, lead_id)
    lead_service.assert_can_edit(user, lead)
    result = await ai_service.auto_judge_standards(db, user=user, lead=lead)
    # 判定结果落库（同一商机只留最新一条）：手动「重新判定」也走这里覆盖旧结论
    judgement = await judgement_service.save_judgement(db, lead=lead, result=result)
    result["judgement_id"] = judgement.id
    dossier = await dossier_service.get(db, lead.id)
    result["dossier_revision"] = dossier.revision if dossier is not None else None
    return result


@router.post(
    "/opportunities/{lead_id}/standards/review-completed",
    summary="复核当前环节已完成标准的证据（不修改完成状态或判定草稿）",
)
async def review_completed_standards(
    lead_id: str,
    user: User = Depends(require_lead_write),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    lead = await lead_service.get_lead(db, lead_id)
    lead_service.assert_can_edit(user, lead)
    if lead.owner_id != user.id:
        from app.core.errors import PermissionDeniedError

        raise PermissionDeniedError("请由项目负责人复核完成依据")
    return await ai_service.auto_judge_standards(db, user=user, lead=lead, review_completed=True)


@router.get(
    "/opportunities/{lead_id}/standards/judgement",
    summary="读最新一次 AI 判定（含 stale：有新跟进还没判定过）",
)
async def get_standard_judgement(
    lead_id: str,
    user: User = Depends(require_lead_view),  # noqa: ARG001 - 只读接口，权限由依赖校验
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    lead = await lead_service.get_lead(db, lead_id)
    return await judgement_service.judgement_state(db, lead=lead)


@router.post(
    "/opportunities/{lead_id}/standards/auto-apply",
    summary="采纳自动判定结果并落库（引文重新核对，evidence 自动带出）",
)
async def auto_apply_standards(
    lead_id: str,
    payload: StandardAutoApplyRequest,
    user: User = Depends(require_lead_write),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    lead = await lead_service.get_lead(db, lead_id)
    lead_service.assert_can_edit(user, lead)
    result = await stage_service.auto_apply_standards(
        db,
        user=user,
        lead=lead,
        items=[item.model_dump() for item in payload.items],
    )
    # 采纳成功 → 对应判定置 accepted（草稿箱清空，避免"已生效还挂着待确认"）
    await judgement_service.mark_accepted(db, lead_id=lead.id)
    progress = await stage_service.stage_progress(db, lead)
    return {
        "items": result["items"],
        "applied_count": result["applied_count"],
        "rejected_count": result["rejected_count"],
        "stage_progress": _progress_out(progress),
    }


@router.post(
    "/opportunities/{lead_id}/standards/sales-confirm",
    summary="负责人明确确认阶段候选并选择性更正档案",
)
async def sales_confirm_standards(
    lead_id: str,
    payload: SalesStageConfirmationRequest,
    user: User = Depends(require_lead_write),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    lead = await lead_service.get_lead(db, lead_id)
    lead_service.assert_can_edit(user, lead)
    if lead.owner_id != user.id:
        from app.core.errors import PermissionDeniedError

        raise PermissionDeniedError("请由项目负责人本人确认当前阶段候选")
    result = await stage_confirmation_service.confirm_stage_candidates(
        db,
        user=user,
        lead=lead,
        request=payload.model_dump(mode="json"),
    )
    if result.get("idempotent_replay"):
        return {**result["response"], "idempotent_replay": True}
    progress = await stage_service.stage_progress(db, lead)
    result["stage_progress"] = _progress_out(progress)
    receipt = await stage_confirmation_service.get_confirmation(db, result["confirmation_id"])
    receipt.response = result
    return result


@router.post("/opportunities/{lead_id}/stage/advance", summary="推进环节（硬门槛校验）")
async def advance_stage(
    lead_id: str,
    payload: StageAdvanceRequest,
    user: User = Depends(require_lead_write),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    lead = await lead_service.get_lead(db, lead_id)
    lead_service.assert_can_edit(user, lead)
    await stage_service.advance_stage(
        db, user=user, lead=lead, basis=payload.basis, remark=payload.remark
    )
    progress = await stage_service.stage_progress(db, lead)
    return {"lead": lead_out(lead), "stage_progress": _progress_out(progress)}


@router.post("/opportunities/{lead_id}/stage/rollback", summary="回退环节")
async def rollback_stage(
    lead_id: str,
    payload: StageAdvanceRequest,
    user: User = Depends(require_lead_write),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    lead = await lead_service.get_lead(db, lead_id)
    lead_service.assert_can_edit(user, lead)
    await stage_service.rollback_stage(
        db, user=user, lead=lead, basis=payload.basis, remark=payload.remark
    )
    progress = await stage_service.stage_progress(db, lead)
    return {"lead": lead_out(lead), "stage_progress": _progress_out(progress)}


@router.post("/opportunities/{lead_id}/stage/sign", summary="环节七：完成签约")
async def sign_contract(
    lead_id: str,
    contract_amount_fen: int | None = Query(None, gt=0),
    sign_date: date | None = None,
    user: User = Depends(require_lead_write),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    lead = await lead_service.get_lead(db, lead_id)
    lead_service.assert_can_edit(user, lead)
    await stage_service.sign_contract(
        db, user=user, lead=lead, contract_amount_fen=contract_amount_fen, sign_date=sign_date
    )
    return {"lead": lead_out(lead)}


@router.post("/opportunities/{lead_id}/amount", summary="金额滚动修正（必须填依据）")
async def change_amount(
    lead_id: str,
    payload: AmountChangeRequest,
    user: User = Depends(require_lead_write),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    lead = await lead_service.get_lead(db, lead_id)
    change = await stage_service.change_amount(
        db, user=user, lead=lead, amount_fen=payload.amount_fen, reason=payload.reason
    )
    return {
        "lead": lead_out(lead),
        "change": amount_change_out(change) if change else None,
    }


@router.post("/opportunities/{lead_id}/risks", summary="登记 R1–R6 风险")
async def add_risks(
    lead_id: str,
    payload: RiskCreateRequest,
    user: User = Depends(require_lead_write),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    lead = await lead_service.get_lead(db, lead_id)
    rows = await stage_service.add_risks(
        db, user=user, lead=lead, risk_types=payload.risk_types, description=payload.description
    )
    return {"items": [risk_out(r) for r in rows], "total": len(rows), "lead": lead_out(lead)}


@router.patch("/opportunities/risks/{risk_id}", summary="处理风险")
async def update_risk(
    risk_id: str,
    payload: RiskUpdateRequest,
    user: User = Depends(require_lead_write),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    risk = (await db.execute(select(RiskAlert).where(RiskAlert.id == risk_id))).scalar_one_or_none()
    if risk is None:
        raise NotFoundError("风险记录不存在")
    await stage_service.update_risk(
        db, user=user, risk=risk, status=payload.status, handle_note=payload.handle_note
    )
    return risk_out(risk)


# ══════════════ 环节标准卡配置（6.5）══════════════


@router.get("/stage-standards", summary="环节标准卡列表")
async def list_stage_standards(
    stage: str | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    rows = await stage_service.list_standards(db, stage)
    return {"items": [stage_standard_out(s) for s in rows], "total": len(rows)}


@router.post("/stage-standards", summary="新增完成标准（管理员/市场部管理）")
async def create_stage_standard(
    payload: dict,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    row = await stage_service.create_standard(db, user=user, payload=payload)
    return stage_standard_out(row)


@router.patch("/stage-standards/{standard_id}", summary="编辑完成标准（管理员）")
async def update_stage_standard(
    standard_id: str,
    payload: dict,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    row = (
        await db.execute(select(StageStandard).where(StageStandard.id == standard_id))
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError("标准不存在")
    row = await stage_service.update_standard(db, user=user, standard=row, payload=payload)
    return stage_standard_out(row)


@router.get("/leads/{lead_id}/stage-progress", summary="当前环节进度（单独取用）")
async def stage_progress_only(
    lead_id: str,
    user: User = Depends(require_lead_view),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    lead = await lead_service.get_lead(db, lead_id)
    if not lead.current_stage:
        return {
            "stage": None,
            "stage_label": "",
            "standards": [],
            "hard_gate_passed": False,
            "missing_hard_gates": [],
            "stay_days": 0,
            "base_days": 0,
            "overdue": False,
            "done_count": 0,
            "total_count": 0,
        }
    progress = await stage_service.stage_progress(db, lead)
    return _progress_out(progress)
