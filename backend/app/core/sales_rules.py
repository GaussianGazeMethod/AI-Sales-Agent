"""跨入口共用的结构化事实约束。

方案与报价使用统一的条件 ID、三态与 AND 组合，助手和 PC 读取同一版本。
语义判断由模型完成，本模块只验证并组合结构化状态。"""

import hashlib
import json
from datetime import date, datetime


def sales_stage_days(lead, *, today=None):
    """阶段自然日耗时；签约即停止，结束日期缺失时保留未知。"""
    entered = lead.stage_entered_at
    if entered is None:
        return None
    end = lead.sign_date if lead.status == "signed" else (today or date.today())
    if end is None or end < entered.date():
        return None
    return (end - entered.date()).days


def rhythm_issue(facts, *, today=None):
    """R5唯一口径：下一节点和预计时间；不读取旧日期字段或年度窗口。"""
    import re

    facts = facts or {}
    if not (facts.get("next_node") and facts.get("next_node_time")):
        return "下一关键节点或预计时间尚未明确"
    dates = re.findall(r"\d{4}-\d{2}-\d{2}", str(facts["next_node_time"]))
    if dates and max(dates) < (today or date.today()).isoformat():
        return "下一关键节点已到期，请确认完成情况或新安排"
    return None

# ══════════════ 「方案与报价」固定双条件：唯一定义 ══════════════

PROPOSAL_QUOTE_RULE = {
    "rule_id": "proposal_quote_completion",
    # 标准绑定用稳定的「阶段 + 序号」定位；运行时下发的 S7 等短键不是永久业务 ID。
    "standard_ref": {"stage": "s3_proposal_quote", "seq": "1"},
    "combination": "AND",
    "states": ["completed", "not_completed", "unknown"],
    "conditions": [
        {
            "id": "proposal_completion",
            "label": "建设方案已完成",
            "fact_key": "proposal_completion_state",
        },
        {
            "id": "quote_completion",
            "label": "报价已完成",
            "fact_key": "quote_completion_state",
        },
    ],
    "semantics": {
        "未提及": "unknown",
        "明确仍在准备": "not_completed",
        "整项达成": "两个条件同时completed",
        "模型自报总布尔": "不作为执行依据",
        "提交或客户认可": "另行记录,不作为本标准的额外条件",
        "客户预算未知": "既不证明也不否定我方报价完成",
    },
}


