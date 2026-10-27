"""AI 销售助手服务（SX-1 / S2-3 / S2-4）。

两项能力：
① **录入代理**：专员发一段结构化信息 → Agently 结构化抽取 → 生成更新草稿
   →  负责人确认后才落库
② **项目诊断**：按 7 环节标准 + 停留时长 + 风险模型输出缺失动作 / 建议 / 话术 / 下周计划

设计口径：
- 模型负责语义判断（客户归属、标准命中、风险判定、诊断结论），宿主负责枚举与键的成员校验、
  金额换算、身份拼接、权限与副作用。模型永不接触 UUID。
-  **不做规则降级**：未配置 key 或模型调用失败时直接抛 `AiUnavailableError`（503）。
  `skills/agently/SKILL.md` 反模式明令禁止用 mock、确定性逻辑或手写本地替代品替换
  模型负责的行为——"看起来还能用"的假结果比失败更糟。
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import date, datetime
from enum import Enum
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import assistant_analysis
from app.ai import keys as key_utils
from app.ai import requests as ai_requests
from app.ai.runtime import MODEL_DIAGNOSE, MODEL_EXTRACT, assert_ai_ready, model_name
from app.core import dicts
from app.core.deps import can_view_all_leads
from app.core.dicts import STAGE_STANDARD_SEED
from app.core.errors import (
    AiUnavailableError,
    BizError,
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
)
from app.core.sales_rules import (
    PROPOSAL_QUOTE_RULE,
    combine_proposal_quote,
    combo_rule_revision,
    drop_stale_conditions,
    is_proposal_quote_standard,
    merge_condition_states,
    normalize_condition_entries,
    raw_condition_contract_error,
    states_from_facts,
)
from app.models.ai import AiDiagnosis, AiDraft
from app.models.enums import (
    AiDraftStatus,
    AiParseMode,
    FollowMethod,
    ReminderType,
    RiskType,
    Stage,
    UserRole,
)
from app.models.lead import Lead
from app.models.stage import StageStandard
from app.models.user import User
from app.schemas import AiIntakeRequest
from app.services import (
    audit_service,
    dossier_service,
    followup_service,
    lead_service,
    stage_service,
)
from workflows.main_flow import run_intake_flow

logger = logging.getLogger(__name__)


async def _stage_standard_rows(db: AsyncSession, lead: Lead | None) -> list[StageStandard]:
    """取该客户**当前环节**的完成标准。

    只取当前环节：下发给模型的标准必须与它此刻所处环节一致，否则勾选会跨环节失真。
    无环节（还在闭环一）时取第一环节，供未定位客户时展示规则。
    """
    if lead is None:
        return []
    stage = lead.current_stage or Stage.S1_VALID_OPPORTUNITY
    return list(await stage_service.list_standards(db, stage))


def _follow_methods_payload() -> list[dict]:
    """跟进方式枚举（宿主定义，模型只能在其中选）。"""
    return [
        {"code": code, "label": dicts.label_of(dicts.FOLLOW_METHOD_LABELS, code)}
        for code in (
            FollowMethod.VISIT,
            FollowMethod.PHONE,
            FollowMethod.WECHAT,
            FollowMethod.ONLINE_MEETING,
        )
    ]


def _risk_catalog() -> dict[str, str]:
    """风险代码 R1–R5（宿主定义，模型只能在其中选）。"""
    return {risk.value: dicts.label_of(dicts.RISK_TYPE_LABELS, risk.value) for risk in RiskType}


def _stage_catalog() -> dict[str, dict]:
    """7 环节顺序、基准时长与成交概率（事实性材料，供模型对齐口径）。"""
    return {
        stage: {
            "label": dicts.label_of(dicts.STAGE_LABELS, stage),
            "duration_days": dicts.duration_of(stage),
            "win_probability": dicts.win_probability_of(stage),
        }
        for stage in dicts.STAGE_ORDER
    }


async def _candidate_leads(
    db: AsyncSession, *, user: User, explicit: Lead | None
) -> tuple[list[Lead], str]:
    """宿主取候选客户：该用户可见、未删除、按最近更新倒序、**有界**。

     这是非语义的有界取集（按时间），不是"猜客户"；语义判断属于模型（节点 N1）。
    有界就必须如实告知范围（写进 candidate_scope），不能假装匹配了全库。
    """
    if explicit is not None:
        return [explicit], "调用方已在界面指定客户，无需定位"

    stmt = select(Lead).where(Lead.is_deleted.is_(False))
    if not can_view_all_leads(user):
        stmt = stmt.where(Lead.owner_id == user.id)
    rows = list(
        (await db.execute(stmt.order_by(Lead.updated_at.desc()).limit(key_utils.CANDIDATE_LIMIT)))
        .scalars()
        .all()
    )
    scope = (
        f"该账号可见线索中最近更新的 {len(rows)} 条"
        f"（上限 {key_utils.CANDIDATE_LIMIT} 条，按更新时间倒序）"
    )
    return rows, scope


# ══════════════ 录入代理：生成草稿 ══════════════


INTAKE_SYSTEM_PROMPT = """你是「市场助手」AI 销售助手，负责把销售发来的一段拜访/沟通信息解析成结构化的更新草稿。

