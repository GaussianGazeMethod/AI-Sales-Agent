"""事实提取后的独立阶段基准比对。模型判语义，宿主校验原文/键并按共享规则组合。"""

import logging
from typing import Literal

from pydantic import BaseModel, Field

from app.ai.assistant_context import stage_input
from app.ai.contracts import ConditionJudgement
from app.ai.runtime import MODEL_EXTRACT, run_structured
from app.core.sales_rules import (
    PROPOSAL_QUOTE_RULE,
    combine_proposal_quote,
    condition_ids,
    drop_stale_conditions,
    funding_confirmation_blocked,
    is_proposal_quote_standard,
    merge_condition_states,
    normalize_condition_entries,
    states_from_facts,
)

logger = logging.getLogger(__name__)


class IdentityEvidence(BaseModel):
    field: Literal["name", "address", "organization_type"]
    evidence_key: str
    quote: str


class GateJudgement(BaseModel):
    standard_key: str
    achieved: bool
    evidence_key: str | None
    quote: str | None
    reason: str = Field(min_length=1, max_length=300)
    # 固定双条件：条件集合由 `app/core/sales_rules.py` 唯一定义，模型不得自行拆分/合并/改名。
    # 2026-10-19 前由模型自报分项的 `components` 契约已被 Spec20 §12 替代并移除。
    conditions: list[ConditionJudgement] = Field(default_factory=list, max_length=2)
    identity_evidence: list[IdentityEvidence] = Field(default_factory=list, max_length=3)


class Judgements(BaseModel):
    items: list[GateJudgement] = Field(max_length=18)
    read_evidence_keys: list[str] = Field(default_factory=list, max_length=5)


def _evidence_source_ids(context):
    """宿主侧证据身份表：哪个 E 槽对应哪条消息（followup_id / 档案里的 turn_id）。

    `stage_input` 只把 {key, content, occurred_at} 交给模型（Prompt 零变化），所以身份
    必须在投影之前单独取出，作为宿主参数传进 `_judge`：判定层据此区分
    "本轮原文"与"再引证旧消息"，写入层才不会把旧引用伪记成本轮重新确认。
    """
    return {
        e.get("key"): {
            "followup_id": e.get("followup_id") or "",
            "turn_id": e.get("turn_id") or "",
        }
        for e in (context.get("evidence") or [])
        if e.get("key")
    }


def _host_timing(context):
    """宿主控制面时序参数：各条件档案水位 + 本轮业务发生时间。

    `stage_input` 只交付模型需要的字段，这两个键投影后不可见 ⇒ 判定层的时序门
    （`drop_stale_conditions` 与「本轮消息用本轮时点」的处理）会静默失效。
    所以在投影之前取出，作为明确的宿主参数传入 `_judge`：
    Prompt/模型输入白名单保持不变，时序判断本身仍复用 sales_rules 的既有函数。
    """
    return {
        "condition_prior_times": dict(context.get("condition_prior_times") or {}),
        "turn_occurred_at": context.get("turn_occurred_at"),
    }


def _deliver_current_turn_date(context):
    """把本轮业务发生日期补到本轮证据槽（E0）上。

    `assistant_projects.prepare` 只覆盖了 E0 的 content，`occurred_at` 留空；
    而提取结果已经给出本轮业务日期（宿主 `turn_occurred_at` 持有）。投影白名单本来就
    交付 `occurred_at`，所以这里只补值、不加字段：不补就等于让模型看不出哪条是新的
    （C03 实测 E0 为 null，模型因此把昨日 `not_completed` 当成当前状态）。
    不用记录时间替代：只用真实提取出的业务发生日期。
    """
    evidence = context.get("evidence") or []
    turn_occurred_at = context.get("turn_occurred_at")
    if evidence and turn_occurred_at and not evidence[0].get("occurred_at"):
        evidence[0]["occurred_at"] = turn_occurred_at
    return context


def _identity_gaps(identity):
    """Required CRM identity fields for S1/1 that are absent from the host record."""
    identity = identity or {}
    required = (
        ("name_present", "名称"),
        ("address_present", "地址"),
        ("organization_type_present", "机构类型"),
    )
    return [label for key, label in required if not identity.get(key)]


async def judge(context):
    cold = context.get("_evidence_source")
    source_ids = _evidence_source_ids(context)
    host_timing = _host_timing(context)
    # 本轮证据的业务日期在投影前补上（同一张证据槽，不新增字段）。
    context = _deliver_current_turn_date(context)
    context = stage_input(context)
    return await _judge(
        context, cold=cold, source_ids=source_ids, host_timing=host_timing
    )