def _digest(obj) -> str:
    payload = json.dumps(obj, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


def combo_rule_revision() -> str:
    """规则修订：对定义块做内容寻址摘要，口径同 `app/core/rules_version.py`。

    只读、发布=改定义块（与 R1–R6 基准一样不提供更改入口）；写入时锚定，
    旧记录不回填、不重判；权限仍归管理员/后端，销售与模型无修改入口。
    """
    return _digest(PROPOSAL_QUOTE_RULE)


def condition_ids() -> tuple:
    return tuple(c["id"] for c in PROPOSAL_QUOTE_RULE["conditions"])


def fact_key(condition_id: str):
    for cond in PROPOSAL_QUOTE_RULE["conditions"]:
        if cond["id"] == condition_id:
            return cond["fact_key"]
    return None


def is_proposal_quote_standard(stage, seq) -> bool:
    """按稳定「阶段 + 序号」判断某条标准是否由固定双条件组合，不用运行时短键。"""
    ref = PROPOSAL_QUOTE_RULE["standard_ref"]
    return str(stage or "") == ref["stage"] and str(seq or "") == ref["seq"]


def condition_prompt_block() -> str:
    """下发给两份 Prompt 的共享定义块（单行、无引号无换行，JSON 注入后仍逐字可见）。"""
    pairs = ";".join(
        f"{c['id']}={c['label']}=>{c['fact_key']}"
        for c in PROPOSAL_QUOTE_RULE["conditions"]
    )
    sem = PROPOSAL_QUOTE_RULE["semantics"]
    return (
        f"共享组合规则 rule_id={PROPOSAL_QUOTE_RULE['rule_id']} "
        f"revision={combo_rule_revision()} 条件={pairs} "
        f"状态=completed|not_completed|unknown "
        f"组合={PROPOSAL_QUOTE_RULE['combination']}(两个条件同时completed) "
        f"模型自报整项布尔值{sem['模型自报总布尔']} 未提及={sem['未提及']} "
        f"明确仍在准备={sem['明确仍在准备']} "
        "每个completed必须给出该条件自己的逐字原话与来源键,不同条件可引用不同消息; "
        f"整项达成={sem['整项达成']}; {sem['提交或客户认可']}; {sem['客户预算未知']} "
        # facts 里的两个 *_state 是历史累计快照，单独交付的
        # prior_condition_states 带各自来源日期；历史不是约束，本轮更晚的逐字原文可以更新它。
        "facts中的 proposal_completion_state/quote_completion_state 是历史累计快照,"
        "其来源日期见 prior_condition_states;历史不是约束,"
        "本轮更晚的逐字原文可以更新该状态;未提及不清空历史,更早补录由宿主水位挡下"
    )


def states_from_facts(facts) -> dict:
    """累计状态读取：档案里没有记录的条件按 unknown，默认即未达成，不做关键词推断。"""
    facts = facts or {}
    states = {}
    for cond in PROPOSAL_QUOTE_RULE["conditions"]:
        value = facts.get(cond["fact_key"])
        states[cond["id"]] = value if value in PROPOSAL_QUOTE_RULE["states"] else "unknown"
    return states


def normalize_condition_entries(entries) -> list:
    """契约整形（宿主职责，不含语义判断）：只保留固定 ID，重复取首次，非法丢弃。"""
    allowed = set(condition_ids())
    seen: dict = {}
    for raw in entries or []:
        if isinstance(raw, dict):
            entry = raw
        elif hasattr(raw, "model_dump"):
            entry = raw.model_dump()
        else:
            entry = {}
        cid = str(entry.get("condition_id") or "").strip()
        if cid not in allowed or cid in seen:
            continue
        state = str(entry.get("state") or "").strip()
        seen[cid] = {
            "condition_id": cid,
            "state": state if state in PROPOSAL_QUOTE_RULE["states"] else "unknown",
            "source_key": str(entry.get("source_key") or ""),
            "quote": str(entry.get("quote") or ""),
        }
    return list(seen.values())


def raw_condition_contract_error(entries) -> str | None:
    """归一化**之前**的原始结构校验。

    `normalize_condition_entries` 是整形器：重复取首个、非法丢弃。它不能当门——
    否则一条结构非法的回传会被"修好"，再靠档案累计凑齐 AND 变成成功采纳
    。本函数只做纯结构判断，
    不含任何关键词或语义猜测：条目必须是带 `condition_id` 的映射、
    ID 必须属于共享规则的固定条件、不得重复、状态必须属于规则声明的三态。
    合法缺项（本轮没给某个条件）与合法 unknown 不在拒绝之列，仍由累计口径处理。
    """
    if not entries:
        return None
    allowed_ids = set(condition_ids())
    allowed_states = set(PROPOSAL_QUOTE_RULE["states"])
    seen: list = []
    for index, raw in enumerate(entries):
        if isinstance(raw, dict):
            entry = raw
        elif hasattr(raw, "model_dump"):
            entry = raw.model_dump()
        else:
            return f"第 {index + 1} 个条件不是结构化条目，无法核对来源，拒绝采纳"
        cid = str(entry.get("condition_id") or "").strip()
        if not cid:
            return f"第 {index + 1} 个条件缺少 condition_id，拒绝采纳"
        if cid not in allowed_ids:
            allowed = "、".join(sorted(allowed_ids))
            return f"条件 {cid} 不属于共享规则的固定条件（{allowed}），拒绝采纳"
        if cid in seen:
            return f"条件 {cid} 重复提交，拒绝按首个静默去重后采纳"
        seen.append(cid)
        state = str(entry.get("state") or "").strip()
        if state not in allowed_states:
            return f"条件 {cid} 的状态 {state or '（空）'} 不属于合法三态，拒绝采纳"
    return None


def combine_proposal_quote(states) -> bool:
    """整项达成的唯一算法：两个固定条件同时 completed；其余状态（含缺项）一律未达成。"""
    states = states or {}
    if not condition_ids():
        return False
    return all(states.get(cid) == "completed" for cid in condition_ids())


def merge_condition_states(accumulated, verified_this_round) -> dict:
    """跨轮/跨会话累计：本轮明确的状态覆盖旧值，unknown 不覆盖已知项（未提及不清空）。"""
    merged = dict(accumulated or {})
    for cid, state in (verified_this_round or {}).items():
        if state == "unknown" and cid in merged:
            continue
        merged[cid] = state
    return merged


def parse_condition_time(value):
    """业务时间解析：解析失败返回 None（不猜测、不拿录入时间顶替）。"""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00").split("+")[0])
    except ValueError:
        return None


def drop_stale_conditions(candidates, candidate_times, prior_times, accumulated) -> dict:
    """按业务时序剔掉「旧补录」候选。

    `prior_times` 是该条件在档案里的现行时序水位（由 `dossier_service.proof_effective_time`
    得出），`candidate_times` 是本轮各条件**实际定位到的来源**的业务时间。候选时间更早
    （事后补录）时不得解除更新的事实：该条件回落到档案现行状态，调用方据此不再写库。
    任一侧时间缺失或不可解析时不猜测，保留候选——写入层 `_apply_fact_update` 的乱序守卫
    仍会兜底，保证校验、累计、实际生效写入与最终 AND 用同一组时序合法的状态。
    """
    kept = {}
    for cid, state in (candidates or {}).items():
        prior = parse_condition_time((prior_times or {}).get(cid))
        arrived = parse_condition_time((candidate_times or {}).get(cid))
        if (
            state != "unknown"
            and prior is not None
            and arrived is not None
            and arrived < prior
        ):
            kept[cid] = (accumulated or {}).get(cid, "unknown")
            continue
        kept[cid] = state
    return kept


# ══════════════ 既有跨入口规则 ══════════════


def funding_confirmation_blocked(facts, arrangement=None):
    """明确不可用资金、尚未明确的分期签约安排不能自动确认经费。"""
    arrangement = arrangement or (facts or {}).get("contract_arrangement_status")
    return (facts or {}).get("funding_available") is False or arrangement in {
        "unclear", "explicitly_pending",
    }
