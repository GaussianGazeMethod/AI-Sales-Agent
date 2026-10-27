"""N2 · 更新项抽取（模型节点）。

归属：模型把一段话读成结构化更新项；宿主校验枚举与键的成员资格（并在服务层复核金额换算）。

输入依赖：必须已经知道"是哪个客户"，才能取到该客户**当前环节**的标准清单。
- 调用方已指定客户 → 由宿主预取，写在 facts["explicit_standards"]
- 未指定 → 由 N1（resolve_customer）写入 state["intake_resolution"].matched_key，
  宿主早已把各候选的标准预取在 facts["candidate_standards"] 里
"""

from __future__ import annotations

from datetime import date
from typing import Any, cast

from agently import TriggerFlowRuntimeData

from app.ai import keys as key_utils
from app.ai.contracts import IntakeExtract
from app.ai.runtime import MODEL_EXTRACT, run_structured


def _standards_for(facts: dict[str, Any], state: Any) -> tuple[list[dict], str | None]:
    """取出「本次要下发的标准」与它所属的客户键（纯宿主取值，无语义判断）。"""
    explicit = facts.get("explicit_standards")
    if explicit is not None:
        return list(explicit), facts.get("explicit_key")

    resolution = state.get_state("intake_resolution") or {}
    matched_key = resolution.get("matched_key")
    if not matched_key:
        return [], None
    by_candidate = facts.get("candidate_standards") or {}
    return list(by_candidate.get(matched_key) or []), matched_key


async def extract_update(data: TriggerFlowRuntimeData) -> dict[str, Any]:
    """把 message 读成结构化更新项；结果写入 state["intake_extract"]。"""
    facts = cast(dict[str, Any], data.input)
    standards, lead_key = _standards_for(facts, data)
    allowed_standards = {item["key"] for item in standards}
    risk_codes = [str(code) for code in (facts.get("risk_catalog") or {})]
    methods = [str(item["code"]) for item in (facts.get("follow_methods") or [])]

    if lead_key is None:
        # 没定位到客户 → 只抽通用信息，不下发任何标准（不猜环节、不猜标准）
        await data.async_set_state(
            "intake_extract",
            {
                "lead_key": None,
                "standards_offered": [],
                "followup": None,
                "matched_standard_keys": [],
                "amount_yuan": None,
                "amount_reason": "",
                "risk_types": [],
                "evidence": None,
                "next_node": None,
                "next_node_time": None,
                "advance_stage": False,
                "summary": "未定位到客户，请先指定商机后再解析更新项",
            },
            emit=False,
        )
        return facts

    customer = {
        "key": lead_key,
        "name": facts.get("explicit_name") or _candidate_name(facts, lead_key),
        "city": facts.get("explicit_city") or _candidate_city(facts, lead_key),
        "stage_label": facts.get("explicit_stage_label") or "",
    }

    parsed = await run_structured(
        alias=MODEL_EXTRACT,
        agent_name="ai-intake-extract-update",
        prompt_file="intake_extract_update.yaml",
        mappings={
            "message": facts.get("message") or "",
            "customer": customer,
            # 相对时间（"下周三""月底前"）要按今天换算成绝对日期，所以必须给模型今天
            "today": date.today().isoformat(),
            "standards": standards,
            "follow_methods": facts.get("follow_methods") or [],
            "risk_catalog": facts.get("risk_catalog") or {},
        },
        output_model=IntakeExtract,
        validators=[
            key_utils.enum_validator(allowed=methods, field="followup.method", label="跟进方式"),
            key_utils.offered_key_list_validator(
                allowed=allowed_standards, field="matched_standard_keys", label="完成标准键"
            ),
            key_utils.enum_list_validator(allowed=risk_codes, field="risk_types", label="风险代码"),
        ],
    )

    await data.async_set_state(
        "intake_extract",
        {
            "lead_key": lead_key,
            "standards_offered": standards,
            "followup": parsed.get("followup") or None,
            "matched_standard_keys": parsed.get("matched_standard_keys") or [],
            "amount_yuan": parsed.get("amount_yuan"),
            "amount_reason": parsed.get("amount_reason") or "",
            "risk_types": parsed.get("risk_types") or [],
            "evidence": parsed.get("evidence"),
            #  契约里新增字段后必须同时写进 state，否则模型给了也会在这里被丢掉
            "next_node": parsed.get("next_node"),
            "next_node_time": parsed.get("next_node_time"),
            "advance_stage": bool(parsed.get("advance_stage")),
            "summary": parsed.get("summary") or "",
        },
        emit=False,
    )
    return facts


def _candidate_name(facts: dict[str, Any], lead_key: str) -> str:
    for item in facts.get("candidates") or []:
        if item.get("key") == lead_key:
            return str(item.get("name") or "")
    return ""


def _candidate_city(facts: dict[str, Any], lead_key: str) -> str:
    for item in facts.get("candidates") or []:
        if item.get("key") == lead_key:
            return str(item.get("city") or "")
    return ""