要求：
1. 只输出 JSON，不要任何解释文字。
2. 严格按给定 schema 输出：
{
  "lead_name": "客户名称或 null",
  "followup": {"method": "visit|phone|wechat|online_meeting", "content": "跟进内容摘要", "followed_at": "ISO时间或 null"},
  "matched_standard_ids": ["匹配到的完成标准 id"],
  "amount_fen": 金额（整数分，没有则 null）,
  "amount_reason": "金额变更依据（没有金额变更则空字符串）",
  "risks": ["r1_budget_unclear|r2_trust_missing|r3_decision_chain|r4_demand_unclear|r5_project_rhythm"],
  "advance_stage": false,
  "summary": "一句话回报给销售：更新了什么、有什么风险、请确认"
}
3. 只匹配上下文里给出的完成标准 id，不要编造。
4. 金额一律换算成"分"的整数（30 万元 = 30000000）。
5. risks 只从 5 个代码里选，判断依据：资金/预算不清→r1；信任不足→r2；决策链未打通（如"等校长定"）→r3；需求不清→r4；节奏/申报/挂网/封账→r5。
6. advance_stage 只在明确表示"该环节标准已全部完成、可以进入下一环节"时为 true。"""


def _host_summary(lead: Lead | None, result: dict, before_amount_fen: int | None) -> str:
    """宿主侧的事实性回报行（哪些被更新、金额变化、有哪些告警），与模型摘要拼在一起。

    这里只做格式化与事实陈述，不替代模型的语义结论。
    """
    parts: list[str] = []
    if lead:
        stage_label = dicts.label_of(dicts.STAGE_LABELS, lead.current_stage) or "闭环一（线索跟进）"
        parts.append(f"已定位 {lead.name}｜当前 {stage_label}")
    else:
        parts.append(" 未定位到客户，请先指定商机")

    followup = result.get("followup") or {}
    if followup.get("content"):
        parts.append(
            f"跟进记录：{dicts.label_of(dicts.FOLLOW_METHOD_LABELS, followup.get('method'))}"
        )

    count = len(result.get("standards") or [])
    if count:
        parts.append(f"勾选完成标准 {count} 条")

    amount_fen = result.get("amount_fen")
    if amount_fen and amount_fen != before_amount_fen:
        before = (before_amount_fen or 0) / 100 / 10000
        parts.append(f"金额 {before:.2f}万 → {amount_fen / 100 / 10000:.2f}万")

    if result.get("next_node") or result.get("next_node_time"):
        parts.append(f"下一节点：{result.get('next_node') or '待确认'}；预计时间：{result.get('next_node_time') or '待确认'}")

    risks = result.get("risk_types") or []
    if risks:
        parts.append("风险：" + "、".join(dicts.label_of(dicts.RISK_TYPE_LABELS, r) for r in risks))

    if result.get("warnings"):
        parts.append("注意：" + "；".join(result["warnings"]))

    model_summary = (result.get("summary") or "").strip()
    if model_summary:
        parts.append(model_summary)
    parts.append("请确认后生效")
    return "｜".join(parts)


def _json_safe(value):
    """把解析结果里的 datetime / Enum 递归转成可 JSON 序列化的值。

    AiDraft.payload 是 JSON 列，直接塞 datetime 会在 flush 时报
    "Object of type datetime is not JSON serializable"。
    """
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return value


async def parse_intake(db: AsyncSession, *, user: User, payload: AiIntakeRequest) -> AiDraft:
    """S2-3：结构化信息 → AI 生成更新草稿（**不直接改业务数据**）。

    流程：宿主取数 → TriggerFlow（N1 客户定位 → N2 更新项抽取 → H1 校验组装）
    → 宿主落草稿 → 人工确认后才写业务表。
    AI 不可用时抛 `AiUnavailableError`（503），**不做规则降级**。
    """
    assert_ai_ready()
    message = payload.message.strip()

    explicit_lead = await lead_service.get_lead(db, payload.lead_id) if payload.lead_id else None
    candidates, candidate_scope = await _candidate_leads(db, user=user, explicit=explicit_lead)
    candidate_visible, identity_map = key_utils.issue_candidate_keys(candidates)

    # 宿主预取每个候选**当前环节**的标准，并签发 S* 键（键→standard_id 只留在宿主侧）。
    #  必须以**候选键**为索引（不是 lead.id）：节点拿到的是候选键。
    candidate_standards: dict[str, list[dict]] = {}
    standard_map: dict[str, dict[str, str]] = {}
    for index, lead in enumerate(candidates, start=1):
        key = key_utils.candidate_key(index)
        if identity_map.get(key) != lead.id:  # 防漂移：签发与取数必须同源同序
            raise RuntimeError("候选键与线索映射不一致（宿主取数逻辑漂移）")
        rows = await _stage_standard_rows(db, lead)
        visible, mapping = key_utils.issue_standard_keys(rows)
        candidate_standards[key] = visible
        standard_map[key] = mapping

    facts: dict[str, Any] = {
        "message": message,
        "candidates": candidate_visible,
        "candidate_scope": candidate_scope,
        "candidate_standards": candidate_standards,
        "identity_map": identity_map,
        "standard_map": standard_map,
        "follow_methods": _follow_methods_payload(),
        "risk_catalog": _risk_catalog(),
        "explicit_lead_id": None,
        "explicit_key": None,
        "explicit_standards": None,
    }
    if explicit_lead is not None:
        explicit_rows = await _stage_standard_rows(db, explicit_lead)
        explicit_visible, explicit_mapping = key_utils.issue_standard_keys(explicit_rows)
        facts.update(
            {
                "explicit_lead_id": explicit_lead.id,
                "explicit_key": "C0",
                "explicit_name": explicit_lead.name,
                "explicit_city": explicit_lead.city or "",
                "explicit_stage_label": dicts.label_of(
                    dicts.STAGE_LABELS, explicit_lead.current_stage
                ),
                "explicit_standards": explicit_visible,
            }
        )
        identity_map["C0"] = explicit_lead.id
        standard_map["C0"] = explicit_mapping

    try:
        result = await run_intake_flow(facts)
    except AiUnavailableError:
        raise
    except Exception as exc:  # noqa: BLE001 - AI 层任何异常都归为"能力不可用"
        logger.exception("AI 录入流程失败")
        raise AiUnavailableError(f"AI 解析失败：{exc}") from exc

    lead: Lead | None = None
    if result.get("lead_id"):
        lead = await lead_service.get_lead(db, result["lead_id"])

    before_amount_fen = lead.budget_amount_fen if lead else None
    summary = _host_summary(lead, result, before_amount_fen)

    confidence = 1.0 if facts["explicit_lead_id"] else (0.9 if lead else 0.0)
    match_candidates = candidate_visible[:5]

    draft_payload = {
        "followup": result.get("followup"),
        "standards": result.get("standards") or [],
        "amount": (
            {
                "amount_fen": result.get("amount_fen"),
                "before_fen": before_amount_fen,
                "reason": result.get("amount_reason") or "AI 依据结构化信息中的预算描述修正",
            }
            if result.get("amount_fen")
            else None
        ),
        "risks": result.get("risk_types") or [],
        "schedule": (
            {"next_node": result.get("next_node"), "next_node_time": result.get("next_node_time")}
            if result.get("next_node") or result.get("next_node_time") else None
        ),
        "advance_stage": bool(result.get("advance_stage")),
        "lead_stage": lead.current_stage if lead else None,
        "resolution": {
            "source": result.get("resolution_source"),
            "reason": result.get("resolution_reason"),
            "candidate_scope": candidate_scope,
        },
        "warnings": result.get("warnings") or [],
    }

    draft = AiDraft(
        source_message=message,
        channel=payload.channel,
        lead_id=lead.id if lead else None,
        lead_name=lead.name if lead else None,
        match_confidence=confidence,
        match_candidates=match_candidates,
        summary=summary,
        payload=_json_safe(draft_payload),
        status=AiDraftStatus.PENDING.value,
        created_by=user.id,
        created_by_name=user.name,
        parse_mode=AiParseMode.LLM,
        model_used=model_name(MODEL_EXTRACT),
    )
    db.add(draft)
    await db.flush()

    from app.models.activity import Reminder

    db.add(
        Reminder(
            lead_id=lead.id if lead else None,
            lead_name=lead.name if lead else None,
            type=ReminderType.PENDING_CONFIRM,
            content=f"AI 更新草稿待确认：{summary[:120]}",
            remind_at=datetime.now(),
            receiver_id=user.id,
            receiver_name=user.name,
            dedup_key=f"ai_draft:{draft.id}",
        )
    )
    return draft


# ══════════════ 草稿列表 / 详情 ══════════════


async def list_drafts(
    db: AsyncSession,
    *,
    user: User,
    status: str | None = AiDraftStatus.PENDING,
    mine_only: bool = True,
    lead_id: str | None = None,
    offset: int = 0,
    limit: int = 20,
) -> tuple[list[AiDraft], int]:
    stmt = select(AiDraft)
    if status:
        stmt = stmt.where(AiDraft.status == status)
    # 按商机过滤：2.2 商机详情据此提示"AI 已抽到的时间，一键填入"
    if lead_id:
        stmt = stmt.where(AiDraft.lead_id == lead_id)
    if mine_only:
        stmt = stmt.where(AiDraft.created_by == user.id)
    total = int((await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar() or 0)
    rows = (
        (await db.execute(stmt.order_by(AiDraft.created_at.desc()).offset(offset).limit(limit)))
        .scalars()
        .all()
    )
    return list(rows), total


async def get_draft(db: AsyncSession, draft_id: str) -> AiDraft:
    draft = (await db.execute(select(AiDraft).where(AiDraft.id == draft_id))).scalar_one_or_none()
    if draft is None:
        raise NotFoundError("草稿不存在")
    return draft


# ══════════════ 录入代理：确认落库  ══════════════


def _assert_can_handle_draft(draft: AiDraft, user: User) -> None:
    """ 草稿必须由**创建人本人**确认或忽略（spec/06 §1.1「原负责人确认后才写入」）。

    实测踩过：确认接口只校验了 `require_lead_write`（有没有写权限），
    于是任意有写权限的人都能把**别人**的草稿确认落库 —— "负责人确认"形同虚设。
    管理员保留兜底权限（人员离职/异常时仍能处理）。
    """
    if draft.created_by == user.id:
        return
    if user.role == UserRole.ADMIN:
        return
    raise PermissionDeniedError("只有草稿创建人本人可以确认或忽略该草稿（管理员除外）")


async def confirm_draft(
    db: AsyncSession,
    *,
    user: User,
    draft: AiDraft,
    payload_override: dict | None = None,
    remark: str | None = None,
) -> dict:
    """ 只有项目负责人确认后才生效：把草稿写入正式业务表。"""
    _assert_can_handle_draft(draft, user)
    if draft.status != AiDraftStatus.PENDING:
        raise ConflictError("该草稿已处理过")

    payload = dict(draft.payload or {})
    if payload_override:
        payload.update({k: v for k, v in payload_override.items() if v is not None})

    lead_id = draft.lead_id or payload.get("lead_id")
    if not lead_id:
        raise ConflictError("未定位到客户，请先指定商机后再确认")
    lead = await lead_service.get_lead(db, lead_id)

    applied: dict = {
        "followup": None,
        "standards": [],
        "amount": None,
        "risks": [],
        "stage": None,
        "schedule": None,
    }
    notes: list[str] = []

    # ① 跟进记录
    follow = payload.get("followup") or {}
    if follow.get("content"):
        created = await followup_service.add_followup_from_ai(
            db,
            user=user,
            lead=lead,
            method=follow.get("method") or FollowMethod.WECHAT,
            content=follow.get("content"),
            followed_at=_parse_dt(follow.get("followed_at")),
        )
        applied["followup"] = created.id

    # ② 完成标准勾选（ 证据必填，不得伪造）
    #
    #  这里曾经写的是 `evidence=item.get("evidence") or draft.source_message[:200]`：
    #    模型没抽出证据时，宿主把**客户消息原文**当成"证据"写进去 —— 于是完成记录
    #    看上去"有证据"，其实是伪造的（原文只说明"发生过沟通"，不等于"标准已达成"）。
    #    正确行为（设计稿 05「资料不足时保持标准未完成」）：没有证据就不勾选，
    #    并把"缺什么"写进 notes，让人去 2.2 就地补证据或勾选。
    for item in payload.get("standards") or []:
        standard_id = item.get("standard_id")
        if not standard_id:
            continue
        evidence = str(item.get("evidence") or "").strip()
        if not evidence:
            notes.append(
                f"标准「{item.get('content') or standard_id}」**未勾选**："
                "草稿没有提供证据，而完成标准必须附证据（原文不能当证据）。"
                "请在商机详情「环节进度」里补证据后手动勾选，或在确认时填写证据。"
            )
            continue
        try:
            completion = await stage_service.toggle_standard(
                db,
                user=user,
                lead=lead,
                standard_id=standard_id,
                is_done=True,
                evidence=evidence,
                remark="AI 录入草稿确认",
                source="ai_confirmed",
            )
            applied["standards"].append(completion.id)
        except BizError as exc:
            notes.append(f"标准「{item.get('content') or standard_id}」未能勾选：{exc.message}")

    # ③ 金额滚动修正（必须带依据）
    amount = payload.get("amount") or {}
    if amount.get("amount_fen"):
        try:
            change = await stage_service.change_amount(
                db,
                user=user,
                lead=lead,
                amount_fen=int(amount["amount_fen"]),
                reason=amount.get("reason") or "AI 依据结构化信息中的预算描述修正",
                source="ai_confirmed",
            )
            applied["amount"] = change.id if change else None
        except BizError as exc:
            notes.append(f"金额未更新：{exc.message}")

    # ④ 风险识别
    risks = payload.get("risks") or []
    if risks:
        created_risks = await stage_service.add_risks(
            db,
            user=user,
            lead=lead,
            risk_types=risks,
            description=f"AI 从结构化信息识别：{draft.source_message[:200]}",
            source="ai_confirmed",
        )
        applied["risks"] = [r.id for r in created_risks]

    # ⑤ 审查确认的下一节点与时间统一进入项目档案，不再写旧日期字段。
    schedule = payload.get("schedule") or {}
    if set(schedule) - {"next_node", "next_node_time"}:
        raise ValidationError("时间安排必须使用下一关键节点及预计时间，请重新审查草稿")
    values = {k: schedule[k] for k in ("next_node", "next_node_time")
              if isinstance(schedule.get(k), str) and schedule[k].strip()}
    if values:
        from app.services import dossier_service

        stored = await dossier_service.get(db, lead.id)
        statement = "负责人确认项目安排：" + "；".join(
            ("下一关键节点" if k == "next_node" else "预计时间") + "：" + v
            for k, v in values.items()
        )
        dossier = await dossier_service.persist(
            db, user=user, lead=lead, task={},
            extracted={"updates": [{"key": k, "value": v, "quote": statement,
                                     "knowledge_state": "confirmed"} for k, v in values.items()]},
            message=statement, turn_id="draft-confirm:" + draft.id,
            revision=stored.revision if stored else None,
            record={"source": "pc_draft_confirmation", "draft_id": draft.id,
                    "actor_id": user.id, "original_message": draft.source_message},
            followup_id=applied["followup"],
        )
        await dossier_service.refresh_risks(db, lead=lead, dossier=dossier)
        applied["schedule"] = values

    # ⑥ 环节推进（仅在标准齐备、硬门槛通过时；否则只提示）
    if payload.get("advance_stage") and lead.current_stage:
        try:
            await stage_service.advance_stage(
                db, user=user, lead=lead, basis="AI 录入草稿确认：本环节标准已完成"
            )
            applied["stage"] = lead.current_stage
        except BizError as exc:
            notes.append(f"环节未推进：{exc.message}")

    draft.status = AiDraftStatus.CONFIRMED
    draft.confirmed_by = user.id
    draft.confirmed_by_name = user.name
    draft.confirmed_at = datetime.now()
    draft.apply_result = {"applied": applied, "notes": notes}

    await _close_pending_reminder(db, draft.id)

    await audit_service.write_log(
        db,
        operator=user,
        action="ai_draft_confirm",
        target_type="lead",
        target_id=lead.id,
        target_name=lead.name,
        detail=f"确认 AI 更新草稿：跟进={bool(applied['followup'])}，标准 {len(applied['standards'])} 条，"
        f"金额={'是' if applied['amount'] else '否'}，风险 {len(applied['risks'])} 条"
        + (f"；备注：{remark}" if remark else ""),
    )
    return {"draft": draft, "applied": applied, "notes": notes, "lead": lead}


async def ignore_draft(
    db: AsyncSession, *, user: User, draft: AiDraft, remark: str | None = None
) -> None:
    _assert_can_handle_draft(draft, user)
    if draft.status != AiDraftStatus.PENDING:
        raise ConflictError("该草稿已处理过")
    draft.status = AiDraftStatus.IGNORED
    draft.confirmed_by = user.id
    draft.confirmed_by_name = user.name
    draft.confirmed_at = datetime.now()
    await _close_pending_reminder(db, draft.id)
    await audit_service.write_log(
        db,
        operator=user,
        action="ai_draft_ignore",
        target_type="ai_draft",
        target_id=draft.id,
        target_name=draft.lead_name,
        detail=f"忽略 AI 草稿；备注：{remark or '无'}",
    )


async def confirm_many(db: AsyncSession, *, user: User, ids: list[str]) -> dict:
    """批量确认（逐条独立，失败不影响其他）。"""
    ok, failed = 0, []
    for draft_id in ids:
        try:
            draft = await get_draft(db, draft_id)
            await confirm_draft(db, user=user, draft=draft)
            ok += 1
        except BizError as exc:
            # 单条失败不影响整批：找不到 / 已处理 / 未定位客户都记入 failed
            failed.append({"id": draft_id, "message": exc.message})
    return {"confirmed": ok, "failed": failed}


async def _close_pending_reminder(db: AsyncSession, draft_id: str) -> None:
    from app.models.activity import Reminder
    from app.models.enums import ReminderStatus

    row = (
        await db.execute(select(Reminder).where(Reminder.dedup_key == f"ai_draft:{draft_id}"))
    ).scalar_one_or_none()
    if row and row.status == ReminderStatus.PENDING:
        row.status = ReminderStatus.DONE
        row.done_at = datetime.now()


def _parse_dt(value) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


# ══════════════ 项目诊断（S2-4）══════════════


def _diagnose_standards(progress: dict) -> list[dict]:
    """签发诊断用的 S* 标准键，并把完成情况附在模型可见载荷上。"""
    rows = [item["standard"] for item in progress["standards"]]
    visible, mapping = key_utils.issue_standard_keys(rows)
    completions = {item["standard"].id: item["completion"] for item in progress["standards"]}
    for item in visible:
        completion = completions.get(mapping[item["key"]])
        item["is_done"] = bool(completion.is_done) if completion else False
        item["evidence"] = (completion.evidence or "") if completion else ""
    return visible


async def diagnose(
    db: AsyncSession, *, user: User, lead: Lead, question: str | None = None
) -> AiDiagnosis:
    """S2-4/B5：PC 点击诊断与聊天唤起走**同一个 project-diagnosis Skill**。

    模型负责五项散文（同一栏目、同一证据键校验，spec/24 T31/T08）；宿主负责
    快照组装、派生富子列表（missing_actions 用硬门槛、risk_findings 用宿主
    在挂预警，满足 spec/06 C1「说清缺哪条、为什么」）、组装 Markdown 与落库。
    AI 不可用时抛 `AiUnavailableError`，**不做规则诊断降级**。
    """
    assert_ai_ready()
    progress = await stage_service.stage_progress(db, lead)
    followups = await followup_service.list_followups(db, lead.id)
    risks = await stage_service.list_risks(db, lead.id)

    standards = _diagnose_standards(progress)
    stage_label = (
        dicts.label_of(dicts.STAGE_LABELS, lead.current_stage)
        if lead.current_stage
        else "闭环一（尚未成为有效商机）"
    )

    # B5/T31（spec/23/24）：PC 点击与聊天唤起走**同一个 project-diagnosis Skill**、
    # 同一组五项栏目与同一证据键校验。富子列表改由**宿主**派生：
    # missing_actions 用已算好的硬门槛标准（spec/06 C1「说清缺哪条、为什么」），
    # risk_findings 用宿主在挂预警；模型只负责五项散文。旧 diagnose_project
    # 独立契约退役，原因记录于 spec/26 追加更正。
    snapshot = await dossier_service.snapshot(db, user, lead.id, compact=False)
    analysis_context = {
        **snapshot,
        "message": question or "专员在 PC 端点击诊断：请判断当前卡在哪、下一步做什么。",
        "now": datetime.now().isoformat(),
    }
    analysis_context["evidence"] = [e for e in analysis_context["evidence"] if e.get("content")]
    analysis_context["actions"] = [
        {k: v for k, v in a.items() if k != "id"} for a in analysis_context["actions"]
    ]

    try:
        analysis = await assistant_analysis.generate("project-diagnosis", analysis_context)
    except AiUnavailableError:
        raise
    except Exception as exc:  # noqa: BLE001 - AI 层异常统一归为“能力不可用”
        logger.exception("AI 项目诊断失败")
        raise AiUnavailableError(f"AI 诊断失败：{exc}") from exc

    sections = analysis["sections"]
    by_title = {s["title"]: (s.get("content") or "") for s in sections}

    def sentences(text: str) -> list[str]:
        return [part.strip() for part in re.split(r"[。；;]+", text) if part.strip()]

    stay_text = "停留天数未统计"
    if progress["stay_days"] is not None:
        stay_text = f"已停留 {progress['stay_days']} 天" + (
            f"（基准 {progress['base_days']} 天）" if progress["base_days"] else "（基准未设）"
        )
    lines = [f"## {lead.name} · {stage_label}", f"**当前状态**：{stay_text}"]
    for section in sections:
        lines += ["", f"### {section['title']}", section["content"]]
    if risks:
        lines += [
            "",
            "### 宿主在挂预警",
            "、".join(str(r.description or "") for r in risks[:3]),
        ]
    if followups:
        lines.append("")
        lines.append("### 附：最近跟进")
        for f in followups[:5]:
            method = dicts.label_of(dicts.FOLLOW_METHOD_LABELS, f.method)
            lines.append(
                f"- {f.followed_at.strftime('%Y-%m-%d') if f.followed_at else ''} "
                f"{method}：{f.content or ''}"
            )
    warnings = [str(w) for w in (analysis.get("unknowns") or [])]
    if warnings:
        lines += ["", ">  " + "；".join(warnings)]
    content = "\n".join(lines)

    standard_by_key = {s["key"]: s for s in standards}
    missing_actions = [
        {
            "standard_key": key,
            "why": (
                f"硬门槛「{standard_by_key.get(key, {}).get('content') or key}」"
                "尚未在当前项目的实际执行中达成。"
            ),
        }
        for key in progress["missing_hard_gates"]
    ]
    risk_findings = [
        {
            "risk_type": r.risk_type,
            "evidence": r.description or "",
            "status": r.status,
            "handle_note": r.handle_note or "",
        }
        for r in risks
        if r.risk_type
    ]

    report_trigger = "stuck_alert" if progress["overdue"] else "manual"
    previous_diagnoses = list(
        (
            await db.execute(
                select(AiDiagnosis)
                .where(
                    AiDiagnosis.lead_id == lead.id,
                    AiDiagnosis.trigger == report_trigger,
                )
                .order_by(AiDiagnosis.created_at, AiDiagnosis.id)
            )
        )
        .scalars()
        .all()
    )
    previous_diagnosis = previous_diagnoses[-1] if previous_diagnoses else None
    source_text = question or "专员在 PC 端点击诊断"
    basis = {
        "facts": analysis_context.get("facts") or {},
        "evidence": analysis_context.get("evidence") or [],
        "dossier_revision": snapshot.get("revision") or 0,
    }
    basis_sha256 = hashlib.sha256(
        json.dumps(basis, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()

    diagnosis = AiDiagnosis(
        lead_id=lead.id,
        lead_name=lead.name,
        stage=lead.current_stage,
        trigger=report_trigger,
        content=content,
        structured={
            "stay_days": progress["stay_days"],
            "base_days": progress["base_days"],
            "overdue": progress["overdue"],
            "missing_hard_gates": progress["missing_hard_gates"],
            "done_count": progress["done_count"],
            "total_count": progress["total_count"],
            "sections": sections,
            "report_skill": "project-diagnosis",
            "rule_provenance": analysis.get("rule_provenance"),
            "requirements_status": analysis.get("requirements_status"),
            "unknowns": analysis.get("unknowns") or [],
            "evidence": analysis_context["evidence"],
            "dossier_revision": snapshot.get("revision") or 0,
            "analysis_version": len(previous_diagnoses) + 1,
            "supersedes_report_id": previous_diagnosis.id if previous_diagnosis else None,
            "source_message_sha256": hashlib.sha256(
                source_text.encode("utf-8")
            ).hexdigest(),
            "analysis_basis_sha256": basis_sha256,
            "stage_diagnosis": by_title.get("项目现状") or "",
            "missing_actions": missing_actions,
            "risk_findings": risk_findings,
            "suggested_actions": sentences(by_title.get("下一步行动") or ""),
            "talk_tracks": sentences(by_title.get("推进策略") or ""),
            "next_week_plan": [],
            "warnings": warnings,
            "standards": standards,
        },
        created_by=user.id,
        created_by_name=user.name,
        parse_mode=AiParseMode.LLM,
        model_used=model_name(MODEL_EXTRACT),
    )
    db.add(diagnosis)
    await db.flush()

    await audit_service.write_log(
        db,
        operator=user,
        action="ai_diagnose",
        target_type="lead",
        target_id=lead.id,
        target_name=lead.name,
        detail=f"AI 诊断（Agently · {model_name(MODEL_EXTRACT)}）",
    )
    return diagnosis


# ══════════════ 环节标准自动判定（N5：只判定、不写库）══════════════
#
#  业务规则：「环节完成标准应该由 AI 从市场专员已提交的结构化跟进信息里
#   抽取判定」，专员不再手工登记一遍证据（原则「简化事、赋能人」）。
#
# 归属：模型负责"跟进里到底写没写这件事"的语义判断；宿主负责
#   ① 只下发该商机**当前环节**未完成的标准与该商机的真实跟进；
#   ② **逐字核对**引文（quote_in_text）——核对不过一律 satisfied=false（不允许编证据，底线）；
#   ③ 键 → UUID 拼接；本接口**不写任何业务表**（采纳落库走 auto-apply）。

#: 单次判定下发给模型的跟进条数上界（最近 N 条）。
#: 有界就必须如实告知范围（写进 followup_scope），不假装读了全部跟进。
JUDGE_FOLLOWUP_LIMIT = 20


def _judge_followups(followups: list) -> tuple[list[dict], dict[str, Any]]:
    """跟进记录 → (模型可见 `F1..Fn`, 宿主侧 key→FollowUp)。模型永不接触 UUID。"""
    visible: list[dict] = []
    mapping: dict[str, Any] = {}
    for index, follow in enumerate(followups[:JUDGE_FOLLOWUP_LIMIT], start=1):
        key = f"F{index}"
        mapping[key] = follow
        visible.append(
            {
                "key": key,
                "followed_at": follow.followed_at.strftime("%Y-%m-%d")
                if follow.followed_at
                else "",
                "method_label": dicts.label_of(dicts.FOLLOW_METHOD_LABELS, follow.method),
                "content": follow.content or "",
            }
        )
    return visible, mapping


def _seq_int(seq: str | None) -> int | str:
    """标准序号：能转整数的转整数（前端按数字用），否则原样返回。"""
    text = str(seq or "").strip()
    return int(text) if text.isdigit() else text


async def auto_judge_standards(
    db: AsyncSession, *, user: User, lead: Lead, review_completed: bool = False
) -> dict:
    """S2-4 扩展：按**真实跟进信息**逐条判定当前环节完成标准（**只判定、不写库**）。

    - 已完成的标注准进 `already_done`，只把未完成的发给模型判定；
    - `hard_gate_passed` / `missing_hard_gates` 是**当前库里的真实状态**（本接口不改数据），
      采纳（auto-apply）之后才会变；
    - AI 不可用/解析失败 → 抛 `AiUnavailableError`（503），**不做规则降级**。
    """
    assert_ai_ready()
    if not lead.current_stage:
        raise ConflictError("该线索尚未进入闭环二环节")

    standards = await stage_service.list_standards(db, lead.current_stage)
    completions = {c.standard_id: c for c in await stage_service.list_completions(db, lead)}
    done_ids = [s.id for s in standards if completions.get(s.id) and completions[s.id].is_done]
    pending = [s for s in standards if (s.id in set(done_ids)) == review_completed]
    missing_hard_gates = [
        s.content
        for s in standards
        if s.is_hard_gate and not (completions.get(s.id) and completions[s.id].is_done)
    ]

    all_followups = await stage_service.list_lead_followups(db, lead.id)
    followups = all_followups[:JUDGE_FOLLOWUP_LIMIT]
    followup_visible, followup_map = _judge_followups(followups)
    standard_visible, standard_map = key_utils.issue_standard_keys(pending)
    standard_by_id = {s.id: s for s in pending}
    standards_by_key = {
        item["key"]: standard_by_id[standard_map[item["key"]]] for item in standard_visible
    }

    stage_label = dicts.label_of(dicts.STAGE_LABELS, lead.current_stage)
    followup_scope = (
        f"最近 {len(followups)} 条跟进（该商机共 {len(all_followups)} 条，按跟进时间倒序）"
    )
    facts: dict[str, Any] = {
        "customer": {
            "name": lead.name,
            "project_name": lead.project_name or "",
            "city": f"{lead.province or ''}{lead.city or ''}{lead.district or ''}",
            "org_type_label": dicts.label_of(dicts.ORG_TYPE_LABELS, lead.org_type),
        },
        "stage": {
            "key": lead.current_stage,
            "label": stage_label,
            "stay_days": (
                max((datetime.now() - lead.stage_entered_at).days, 0)
                if lead.stage_entered_at
                else 0
            ),
            "base_days": dicts.duration_of(lead.current_stage),
        },
        "standards": standard_visible,
        "followups": followup_visible,
        "today": date.today().isoformat(),
        "followup_scope": followup_scope,
    }

    if not pending:
        return {
            "stage": lead.current_stage,
            "stage_label": stage_label,
            "judged_at": datetime.now().isoformat(timespec="seconds"),
            "model_used": model_name(MODEL_DIAGNOSE),
            "parse_mode": AiParseMode.LLM.value,
            "items": [],
            "already_done": done_ids,
            "hard_gate_passed": not missing_hard_gates,
            "missing_hard_gates": missing_hard_gates,
            "followup_scope": followup_scope,
            "note": "当前环节没有已完成项可复核"
            if review_completed
            else "当前环节的完成标准都已完成，无需判定",
        }

    try:
        structured = await ai_requests.judge_stage_standards(facts)
    except AiUnavailableError:
        raise
    except Exception as exc:  # noqa: BLE001 - AI 层任何异常都归为"能力不可用"
        logger.exception("AI 环节标准判定失败")
        raise AiUnavailableError(f"AI 判定失败：{exc}") from exc

    judged: dict[str, dict] = {item["standard_key"]: item for item in structured.get("items") or []}
    warnings: list[str] = list(structured.get("warnings") or [])
    items: list[dict] = []
    # 固定双条件的累计状态来自项目档案（与助手入口同一存储、同一规则修订），本轮结果再并入。
    from app.models.dossier import ProjectDossier

    _dossier = (
        (await db.execute(select(ProjectDossier).where(ProjectDossier.lead_id == lead.id)))
        .scalars()
        .first()
    )
    accumulated_states = states_from_facts(_dossier.facts if _dossier is not None else None)
    condition_revision = combo_rule_revision()

    for visible in standard_visible:
        key = visible["key"]
        standard = standards_by_key[key]
        judgement = judged.get(key)
        if judgement is None:
            # 模型漏判：如实报"未判定"，不替它猜（更不得当成已达成）
            items.append(
                {
                    "standard_id": standard.id,
                    "seq": _seq_int(standard.seq),
                    "content": standard.content,
                    "is_hard_gate": bool(standard.is_hard_gate),
                    "evidence_hint": standard.evidence_hint,
                    "satisfied": False,
                    "confidence": 0.0,
                    "evidence_quote": None,
                    "followup_id": None,
                    "followup_at": None,
                    "reason": "模型未对这条标准给出判定，请重试或人工核对",
                }
            )
            continue

        satisfied = bool(judgement.get("satisfied"))
        confidence = float(judgement.get("confidence") or 0.0)
        quote = judgement.get("evidence_quote")
        reason = judgement.get("reason") or ""
        follow = followup_map.get(judgement.get("followup_key") or "")

        if is_proposal_quote_standard(standard.stage, standard.seq):
            # 整项达成一律由共享 AND 在「档案累计 + 本轮已逐字核验」的条件状态上计算；
            # 模型自报的 satisfied 总值不作为执行依据（引用逐字可核≠语义成立，语义仍归模型）。
            raw_error = raw_condition_contract_error(judgement.get("conditions"))
            if raw_error:
                # ：与 PC 回传、助手契约同一口径——重复/非法 ID/非法状态
                # 在归一化之前拒绝本项，不做"取首个 + 降级 unknown + 档案补齐"的静默修复。
                warnings.append(f"{raw_error}；本项不采纳")
                continue
            entries = normalize_condition_entries(judgement.get("conditions"))
            from app.services import dossier_service as _dossier_service

            # 档案各条件的现行时序水位 + 本轮各条件**实际定位到的**跟进时间，
            # 与助手入口共用同一口径判断事后补录，不给"一处拒绝旧事实、另一处照它勾选"留空间。
            prior_times = _dossier_service.condition_prior_times(_dossier)
            candidate_times = {}
            for entry in entries:
                src = followup_map.get(entry.get("source_key") or "")
                text = entry.get("quote") or ""
                if not text:
                    # 本轮没有新原话：只沿用档案中已逐字核验过的状态，不接受凭空声称。
                    entry["state"] = accumulated_states.get(
                        str(entry.get("condition_id")), "unknown"
                    )
                    continue
                if src is not None and stage_service.quote_in_text(text, src.content):
                    if src.followed_at is not None:
                        candidate_times[str(entry.get("condition_id"))] = (
                            src.followed_at.isoformat()
                        )
                    continue
                if text:
                    warnings.append(
                        f"条件 {entry.get('condition_id')} 的引文在所选跟进中无法逐字核对，"
                        "该条件按未确立处理"
                    )
                entry.update(state="unknown", quote="", source_key="")
            this_round = {
                str(e.get("condition_id")): str(e.get("state"))
                for e in entries
                if e.get("condition_id")
            }
            this_round = drop_stale_conditions(
                this_round, candidate_times, prior_times, accumulated_states
            )
            for entry in entries:
                gated = this_round.get(str(entry.get("condition_id")))
                if gated is not None and gated != entry.get("state"):
                    warnings.append(
                        f"条件 {entry.get('condition_id')} 引用的业务时间早于档案现行状态的时序"
                        "水位，事后补录不解除更新的事实，该条件按档案状态处理"
                    )
                    entry.update(state=gated, quote="", source_key="")
            merged = merge_condition_states(accumulated_states, this_round)
            present = {str(e.get("condition_id")) for e in entries}
            for cond_def in PROPOSAL_QUOTE_RULE["conditions"]:
                cid = cond_def["id"]
                if cid in present:
                    continue
                # 模型漏给某个固定条件时，用档案累计状态补全展示，不凭空当成 unknown。
                entries.append(
                    {
                        "condition_id": cid,
                        "state": merged.get(cid, "unknown"),
                        "quote": "",
                        "source_key": "",
                        "state_source": "项目档案累计",
                    }
                )
            for entry in entries:
                cid = str(entry.get("condition_id"))
                if entry.get("state") != merged.get(cid):
                    entry["state_source"] = "项目档案累计"
                    entry["state"] = merged.get(cid, "unknown")
                elif not entry.get("state_source"):
                    entry["state_source"] = "本轮跟进核验"
            host_satisfied = combine_proposal_quote(merged)
            if host_satisfied != bool(satisfied):
                warnings.append(
                    f"模型自报整项结果（{bool(satisfied)}）与共享规则 AND 组合"
                    f"（{host_satisfied}）不一致，按规则组合执行；规则修订 {condition_revision}"
                )
            satisfied = host_satisfied
            decisive = next(
                (
                    e
                    for e in entries
                    if e.get("state") == "completed"
                    and e.get("quote")
                    and followup_map.get(e.get("source_key") or "") is not None
                ),
                None,
            )
            if satisfied and decisive is None:
                satisfied = False
                warnings.append("累计条件已齐但本轮无可定位的分项原话，本次不勾选（规则组合）")
            confidence = confidence if satisfied else 0.0
            quote = decisive["quote"] if (satisfied and decisive) else None
            follow = (
                followup_map.get(decisive.get("source_key") or "")
                if (satisfied and decisive)
                else None
            )
            if not satisfied:
                reason = f"{reason}（按共享双条件规则与项目档案累计组合为未达成）"
            items.append(
                {
                    "standard_id": standard.id,
                    "seq": _seq_int(standard.seq),
                    "content": standard.content,
                    "is_hard_gate": bool(standard.is_hard_gate),
                    "evidence_hint": standard.evidence_hint,
                    "satisfied": satisfied,
                    "confidence": confidence,
                    "evidence_quote": quote,
                    "followup_id": follow.id if follow is not None else None,
                    "followup_at": (
                        follow.followed_at.isoformat()
                        if (follow is not None and follow.followed_at)
                        else None
                    ),
                    "reason": reason,
                    "conditions": entries,
                    "condition_states": merged,
                    "rule_revision": condition_revision,
                }
            )
            continue

        if satisfied:
            if follow is None:
                satisfied = False
                confidence = 0.0
                reason = f"引用无法核对：未给出引文所在的跟进记录。{reason}".strip()
                quote = None
            elif not stage_service.quote_in_text(quote, follow.content):
                satisfied = False
                confidence = 0.0
                reason = (
                    f"引用无法核对：给出的引文不存在于所选跟进记录中，判定不成立。{reason}"
                ).strip()
                quote = None
        else:
            # 未达成时不带引文（避免前端误用未经核对的文字）
            quote = None
            follow = None

        items.append(
            {
                "standard_id": standard.id,
                "seq": _seq_int(standard.seq),
                "content": standard.content,
                "is_hard_gate": bool(standard.is_hard_gate),
                "evidence_hint": standard.evidence_hint,
                "satisfied": satisfied,
                "confidence": confidence,
                "evidence_quote": quote if satisfied else None,
                "followup_id": follow.id if (satisfied and follow is not None) else None,
                "followup_at": (
                    follow.followed_at.isoformat()
                    if (satisfied and follow is not None and follow.followed_at)
                    else None
                ),
                "reason": reason,
            }
        )

    if review_completed:
        for item in items:
            completion = completions[item["standard_id"]]
            item["current_evidence"] = completion.evidence
            item["current_done_at"] = completion.done_at.isoformat() if completion.done_at else None

    return {
        "condition_rule_revision": condition_revision,
        "stage": lead.current_stage,
        "stage_label": stage_label,
        "judged_at": datetime.now().isoformat(timespec="seconds"),
        "model_used": model_name(MODEL_DIAGNOSE),
        "parse_mode": AiParseMode.LLM.value,
        "items": items,
        "already_done": done_ids,
        # 当前库里的**真实**硬门槛状态（本接口不改数据；采纳后才变）
        "hard_gate_passed": not missing_hard_gates,
        "missing_hard_gates": missing_hard_gates,
        "followup_scope": facts["followup_scope"],
        "warnings": warnings,
    }


async def list_diagnoses(
    db: AsyncSession, *, user: User, lead_id: str | None = None, offset: int = 0, limit: int = 20
) -> tuple[list[AiDiagnosis], int]:
    stmt = select(AiDiagnosis)
    if lead_id:
        stmt = stmt.where(AiDiagnosis.lead_id == lead_id)
    elif user.role not in {"admin", "market_manager", "executive"}:
        stmt = stmt.where(AiDiagnosis.created_by == user.id)
    total = int((await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar() or 0)
    rows = (
        (await db.execute(stmt.order_by(AiDiagnosis.created_at.desc()).offset(offset).limit(limit)))
        .scalars()
        .all()
    )
    return list(rows), total


def seed_standards_payload() -> list[dict]:
    """标准卡种子数据（供 seed 脚本使用）。"""
    return [
        {
            "stage": stage,
            "seq": seq,
            "content": content,
            "is_hard_gate": is_hard_gate,
            "evidence_hint": hint,
        }
        for stage, seq, content, is_hard_gate, hint in STAGE_STANDARD_SEED
    ]


__all__ = [
    "auto_judge_standards",
    "confirm_draft",
    "confirm_many",
    "diagnose",
    "get_draft",
    "ignore_draft",
    "list_diagnoses",
    "list_drafts",
    "parse_intake",
    "seed_standards_payload",
]