def _locate(quote, sources, preferred_key):
    """只做的都是**逐字定位**：先认给定来源键，再在任何已提供证据里找唯一原文。"""
    if not quote:
        return None
    if quote in sources.get(preferred_key or "", ""):
        return preferred_key
    return next((key for key, source in sources.items() if quote in source), None)


async def _judge(context, *, cold, expanded=False, source_ids=None, host_timing=None):
    standards = {s["key"]: s for s in context["standards"]}
    sources = {e["key"]: e["content"] for e in context["evidence"]}

    def validate(value, _):
        offered = {d["key"] for d in context.get("evidence_catalog", [])}
        if not set(value.get("read_evidence_keys") or []) <= offered:
            return {"ok": False, "reason": "只能展开evidence_catalog中提供的F键"}
        items = value.get("items") or []
        if {i.get("standard_key") for i in items} != set(standards) or len(items) != len(standards):
            logger.warning(
                "阶段判定键不完整 offered=%s got=%s",
                list(standards),
                [i.get("standard_key") for i in items],
            )
            return {
                "ok": False,
                "reason": "每个下发标准必须判定一次，完整键列表为：" + ",".join(standards),
            }
        allowed = set(condition_ids())
        for item in items:
            entries = item.get("conditions") or []
            ids = [
                str((e or {}).get("condition_id") if isinstance(e, dict) else e.condition_id)
                for e in entries
            ]
            if len(ids) != len(set(ids)) or set(ids) - allowed:
                return {
                    "ok": False,
                    "reason": (
                        "conditions 只能给出共享规则的固定条件且不重复，固定条件ID："
                        + ",".join(sorted(allowed))
                    ),
                }
            standard = standards[item["standard_key"]]
            if (
                item.get("achieved")
                and standard.get("stage") == "s4_budget_confirmed"
                and standard.get("hard_gate")
                and funding_confirmation_blocked(
                    context["facts"], context.get("contract_arrangement_status")
                )
            ):
                return {
                    "ok": False,
                    "reason": (
                        "经费达成判断与已核对事实冲突：资金未确认可用，"
                        "或分期建设对应的合同安排尚不清楚，不能判经费确认已完成。"
                    ),
                }
            quote = item.get("quote") or ""
            if item.get("achieved") and not any(
                quote and quote in source for source in sources.values()
            ):
                return {
                    "ok": False,
                    "reason": (
                        f"标准{item['standard_key']}判为达成，但quote不在任何已提供证据中。"
                        "请从对应E键content逐字引用连续片段，不能改写报告前缀或拼接标题。"
                    ),
                }
            if item.get("achieved") and standard.get("stage") == "s1_valid_opportunity" and str(
                standard.get("seq")
            ) == "1":
                missing_identity = set(_identity_gaps(context.get("customer_identity")))
                labels = {"名称": "name", "地址": "address", "机构类型": "organization_type"}
                required_fields = {labels[label] for label in missing_identity}
                identity_evidence = item.get("identity_evidence") or []
                provided_fields = {entry.get("field") for entry in identity_evidence}
                if required_fields - provided_fields:
                    return {
                        "ok": False,
                        "reason": "S1客户信息达成时，主档缺失字段必须逐项给出原文证据："
                        + ",".join(sorted(required_fields - provided_fields)),
                    }
                for entry in identity_evidence:
                    if entry.get("field") not in required_fields:
                        return {"ok": False, "reason": "identity_evidence只能补充主档缺失字段"}
                    if not _locate(
                        entry.get("quote"), sources, entry.get("evidence_key")
                    ):
                        return {"ok": False, "reason": "identity_evidence必须逐字引用已提供证据"}
        return {"ok": True}

    result = await run_structured(
        alias=MODEL_EXTRACT,
        agent_name="assistant-stage-judge",
        prompt_file="assistant_stage_judge.yaml",
        mappings={"context": context},
        output_model=Judgements,
        validators=[validate],
    )
    requested = result.get("read_evidence_keys") or []
    if requested:
        if expanded:
            raise ValueError("已展开证据后仍不足，需缩小本次判断或人工核对，不能假装证据不存在")
        from agently.core import TaskContext
        from agently.types.data import ContextBudget, ContextReadIntent

        if cold is None:
            raise ValueError("历史证据源未绑定，不能将不可读取误作不存在")
        task = TaskContext(task_id="project-stage-evidence")
        task.attach(cold, binding_id="project-history")
        package = await task.reader(
            consumer="model_request",
            phase="stage-evidence",
            budget=ContextBudget(max_chars=12000, max_blocks=5, max_block_chars=6000),
        ).async_read(
            ContextReadIntent(
                query="展开已选择的历史阶段证据",
                explicit_refs=tuple(requested),
                metadata={"optional_selection": "none"},
            )
        )
        if {b.source_ref for b in package.blocks} != set(requested) or any(
            b.completeness != "complete" for b in package.blocks
        ):
            raise ValueError("所需历史证据超出预算或读取不完整，需缩小本次读取")
        readback = [
            {
                "key": b.source_ref,
                "content": b.content,
                "occurred_at": b.metadata.get("occurred_at"),
            }
            for b in package.blocks
        ]
        expanded_context = {**context, "evidence": [*context["evidence"], *readback]}
        final = await _judge(
            expanded_context,
            cold=cold,
            expanded=True,
            # 展开读回的历史块没有宿主身份；沿用同一张身份表按证据键查，查不到的
            # 条目由写入层拒绝"伪记本轮"（S01），不猜来源。
            source_ids=source_ids,
            # 同一组宿主时序必须在递归展开里继续生效，否则读回历史块后时序门悄悄失效。
            host_timing=host_timing,
        )
        final["_expanded_evidence"] = readback
        return final
    accumulated = states_from_facts(context.get("facts"))
    # 档案里各条件的现行时序水位与本轮来源的业务时间都只由宿主提供，
    # 用于剔掉「事后补录」的过期候选；Prompt 不接收这些内部字段（stage_input 白名单投影）。
    timing = host_timing or {}
    prior_times = timing.get("condition_prior_times") or {}
    turn_occurred_at = timing.get("turn_occurred_at")
    occurred_by_key = {
        e.get("key"): e.get("occurred_at") for e in context.get("evidence") or []
    }
    # 证据槽的第一条是本轮消息本身（`prepare` 用本轮原文覆盖它）：它的业务时间应取
    # 本轮事实的发生时间（补录时早于当前时刻），而不是快照里上一条跟进的时间。
    evidence_slots = context.get("evidence") or []
    current_slot_key = evidence_slots[0].get("key") if evidence_slots else None
    if current_slot_key and turn_occurred_at:
        # 本轮消息（E0）用本轮业务时间：补录时早于当前时刻，交给时序门判断是否事后补录。
        occurred_by_key[current_slot_key] = turn_occurred_at
    for item in result["items"]:
        standard = standards[item["standard_key"]]
        compound = is_proposal_quote_standard(standard.get("stage"), standard.get("seq"))
        entries = normalize_condition_entries(item.get("conditions"))
        for entry in entries:
            located = _locate(entry["quote"], sources, entry["source_key"])
            if located:
                if located != entry["source_key"]:
                    logger.warning(
                        "条件引文键纠正 standard=%s condition=%s",
                        item["standard_key"],
                        entry["condition_id"],
                    )
                    entry["source_key"] = located
                continue
            if entry["quote"]:
                # 逐字定位不到原文的条件不能计入达成；宿主不解释引用内容的业务含义。
                logger.warning(
                    "条件引文无原文，该条件按未确立处理 standard=%s condition=%s",
                    item["standard_key"],
                    entry["condition_id"],
                )
                entry.update(state="unknown", quote="", source_key="")
                continue
            # 空引用的新 completed 不是「已核验」。只允许沿用档案中
            # 已确立（因而有来源）的状态；档案里没有就保持未提及，绝不把空声称写进档案。
            entry["state"] = accumulated.get(entry["condition_id"], "unknown")
            entry["source_key"] = ""
        candidate_times = {
            e["condition_id"]: occurred_by_key.get(e["source_key"]) or ""
            for e in entries
            if e["quote"] and e["source_key"]
        }
        this_round = drop_stale_conditions(
            {e["condition_id"]: e["state"] for e in entries},
            candidate_times,
            prior_times,
            accumulated,
        )
        for entry in entries:
            gated = this_round.get(entry["condition_id"], entry["state"])
            if gated != entry["state"]:
                # 被时序守卫剔除的候选连同其引用一起撤销：不写档案、不参与 AND。
                entry.update(state=gated, quote="", source_key="", stale_dropped=True)
        # ：条件引用的「实际来源」随条目走完全程（判定 → 写入 → 回执）。
        # 本轮消息槽 ⇒ 本轮新证据；定位到更早的消息槽 ⇒ 只是再引证旧证据，
        # 不得顶掉该字段自己的最近确认水位（`assistant_projects`/`persist` 据此写库）。
        identities = source_ids or {}
        for entry in entries:
            if not (entry["quote"] and entry["source_key"]):
                continue
            ident = identities.get(entry["source_key"]) or {}
            entry["source_occurred_at"] = occurred_by_key.get(entry["source_key"]) or ""
            entry["source_is_current_turn"] = entry["source_key"] == current_slot_key
            entry["source_followup_id"] = ident.get("followup_id") or ""
            entry["source_turn_id"] = ident.get("turn_id") or ""
        this_round = {e["condition_id"]: e["state"] for e in entries}
        if compound:
            # 整项达成由共享 AND 在「档案累计 + 本轮已核验」的状态上计算，不采信模型自报总值。
            merged = merge_condition_states(accumulated, this_round)
            # 对外交回的条件状态是**生效状态**（本轮已核验优先，其次档案累计），
            # 并标注来源，避免把「本轮未提及」显示成档案里已有的结论。
            verified = {e["condition_id"] for e in entries if e["quote"]}
            # 再引证旧消息的条件不叫"本轮逐字原文"：回执要能看出它引用的是哪条旧消息。
            re_cited = {
                e["condition_id"]
                for e in entries
                if e["quote"] and not e.get("source_is_current_turn", True)
            }
            effective = []
            for cond_def in PROPOSAL_QUOTE_RULE["conditions"]:
                cid = cond_def["id"]
                entry = next((e for e in entries if e["condition_id"] == cid), None)
                if entry is None:
                    entry = {"condition_id": cid, "state": "unknown", "quote": "", "source_key": ""}
                entry["state"] = merged.get(cid, "unknown")
                entry["state_source"] = (
                    "本轮再次引证旧消息（非本轮重新确认）"
                    if cid in re_cited
                    else (
                        "本轮逐字原文"
                        if cid in verified
                    else (
                            "项目档案累计（旧补录不解除新状态）"
                            if entry.get("stale_dropped")
                            else (
                                "项目档案累计"
                                if entry["state"] != "unknown"
                                else "本轮未提及"
                            )
                        )
                    )
                )
                effective.append(entry)
            item["conditions"] = effective
            item["condition_states"] = merged
            achieved = combine_proposal_quote(merged)
            if bool(item.get("achieved")) != achieved:
                logger.warning(
                    "模型自报整项结果与宿主AND组合不一致 standard=%s model=%s host=%s",
                    item["standard_key"],
                    item.get("achieved"),
                    achieved,
                )
            decisive = next(
                (
                    e
                    for e in entries
                    if e["state"] == "completed" and e["quote"] and e["source_key"]
                ),
                None,
            )
            if achieved and decisive is None:
                # 本轮没有任何可逐字定位的分项原话时不勾选：达成时刻应在条件补齐的那一轮记录。
                achieved = False
                logger.warning(
                    "累计条件已齐但本轮无可定位的分项原话，本次不勾选 standard=%s",
                    item["standard_key"],
                )
            item["achieved"] = achieved
            if achieved:
                item["evidence_key"] = decisive["source_key"]
                item["quote"] = decisive["quote"]
            else:
                item.update(evidence_key=None, quote=None)
            continue
        if not item["achieved"]:
            continue
        if standard.get("stage") == "s1_valid_opportunity" and str(standard.get("seq")) == "1":
            missing_identity = _identity_gaps(context.get("customer_identity"))
            labels = {"名称": "name", "地址": "address", "机构类型": "organization_type"}
            field_labels = {value: key for key, value in labels.items()}
            required_fields = {labels[label] for label in missing_identity}
            identity_evidence = item.get("identity_evidence") or []
            supported_fields = {
                entry.get("field")
                for entry in identity_evidence
                if entry.get("field") in required_fields
                and _locate(entry.get("quote"), sources, entry.get("evidence_key"))
            }
            unsupported = required_fields - supported_fields
            if unsupported:
                item.update(
                    achieved=False,
                    evidence_key=None,
                    quote=None,
                    reason=(
                        "客户身份信息未完整核实，主档缺项且没有逐字原文依据："
                        + "、".join(field_labels[field] for field in sorted(unsupported))
                    ),
                )
                continue
        located = _locate(item.get("quote") or "", sources, item.get("evidence_key"))
        if not located:
            # 找不到逐字原文就保持阶段，不把模型推断冒充客户证据。
            logger.warning("阶段引文无原文，降为未达成 standard=%s", item["standard_key"])
            item.update(achieved=False, evidence_key=None, quote=None, conditions=[])
            continue
        item["conditions"] = entries
        if located != item.get("evidence_key"):
            # 引文确实存在只是落在别的证据键：纠正来源键，不把正确事实判成缺证。
            logger.warning("阶段引文键纠正 standard=%s", item["standard_key"])
            item["evidence_key"] = located
    return result
