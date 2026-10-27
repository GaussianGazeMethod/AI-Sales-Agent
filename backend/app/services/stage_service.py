"""闭环二服务：环节标准卡、标准完成（有可核对的依据）、硬门槛流转、金额变更、风险。

这里是「真执行」机制的落点：勾选完成标准**必须有可核对的依据**，硬门槛未过**不允许流转**。

 业务规则：（原则「简化事、赋能人」）：
依据**不再要求专员手打**。优先顺序是
1. 调用方直接给出的 `evidence`（AI 判定结果或人工填写）；
2. `evidence` 为空时，宿主去**该商机真实跟进记录**里定位承载该标准的原文，自动带出；
3. 两条都拿不到 → 拒绝勾选（不允许无痕补勾：spec/03 §6.3 的「能力 vs 态度」判定要靠它）。
`toggle` 的自动带出只做**定位**（原文照抄），"是否达成"的语义判断仍由模型（N5）或人负责。

AI 录入代理确认后的写入也全部走本模块（保证校验一致）。
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import dicts
from app.core.errors import ConflictError, NotFoundError, PermissionDeniedError, ValidationError
from app.core.rules_version import risk_rules_digest
from app.core.sales_rules import (
    PROPOSAL_QUOTE_RULE,
    combine_proposal_quote,
    combo_rule_revision,
    condition_ids,
    drop_stale_conditions,
    fact_key,
    funding_confirmation_blocked,
    is_proposal_quote_standard,
    merge_condition_states,
    normalize_condition_entries,
    raw_condition_contract_error,
    sales_stage_days,
    states_from_facts,
)
from app.models.activity import FollowUp
from app.models.enums import DataSource, LeadStatus, RiskStatus, Stage, UserRole
from app.models.lead import Lead
from app.models.stage import (
    AmountChange,
    RiskAlert,
    StageCompletion,
    StageStandard,
    StageTransition,
)
from app.models.user import User
from app.services import audit_service

# ══════════════ 标准卡 ══════════════


async def list_standards(db: AsyncSession, stage: str | None = None) -> list[StageStandard]:
    stmt = select(StageStandard).where(StageStandard.is_active.is_(True))
    if stage:
        stmt = stmt.where(StageStandard.stage == stage)
    return list(
        (await db.execute(stmt.order_by(StageStandard.stage, StageStandard.sort_order)))
        .scalars()
        .all()
    )


_STAGE_STANDARD_REVISION_FIELDS = (
    "stage",
    "seq",
    "content",
    "is_hard_gate",
    "duration_days",
    "evidence_hint",
    "sort_order",
    "is_active",
)


def _stage_standard_revision(standard: StageStandard) -> dict:
    return {name: getattr(standard, name) for name in _STAGE_STANDARD_REVISION_FIELDS}


def _stage_standard_digest(revision: dict | None) -> str | None:
    if revision is None:
        return None
    canonical = json.dumps(revision, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _assert_stage_standard_admin(user: User) -> None:
    if user.role != UserRole.ADMIN:
        raise PermissionDeniedError("当前角色无权维护环节标准")


async def create_standard(
    db: AsyncSession, *, user: User, payload: dict
) -> StageStandard:
    """Create a standard and append its first readable revision in the same transaction."""
    _assert_stage_standard_admin(user)
    standard = StageStandard(
        stage=payload.get("stage", ""),
        seq=str(payload.get("seq", "1")),
        content=payload.get("content", ""),
        is_hard_gate=bool(payload.get("is_hard_gate", False)),
        duration_days=int(payload.get("duration_days", 0)),
        evidence_hint=payload.get("evidence_hint"),
        sort_order=int(payload.get("sort_order", 0)),
    )
    db.add(standard)
    await db.flush()
    after = _stage_standard_revision(standard)
    await audit_service.write_log(
        db,
        operator=user,
        action="stage_standard_created",
        target_type="stage_standard",
        target_id=standard.id,
        target_name=standard.content,
        detail=json.dumps(
            {
                "schema": "stage-standard-revision/v1",
                "before": None,
                "after": after,
                "before_sha256": None,
                "after_sha256": _stage_standard_digest(after),
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
    )
    return standard


async def update_standard(
    db: AsyncSession, *, user: User, standard: StageStandard, payload: dict
) -> StageStandard:
    """Append a before/after record only when a whitelisted field really changes."""
    _assert_stage_standard_admin(user)
    before = _stage_standard_revision(standard)
    for key in (
        "seq",
        "content",
        "is_hard_gate",
        "duration_days",
        "evidence_hint",
        "sort_order",
        "is_active",
    ):
        if key in payload and payload[key] is not None:
            setattr(standard, key, str(payload[key]) if key == "seq" else payload[key])
    after = _stage_standard_revision(standard)
    if before != after:
        await db.flush()
        await audit_service.write_log(
            db,
            operator=user,
            action="stage_standard_updated",
            target_type="stage_standard",
            target_id=standard.id,
            target_name=standard.content,
            detail=json.dumps(
                {
                    "schema": "stage-standard-revision/v1",
                    "before": before,
                    "after": after,
                    "before_sha256": _stage_standard_digest(before),
                    "after_sha256": _stage_standard_digest(after),
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
        )
    return standard


async def ensure_completions(db: AsyncSession, lead: Lead) -> list[StageCompletion]:
    """确保**当前环节**的标准完成记录已初始化（进入环节时自动建）。"""
    if not lead.current_stage:
        return []
    return await ensure_completions_for_stage(db, lead, lead.current_stage)


async def ensure_completions_for_stage(
    db: AsyncSession, lead: Lead, stage: str
) -> list[StageCompletion]:
    """确保**指定环节**的标准完成记录已初始化（按 standard_id 对齐，缺哪条建哪条）。

    与 `ensure_completions` 同一套数据、同一套语义，只是把「当前环节」换成任意环节：
    历史环节的记录在它当时是当前环节时就已建好；**没进入过的环节**在这里补建
    （全部 `is_done=False`），这样前端点未来环节也能看到"该环节已配置的标准"，
    不会出现"点进去是空的、不知道要做什么"。
    """
    standards = await list_standards(db, stage)
    existing = await list_completions_for_stage(db, lead, stage)
    by_standard = {c.standard_id: c for c in existing}

    for std in standards:
        if std.id in by_standard:
            continue
        row = StageCompletion(
            lead_id=lead.id,
            stage=std.stage,
            standard_id=std.id,
            standard_content=std.content,
            is_done=False,
        )
        db.add(row)
        by_standard[std.id] = row
    await db.flush()
    return [by_standard[s.id] for s in standards]


async def stage_progress(db: AsyncSession, lead: Lead) -> dict:
    """当前环节进度：标准清单 + 勾选状态 + 硬门槛 + 停留天数。"""
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

    completions = await ensure_completions(db, lead)
    standards = {s.id: s for s in await list_standards(db, lead.current_stage)}

    items = []
    missing: list[str] = []
    for c in completions:
        std = standards.get(c.standard_id)
        if std is None:
            continue
        items.append({"standard": std, "completion": c})
        if std.is_hard_gate and not c.is_done:
            missing.append(std.content)
    items.sort(key=lambda x: x["standard"].sort_order)

    stay_days = sales_stage_days(lead)
    base_days = dicts.duration_of(lead.current_stage)

    return {
        "stage": lead.current_stage,
        "stage_label": dicts.label_of(dicts.STAGE_LABELS, lead.current_stage),
        "standards": items,
        "hard_gate_passed": not missing,
        "missing_hard_gates": missing,
        "stay_days": stay_days,
        "base_days": base_days,
        "overdue": bool(
            lead.status != LeadStatus.SIGNED and base_days and stay_days is not None
            and stay_days > base_days
        ),
        "done_count": sum(1 for i in items if i["completion"].is_done),
        "total_count": len(items),
    }


# ══════════════ 指定环节的进度与依据（点步骤条看历史/未来环节） ══════════════


def _stage_entered_at(
    lead: Lead, stage: str, transitions: list[StageTransition], *, is_current: bool
) -> datetime | None:
    """进入该环节的时间。

    当前环节优先用 `lead.stage_entered_at`（它记录的是**本次**进入时间，
    环节做过"回退再进"时比流转记录准）；历史环节退回最近一次「流入」记录。
    """
    if is_current and lead.stage_entered_at:
        return lead.stage_entered_at
    inbound = [t.transition_at for t in transitions if t.to_stage == stage]
    return max(inbound) if inbound else None


def _stage_left_at(
    stage: str, transitions: list[StageTransition], entered_at: datetime | None
) -> datetime | None:
    """离开该环节的时间：进入之后**最早**一次「流出」记录（当前环节没有离开时间）。"""
    outbound = [t.transition_at for t in transitions if t.from_stage == stage]
    if entered_at is not None:
        outbound = [t for t in outbound if t >= entered_at]
    return min(outbound) if outbound else None


async def stage_detail(db: AsyncSession, *, lead: Lead, stage: str) -> dict:
    """**指定环节**的进度与依据。

    与 `stage_progress` 同口径、同一套数据，只把「当前环节」换成传入的 `stage`：
    - 历史环节：返回当时勾选的标准 + **达成依据原文**（`completion.evidence`）；
    - 未进入过的环节：标准清单照常返回，`completion.is_done=False`、`entered_at=null`
      （前端据此显示"该环节还没开始"）；
    - 本函数**只读**：不勾选、不推进环节，也**不建** `biz_stage_completion` 空行
      （那是「进入环节」的动作；只读展示用临时对象合成"未完成"即可，
      避免"点一下未来环节就在库里留下一行"）。
    """
    if dicts.stage_index(stage) < 0:
        raise ValidationError(f"环节不存在：{stage}")

    standards = await list_standards(db, stage)
    existing = {c.standard_id: c for c in await list_completions_for_stage(db, lead, stage)}

    items = []
    missing: list[str] = []
    for std in standards:
        completion = existing.get(std.id)
        if completion is None:
            # 还没进入过这个环节 → 合成一条「未完成」记录（不落库）
            completion = StageCompletion(
                lead_id=lead.id,
                stage=stage,
                standard_id=std.id,
                standard_content=std.content,
                is_done=False,
                source="",
            )
        items.append({"standard": std, "completion": completion})
        if std.is_hard_gate and not completion.is_done:
            missing.append(std.content)
    items.sort(key=lambda x: x["standard"].sort_order)

    transitions = list(
        (
            await db.execute(
                select(StageTransition)
                .where(
                    StageTransition.lead_id == lead.id,
                    or_(StageTransition.to_stage == stage, StageTransition.from_stage == stage),
                )
                .order_by(StageTransition.transition_at.desc())
            )
        )
        .scalars()
        .all()
    )

    is_current = lead.current_stage == stage
    entered_at = _stage_entered_at(lead, stage, transitions, is_current=is_current)
    left_at = None if is_current else _stage_left_at(stage, transitions, entered_at)
    if is_current and lead.status == LeadStatus.SIGNED:
        left_at = datetime.combine(lead.sign_date, datetime.min.time()) if lead.sign_date else None
        stay_days = sales_stage_days(lead)
    elif entered_at is None:
        stay_days = 0 if dicts.stage_index(stage) > dicts.stage_index(lead.current_stage) else None
    elif left_at is not None:
        stay_days = max((left_at.date() - entered_at.date()).days, 0)
    else:
        stay_days = max((datetime.now() - entered_at).days, 0)
    base_days = dicts.duration_of(stage)

    return {
        "stage": stage,
        "stage_label": dicts.label_of(dicts.STAGE_LABELS, stage),
        "is_current": is_current,
        "entered_at": entered_at,
        "left_at": left_at,
        "stay_days": stay_days,
        "base_days": base_days,
        "overdue": bool(
            not (is_current and lead.status == LeadStatus.SIGNED)
            and base_days and stay_days is not None and stay_days > base_days
        ),
        "standards": items,
        "hard_gate_passed": not missing,
        "missing_hard_gates": missing,
        "done_count": sum(1 for i in items if i["completion"].is_done),
        "total_count": len(items),
        "transitions": transitions,
    }


# ══════════════ 引文核对：不允许编证据 ══════════════
#
# 业务规则：原话（底线）：「evidence_quote 必须是该商机真实跟进记录里的原文片段」。
# 因此模型给出的引文一律由宿主**逐字**核对；核对不过就判不达成，绝不替它圆场。

#: 引文最短可核对长度（去掉空白后）。太短的片段（如「确认」）在任何文本里都能找到，不算依据。
MIN_QUOTE_CHARS = 4

#: 自动带出依据时，"内容重合度"至少要命中几个片段才算定位到（见 standard_fragments）。
MIN_FRAGMENT_SCORE = 2

_WS_RE = re.compile(r"\s+")
_FRAGMENT_RE = re.compile(r"[A-Za-z0-9]{2,}|[\u4e00-\u9fff]+")


def normalize_text(text: str | None) -> str:
    """去掉所有空白字符，便于逐字核对（模型偶尔会多加空格/换行，不影响是否"原文"）。"""
    return _WS_RE.sub("", text or "")


def quote_in_text(quote: str | None, text: str | None) -> bool:
    """引文是否**逐字**出现在原文里（去空白后子串比对；过短的引文不认）。"""
    normalized_quote = normalize_text(quote)
    if len(normalized_quote) < MIN_QUOTE_CHARS:
        return False
    return normalized_quote in normalize_text(text)


def standard_fragments(content: str | None) -> set[str]:
    """把标准内容切成可比较片段（中文二元组 + 英文/数字词），用于**候选定位**。

     只用于回答「哪条跟进可能写到了这件事」，**不判定是否达成** ——
    达成与否是语义判断，只能由模型（N5）或人负责（Agently-Skills：不得用宿主规则替代模型语义）。
    """
    fragments: set[str] = set()
    for token in _FRAGMENT_RE.findall(content or ""):
        if token.isascii():
            fragments.add(token.lower())
            continue
        if len(token) == 1:
            continue
        fragments.update(token[i : i + 2] for i in range(len(token) - 1))
    return fragments


def _fragment_score(content: str | None, fragments: set[str]) -> int:
    return len(fragments & standard_fragments(content))


async def list_done_standard_ids(db: AsyncSession, lead: Lead) -> set[str]:
    """**只读**取该商机跨阶段已达成的标准 id 集合（不建行、不 flush）。

    `list_completions` 只看当前环节，助手路径需要「不重查已达成项」跨阶段成立：
    阶段推进后，前阶段已达成的标准同样不能被重复下发或覆盖原依据（Spec20 §3.2/§7.3）。
    """
    if not lead.current_stage:
        return set()
    return {
        c.standard_id
        for c in (
            await db.execute(
                select(StageCompletion).where(
                    StageCompletion.lead_id == lead.id,
                    StageCompletion.is_done.is_(True),
                )
            )
        )
        .scalars()
        .all()
    }


async def list_completions(db: AsyncSession, lead: Lead) -> list[StageCompletion]:
    """**只读**取当前环节的标准完成记录（不建行、不 flush）。

    用于「只判定、不写库」的自动判定接口：它绝不能顺手改数据。
    """
    if not lead.current_stage:
        return []
    return await list_completions_for_stage(db, lead, lead.current_stage)


async def list_completions_for_stage(
    db: AsyncSession, lead: Lead, stage: str
) -> list[StageCompletion]:
    """**只读**取指定环节的标准完成记录（不建行、不 flush）。"""
    return list(
        (
            await db.execute(
                select(StageCompletion).where(
                    StageCompletion.lead_id == lead.id,
                    StageCompletion.stage == stage,
                )
            )
        )
        .scalars()
        .all()
    )


async def list_lead_followups(db: AsyncSession, lead_id: str) -> list[FollowUp]:
    """该商机的跟进记录（最近在前）。"""
    return list(
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


async def find_evidence_followup(
    db: AsyncSession, *, lead: Lead, standard: StageStandard
) -> FollowUp | None:
    """为某条标准定位**唯一**一条承载它的跟进记录（拿不到就返回 None）。

    顺序（都是宿主侧的非语义动作：找记录 → 照抄原文）：
    1. 显式关联：`FollowUp.related_standard_id == standard.id`（AI 录入时挂上的关联）；
    2. 内容重合：与标准内容重合片段最多的一条；**必须唯一最高分**且达到下限，
       否则视为无法唯一匹配（宁可不勾，也不要拿一条不相干的跟进充数）。
    """
    rows = await list_lead_followups(db, lead.id)
    if not rows:
        return None

    linked = [f for f in rows if f.related_standard_id == standard.id]
    if linked:
        return linked[0]

    fragments = standard_fragments(standard.content)
    if not fragments:
        return None
    scored = [(score, f) for f in rows if (score := _fragment_score(f.content, fragments)) > 0]
    if not scored:
        return None
    best = max(score for score, _ in scored)
    if best < MIN_FRAGMENT_SCORE:
        return None
    top = [f for score, f in scored if score == best]
    return top[0] if len(top) == 1 else None


# ══════════════ 标准勾选（依据可自动带出） ══════════════


def _is_prior_stage(stage: str, current: str) -> bool:
    """按阶段顺序判断 stage 是否就在 current 之前（纯结构比较，不看文案关键词）。"""
    try:
        return dicts.STAGE_ORDER.index(stage) < dicts.STAGE_ORDER.index(current)
    except ValueError:
        return False


async def toggle_standard(
    db: AsyncSession,
    *,
    user: User | None,
    lead: Lead,
    standard_id: str,
    is_done: bool,
    evidence: str | None = None,
    remark: str | None = None,
    source: str = "manual",
    operator_name: str | None = None,
    allow_prior_stage: bool = False,
) -> StageCompletion:
    """勾选/取消完成标准。

    勾选**必须有可核对的依据**（谁 + 何时 + 做了什么 + 有无确认人）—— 这是解决
    "能力 vs 态度"的核心机制，不能无痕补勾。但依据**不需要上传凭证、也不需要手打**：
    业务规则：/10-10 更正为「用市场专员反馈的结构化跟进信息来抽取判定」，
    因此 `evidence` 缺省时宿主会去**真实跟进记录**里定位承载该标准的原文并自动带出；
    一条都定位不到才拒绝（提示先用跟进记录写清，或用『按跟进信息自动判定』）。
    """
    if not lead.current_stage:
        raise ConflictError("该线索尚未进入闭环二环节")

    standard = (
        await db.execute(select(StageStandard).where(StageStandard.id == standard_id))
    ).scalar_one_or_none()
    if standard is None:
        raise NotFoundError("完成标准不存在")
    backfill_prior = False
    if standard.stage != lead.current_stage:
        # 默认仍只允许当前环节（人工界面与其他调用方口径不变）。助手反馈路径可显式
        # 请求补记**前阶段尚未达成**的标准（Spec20 §3.2）：只写该项的达成与来源，
        # 不回退阶段、不重判已达成交项、也不改门槛属性。
        backfill_prior = (
            allow_prior_stage and is_done and _is_prior_stage(standard.stage, lead.current_stage)
        )
        if not backfill_prior:
            raise ConflictError("只能勾选当前环节的完成标准")
        done_before = (
            await db.execute(
                select(StageCompletion).where(
                    StageCompletion.lead_id == lead.id,
                    StageCompletion.standard_id == standard_id,
                    StageCompletion.is_done.is_(True),
                )
            )
        ).scalar_one_or_none()
        if done_before is not None:
            raise ConflictError("该标准已有达成依据，前阶段补记不重判也不覆盖原依据")

    if is_done and not (evidence or "").strip():
        matched = await find_evidence_followup(db, lead=lead, standard=standard)
        if matched is None:
            raise ValidationError(
                "没有可依据的跟进信息：请先在跟进记录里写清这件事，或用『按跟进信息自动判定』"
            )
        evidence = matched.content
        remark = remark or (
            f"依据跟进信息自动带出（{matched.followed_at:%Y-%m-%d}，"
            f"{dicts.label_of(dicts.FOLLOW_METHOD_LABELS, matched.method)}）"
        )

    completions = (
        # 前阶段补记时按该阶段对齐记录（缺哪条建哪条），不碰当前阶段的任何行。
        await ensure_completions_for_stage(db, lead, standard.stage)
        if backfill_prior
        else await ensure_completions(db, lead)
    )
    target = next((c for c in completions if c.standard_id == standard_id), None)
    if target is None:
        raise NotFoundError("标准完成记录不存在")

    target.is_done = is_done
    target.evidence = evidence if is_done else target.evidence
    target.remark = remark or target.remark
    if is_done:
        target.done_by = user.id if user else None
        target.done_by_name = operator_name or (user.name if user else "")
        target.done_at = datetime.now()
    target.source = source

    if user:
        await audit_service.write_log(
            db,
            operator=user,
            action="standard_toggle",
            target_type="lead",
            target_id=lead.id,
            target_name=lead.name,
            detail=(
                f"{'勾选' if is_done else '取消'}标准「{standard.content}」；"
                f"依据：{(evidence or '')[:80]}"
            ),
        )
    return target


# ══════════════ 采纳 AI 判定结果（auto-apply） ══════════════


async def auto_apply_standards(
    db: AsyncSession,
    *,
    user: User,
    lead: Lead,
    items: list[dict],
    operator_name: str | None = None,
) -> dict:
    """把「按跟进信息自动判定」的结果落库（ 用户不需要手打证据）。

    逐条**重新校验**（不信任前端回传的判定结果）：
    - 标准必须属于该商机**当前环节**；
    - `followup_id` 必须存在、且属于该商机；未给 id 时用引文在跟进记录里**唯一**定位；
    - `evidence_quote` 必须**逐字**出现在那条跟进里（防止前端伪造/篡改）。

    任一条校验不过 → 整批 422（原子，不做"部分落库"）：宁可让人看清问题重来，
    也不要留下"一半勾了、一半没勾"却没人知道的结果。校验全过才写库，
    `evidence = evidence_quote`、`source = ai_auto`，并写 `standard_auto_apply` 审计日志。
    """
    if not lead.current_stage:
        raise ConflictError("该线索尚未进入闭环二环节")
    if not items:
        raise ValidationError("auto-apply 至少需要一条判定结果")

    resolved: list[tuple[StageStandard, FollowUp, str]] = []
    problems: list[str] = []
    condition_states: dict[str, tuple[list, dict]] = {}
    # T38入口同口径：自动采纳与助手路径共用经费硬门槛的档案事实校验。
    # 人工勾选(toggle_standard)保留人工确认权威，不在此拦。
    from app.models.dossier import ProjectDossier

    auto_dossier = (
        (
            await db.execute(
                select(ProjectDossier).where(ProjectDossier.lead_id == lead.id)
            )
        )
        .scalars()
        .first()
    )

    for index, item in enumerate(items, start=1):
        standard_id = str((item or {}).get("standard_id") or "").strip()
        quote = str((item or {}).get("evidence_quote") or "").strip()
        followup_id = str((item or {}).get("followup_id") or "").strip()

        if not standard_id:
            problems.append(f"第 {index} 条：缺少 standard_id")
            continue
        standard = (
            await db.execute(select(StageStandard).where(StageStandard.id == standard_id))
        ).scalar_one_or_none()
        if standard is None:
            problems.append(f"第 {index} 条：完成标准不存在（{standard_id}）")
            continue
        if standard.stage != lead.current_stage:
            problems.append(f"「{standard.content}」不属于当前环节，不能勾选")
            continue
        if (
            standard.stage == "s4_budget_confirmed"
            and standard.is_hard_gate
            and auto_dossier is not None
            and funding_confirmation_blocked(auto_dossier.facts)
        ):
            problems.append(
                f"「{standard.content}」：项目档案已明确资金不可用或分期签约安排未确认，"
                "不能自动采纳经费硬门槛（与助手跟进路径同口径，事实更新后可重新判定）"
            )
            continue
        if not quote:
            problems.append(f"「{standard.content}」：缺少可核对的引文（evidence_quote）")
            continue

        follow: FollowUp | None = None
        if followup_id:
            follow = (
                await db.execute(select(FollowUp).where(FollowUp.id == followup_id))
            ).scalar_one_or_none()
            if follow is None or follow.lead_id != lead.id:
                problems.append(f"「{standard.content}」：跟进记录不存在或不属于该商机")
                continue
            if not quote_in_text(quote, follow.content):
                problems.append(
                    f"「{standard.content}」：引文无法核对 —— 该文字不存在于所选跟进记录中"
                )
                continue
        else:
            candidates = [
                f for f in await list_lead_followups(db, lead.id) if quote_in_text(quote, f.content)
            ]
            if len(candidates) != 1:
                problems.append(
                    f"「{standard.content}」：未给出 followup_id，且该引文在跟进记录里"
                    f"{'找不到' if not candidates else '不唯一'}，无法核对"
                )
                continue
            follow = candidates[0]

        if is_proposal_quote_standard(standard.stage, standard.seq):
            # 固定双条件：整项达成只认共享规则的 AND；每个条件的原话必须能在该商机的
            # 跟进里逐字唯一定位（不同条件允许引用不同消息），模型自报总布尔不作依据。
            from app.services import dossier_service as _dossier_service

            lead_followups = await list_lead_followups(db, lead.id)
            raw_error = raw_condition_contract_error(item.get("conditions"))
            if raw_error:
                # ：归一化之前先验原始集合，非法就是非法，不"取首个"修好；
                # 合法缺项（未提及）与合法 unknown 不在此列，仍由下面的累计口径处理。
                problems.append(f"「{standard.content}」：{raw_error}")
                continue
            entries = normalize_condition_entries(item.get("conditions"))
            archive_states = states_from_facts(auto_dossier.facts if auto_dossier else None)
            # 与助手入口同一时序口径——档案各条件的现行水位来自
            # `proof_effective_time`，本轮候选时间取该条件**实际定位到的那条跟进**的业务时间。
            prior_times = _dossier_service.condition_prior_times(auto_dossier)
            candidate_times = {}
            for entry in entries:
                if entry["state"] == "unknown":
                    continue
                if not entry["quote"]:
                    # 本轮没有新原话：只沿用档案中确立时已逐字核验过的状态，不接受凭空声称。
                    entry["state"] = archive_states.get(entry["condition_id"], "unknown")
                    continue
                located = [f for f in lead_followups if quote_in_text(entry["quote"], f.content)]
                if len(located) != 1:
                    problems.append(
                        f"「{standard.content}」：条件 {entry['condition_id']} 的原话在该商机的"
                        "跟进里无法逐字唯一定位，该条件按未确立处理，不能计入达成"
                    )
                    entry.update(state="unknown", quote="", source_key="")
                    continue
                entry["followup_id"] = located[0].id
                if located[0].followed_at is not None:
                    candidate_times[entry["condition_id"]] = (
                        located[0].followed_at.isoformat()
                    )
            given = {e["condition_id"] for e in entries}
            missing = [cid for cid in condition_ids() if cid not in given]
            for cid in missing:
                # 模型本轮没给的固定条件：按档案累计状态如实列出，不假装本轮有原话。
                entries.append(
                    {
                        "condition_id": cid,
                        "state": archive_states.get(cid, "unknown"),
                        "quote": "",
                        "source_key": "",
                    }
                )
            gated = drop_stale_conditions(
                {e["condition_id"]: e["state"] for e in entries},
                candidate_times,
                prior_times,
                archive_states,
            )
            for entry in entries:
                state = gated.get(entry["condition_id"], entry["state"])
                if state != entry["state"]:
                    problems.append(
                        f"「{standard.content}」：条件 {entry['condition_id']} 引用的业务时间"
                        f"（{candidate_times.get(entry['condition_id'])}）早于档案现行状态的"
                        "时序水位，事后补录不解除更新的事实，该条件按档案状态处理"
                    )
                    entry.update(state=state, quote="", source_key="", followup_id=None)
            merged = merge_condition_states(
                archive_states, {e["condition_id"]: e["state"] for e in entries}
            )
            if not combine_proposal_quote(merged):
                missing = "、".join(
                    f"{c['id']}（{c['label']}）"
                    for c in PROPOSAL_QUOTE_RULE["conditions"]
                    if merged.get(c["id"]) != "completed"
                )
                problems.append(
                    f"「{standard.content}」：按共享双条件规则（修订 {combo_rule_revision()}）"
                    f"组合为未达成，尚缺：{missing}"
                )
                continue
            condition_states[standard.id] = (entries, merged)

        resolved.append((standard, follow, quote))

    if problems:
        raise ValidationError("自动采纳未通过校验：" + "；".join(problems))

    prepared: list[tuple[StageStandard, FollowUp, str]] = []
    for standard, follow, quote in resolved:
        if standard.id in {x.id for x, _, _ in prepared}:
            raise ValidationError(f"「{standard.content}」重复提交")
        prepared.append((standard, follow, quote))

    results: list[dict] = []
    for standard, follow, quote in prepared:
        entries = condition_states.get(standard.id, ([], {}))[0]
        citation_parts = []
        for e in entries:
            bit = f"{e['condition_id']}={e['state']}"
            if e.get("quote"):
                bit += f"：{e['quote']}"
            else:
                bit += "（项目档案累计来源，确立时已逐字核验）"
            citation_parts.append(bit)
        citations = "；".join(citation_parts)
        remark = (
            f"按跟进信息自动判定并采纳（跟进 {follow.followed_at:%Y-%m-%d}；"
            "判定由 AI 给出，引用已逐字核对）"
        )
        if citations:
            remark = f"{remark} 分项来源：{citations} 组合=AND 规则修订={combo_rule_revision()}"
        completion = await toggle_standard(
            db,
            user=user,
            lead=lead,
            standard_id=standard.id,
            is_done=True,
            evidence=quote,
            remark=remark,
            source=DataSource.AI_AUTO.value,
            operator_name=operator_name,
        )
        if entries:
            from app.services import dossier_service

            await dossier_service.record_condition_states(
                db,
                lead=lead,
                user=user,
                entries=[
                    {
                        "key": fact_key(e["condition_id"]),
                        "state": e["state"],
                        "quote": e["quote"],
                        # 各条件按自己定位到的跟进存来源；沿用档案累计的
                        # 条件没有新原话，写入层会跳过它，其原来源因此不被改写。
                        "followup_id": e.get("followup_id"),
                    }
                    for e in entries
                ],
                followup=follow,
                rule_revision=combo_rule_revision(),
            )
        results.append(
            {
                "standard_id": standard.id,
                "content": standard.content,
                "is_done": completion.is_done,
                "evidence": completion.evidence,
                "source": completion.source,
                "done_by_name": completion.done_by_name,
                "done_at": completion.done_at.isoformat() if completion.done_at else None,
                "followup_id": follow.id,
                "followup_at": follow.followed_at.isoformat() if follow.followed_at else None,
                # 采纳回执把每个固定条件的状态、原话与它自己的来源跟进/业务时间结构化返回，
                # 前端与审计都能看到「分项从哪条消息来」，不只在 remark 里拼一段文字（R03）。
                "remark": completion.remark,
                "condition_sources": [
                    {
                        "condition_id": e["condition_id"],
                        "state": e["state"],
                        "quote": e.get("quote") or None,
                        "followup_id": e.get("followup_id"),
                        "state_source": e.get("state_source")
                        or ("本轮逐字原文" if e.get("quote") else "项目档案累计"),
                    }
                    for e in entries
                ]
                or None,
                "rule_revision": combo_rule_revision() if entries else None,
                "applied": True,
                "reason": "已按跟进信息自动判定并采纳",
            }
        )

    await audit_service.write_log(
        db,
        operator=user,
        action="standard_auto_apply",
        target_type="lead",
        target_id=lead.id,
        target_name=lead.name,
        detail=(
            "按跟进信息自动判定并采纳：勾选 " + "；".join(f"「{r['content']}」" for r in results)
        ),
    )
    return {"applied_count": len(results), "rejected_count": 0, "items": results}


# ══════════════ 环节流转（硬门槛） ══════════════


async def advance_stage(
    db: AsyncSession,
    *,
    user: User,
    lead: Lead,
    basis: str | None = None,
    remark: str | None = None,
) -> Lead:
    """推进到下一环节：硬门槛未过直接拒绝，并提示缺哪条。"""
    if lead.status in (LeadStatus.SIGNED, LeadStatus.LOST):
        raise ConflictError("已签约或已丢单项目不能推进环节")
    if not lead.current_stage:
        raise ConflictError("该线索尚未进入闭环二环节")

    progress = await stage_progress(db, lead)
    if not progress["hard_gate_passed"]:
        raise ConflictError(
            "硬门槛未通过，不能流转。缺失：" + "；".join(progress["missing_hard_gates"])
        )

    next_stage_value = dicts.next_stage(lead.current_stage)
    if next_stage_value is None:
        raise ConflictError("已是最后一个环节")

    stay_days = progress["stay_days"]
    from_stage = lead.current_stage

    lead.current_stage = next_stage_value
    lead.stage_entered_at = datetime.now()
    lead.win_probability = dicts.win_probability_of(next_stage_value)

    db.add(
        StageTransition(
            lead_id=lead.id,
            from_stage=from_stage,
            to_stage=next_stage_value,
            transition_at=datetime.now(),
            operator_id=user.id,
            operator_name=user.name,
            stay_days=stay_days,
            basis=basis,
            remark=remark,
        )
    )

    await ensure_completions(db, lead)

    await audit_service.write_log(
        db,
        operator=user,
        action="stage_advance",
        target_type="lead",
        target_id=lead.id,
        target_name=lead.name,
        detail=f"环节 {dicts.label_of(dicts.STAGE_LABELS, from_stage)} → "
        f"{dicts.label_of(dicts.STAGE_LABELS, next_stage_value)}（停留 {stay_days} 天）",
    )
    return lead


async def rollback_stage(
    db: AsyncSession,
    *,
    user: User,
    lead: Lead,
    basis: str | None = None,
    remark: str | None = None,
) -> Lead:
    """回退到上一环节（客户需求反复 / 决策链回退等），同样留痕。"""
    if lead.status in (LeadStatus.SIGNED, LeadStatus.LOST):
        raise ConflictError("已签约或已丢单项目不能回退环节")
    if not lead.current_stage:
        raise ConflictError("该线索尚未进入闭环二环节")
    idx = dicts.stage_index(lead.current_stage)
    if idx <= 0:
        raise ConflictError("已在第一个环节，无法回退")

    from_stage = lead.current_stage
    to_stage = dicts.STAGE_ORDER[idx - 1]
    stay_days = (
        max((datetime.now() - lead.stage_entered_at).days, 0) if lead.stage_entered_at else 0
    )

    lead.current_stage = to_stage
    lead.stage_entered_at = datetime.now()
    lead.win_probability = dicts.win_probability_of(to_stage)

    db.add(
        StageTransition(
            lead_id=lead.id,
            from_stage=from_stage,
            to_stage=to_stage,
            transition_at=datetime.now(),
            operator_id=user.id,
            operator_name=user.name,
            stay_days=stay_days,
            basis=basis,
            remark=remark or "环节回退",
        )
    )
    await audit_service.write_log(
        db,
        operator=user,
        action="stage_rollback",
        target_type="lead",
        target_id=lead.id,
        target_name=lead.name,
        detail=f"环节回退 {dicts.label_of(dicts.STAGE_LABELS, from_stage)} → "
        f"{dicts.label_of(dicts.STAGE_LABELS, to_stage)}",
    )
    return lead


async def sign_contract(
    db: AsyncSession,
    *,
    user: User,
    lead: Lead,
    contract_amount_fen: int | None = None,
    sign_date=None,
) -> Lead:
    """环节七完成 → 已签约。【编排 O2】商机 → 交付的唯一入口（闭环三本轮只落状态）。"""
    if lead.status == LeadStatus.SIGNED:
        raise ConflictError("该项目已签约，请勿重复提交")
    if lead.current_stage != Stage.S7_CONTRACT_SIGNED:
        raise ConflictError("只有在环节七（合同签约）才能完成签约")
    progress = await stage_progress(db, lead)
    if not progress["hard_gate_passed"]:
        raise ConflictError("硬门槛未通过：" + "；".join(progress["missing_hard_gates"]))

    lead.status = LeadStatus.SIGNED
    lead.sign_date = sign_date or datetime.now().date()
    lead.deliver_handoff_status = "pending"
    if contract_amount_fen:
        await change_amount(
            db,
            user=user,
            lead=lead,
            amount_fen=contract_amount_fen,
            reason="合同签约金额确认",
        )
        lead.contract_amount_fen = contract_amount_fen
    confirmed_contract_amount = contract_amount_fen or lead.contract_amount_fen or None
    from app.services import dossier_service

    await dossier_service.sync_signed_contract(
        db,
        user=user,
        lead=lead,
        amount_fen=confirmed_contract_amount,
        sign_date=lead.sign_date,
    )
    lead.win_probability = 1.0

    await audit_service.write_log(
        db,
        operator=user,
        action="sign_contract",
        target_type="lead",
        target_id=lead.id,
        target_name=lead.name,
        detail="环节七完成，状态=已签约，进入闭环三交付交接",
    )

    # 【编排 O2】商机 → 交付（唯一一次真正的交接）：建合同 + 交付物清单 + 交接单 + 10 阶段
    from app.services import delivery_service

    await delivery_service.contract_from_signed_lead(db, lead=lead, operator=user)
    lead.deliver_handoff_status = "handed_over"
    from app.services import risk_service

    await risk_service.close_signed_timing_risks(db)

    # 通知运营交付部（闭环三接手）
    from app.models.enums import UserRole

    delivery_ids = (
        (
            await db.execute(
                select(User.id).where(
                    User.role.in_([UserRole.DELIVERY_MANAGER, UserRole.DELIVERY_STAFF]),
                    User.is_active.is_(True),
                )
            )
        )
        .scalars()
        .all()
    )
    await audit_service.notify_many(
        db,
        user_ids=list(delivery_ids),
        title="新签约客户待交接",
        content=f"{lead.name} 已签约，请接收交付交接单",
        type_="contract_signed",
        event="contract_signed",
        context={
            "lead_name": lead.name,
            "amount": f"{(lead.contract_amount_fen or 0) / 1000000:.2f}",
        },
        biz_type="lead",
        biz_id=lead.id,
    )
    return lead


# ══════════════ 金额滚动修正（留痕） ══════════════


async def change_amount(
    db: AsyncSession,
    *,
    user: User | None,
    lead: Lead,
    amount_fen: int,
    reason: str,
    source: str = "manual",
    operator_name: str | None = None,
) -> AmountChange | None:
    """改金额**必须填依据**；同时更新成交概率与加权预测。"""
    if not reason or not reason.strip():
        raise ValidationError("修改金额必须填写变更依据")

    before = lead.budget_amount_fen or 0
    after = int(amount_fen)
    if before == after:
        return None

    lead.budget_amount_fen = after
    lead.win_probability = dicts.win_probability_of(lead.current_stage)

    change = AmountChange(
        lead_id=lead.id,
        before_fen=before,
        after_fen=after,
        reason=reason.strip(),
        stage=lead.current_stage,
        operator_id=user.id if user else "",
        operator_name=operator_name or (user.name if user else ""),
        source=source,
    )
    db.add(change)
    await db.flush()

    if user:
        await audit_service.write_log(
            db,
            operator=user,
            action="amount_change",
            target_type="lead",
            target_id=lead.id,
            target_name=lead.name,
            detail=f"金额 {before / 100:.2f} → {after / 100:.2f} 元；依据：{reason[:80]}",
        )
    return change


async def list_amount_changes(db: AsyncSession, lead_id: str) -> list[AmountChange]:
    return list(
        (
            await db.execute(
                select(AmountChange)
                .where(AmountChange.lead_id == lead_id)
                # 最新在上；`id` 作为确定性 tie-break —— 同一秒内落库的多条留痕
                # （例如分阶段补录的两次金额调整）否则顺序会在两次请求间跳动
                .order_by(AmountChange.created_at.desc(), AmountChange.id.desc())
            )
        )
        .scalars()
        .all()
    )


# ══════════════ 风险 ══════════════


async def add_risks(
    db: AsyncSession,
    *,
    user: User | None,
    lead: Lead,
    risk_types: list[str],
    description: str = "",
    source: str = "manual",
    operator_name: str | None = None,
) -> list[RiskAlert]:
    """登记 R1–R5 风险（多选），并同步到主表 risk_types。"""
    from app.models.enums import RiskType

    valid = {r.value for r in RiskType}
    created: list[RiskAlert] = []
    current = set(lead.risk_types or [])

    for risk_type in risk_types:
        if risk_type not in valid:
            continue
        if risk_type == RiskType.R5_PROJECT_RHYTHM:
            current.discard(risk_type)
            from app.services import dossier_service

            dossier = await dossier_service.get(db, lead.id)
            await dossier_service.refresh_risks(db, lead=lead, dossier=dossier)
            matches = (await db.execute(select(RiskAlert).where(
                RiskAlert.lead_id == lead.id, RiskAlert.risk_type == risk_type,
                RiskAlert.status != RiskStatus.RESOLVED,
            ))).scalars().all()
            created.extend(matches)
            if matches:
                current.add(risk_type)
            continue
        current.add(risk_type)
        alert = RiskAlert(
            lead_id=lead.id,
            risk_type=risk_type,
            trigger_rule="人工/AI 识别",
            description=description or dicts.label_of(dicts.RISK_TYPE_LABELS, risk_type),
            status=RiskStatus.PENDING,
            source=source,
            rule_revision=risk_rules_digest(),
        )
        db.add(alert)
        created.append(alert)

    lead.risk_types = sorted(current)
    # flush 以便调用方立刻拿到 id（AI 草稿确认时要回写 apply_result）
    await db.flush()
    if created and user:
        await audit_service.write_log(
            db,
            operator=user,
            action="risk_add",
            target_type="lead",
            target_id=lead.id,
            target_name=lead.name,
            detail="识别风险："
            + "、".join(dicts.label_of(dicts.RISK_TYPE_LABELS, r) for r in risk_types),
        )
    return created


async def list_risks(db: AsyncSession, lead_id: str) -> list[RiskAlert]:
    return list(
        (
            await db.execute(
                select(RiskAlert)
                .where(RiskAlert.lead_id == lead_id, RiskAlert.domain == "sales")
                .order_by(RiskAlert.created_at.desc())
            )
        )
        .scalars()
        .all()
    )


async def update_risk(
    db: AsyncSession,
    *,
    user: User,
    risk: RiskAlert,
    status: str,
    handle_note: str | None = None,
) -> RiskAlert:
    risk.status = status
    risk.handle_note = handle_note
    risk.handler_id = user.id
    risk.handler_name = user.name
    risk.handled_at = datetime.now()
    return risk


async def list_transitions(db: AsyncSession, lead_id: str) -> list[StageTransition]:
    return list(
        (
            await db.execute(
                select(StageTransition)
                .where(StageTransition.lead_id == lead_id)
                .order_by(StageTransition.transition_at.desc())
            )
        )
        .scalars()
        .all()
    )
