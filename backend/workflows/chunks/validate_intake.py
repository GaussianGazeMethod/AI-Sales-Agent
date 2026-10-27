"""H1 · 校验并组装录入结果（宿主节点，不调用模型）。

职责：
- 复核金额换算（「元」→「分」整数，非负）——模型给数值，宿主做算术
- 把 `S*` 键映射回 `standard_id`、`C*` 键映射回 `lead_id`（模型永不接触 UUID）
- 越界项不静默采用：记录到 `warnings` 供专员核对
"""

from __future__ import annotations

from typing import Any, cast

from agently import TriggerFlowRuntimeData


def yuan_to_fen(yuan: Any) -> int | None:
    """「元」→「分」整数。非数值、负数、超范围一律视为不可用（None）。"""
    if yuan is None or isinstance(yuan, bool):
        return None
    try:
        value = float(yuan)
    except (TypeError, ValueError):
        return None
    if value < 0:
        return None
    if value > 1_000_000_000:  # 10 亿元，超出业务合理区间
        return None
    return int(round(value * 100))


async def validate_intake(data: TriggerFlowRuntimeData) -> dict[str, Any]:
    facts = cast(dict[str, Any], data.input)
    extract = data.get_state("intake_extract") or {}
    #  调用方已指定客户时，拓扑会**跳过** resolve_customer 节点，
    # 因此 state 里没有 intake_resolution —— 必须从 facts 里取，否则草稿会丢客户。
    if facts.get("explicit_lead_id"):
        resolution = {
            "source": "explicit",
            "matched_key": facts.get("explicit_key"),
            "reason": "调用方已在界面指定客户，无需定位",
        }
    else:
        resolution = data.get_state("intake_resolution") or {}

    warnings: list[str] = []
    matched_key = resolution.get("matched_key")
    lead_id = (facts.get("identity_map") or {}).get(matched_key) if matched_key else None

    if resolution.get("source") == "model" and matched_key and not lead_id:
        warnings.append(f"客户键 {matched_key} 无法映射到客户，已按未定位处理")
        matched_key, lead_id = None, None
    if resolution.get("source") == "explicit":
        lead_id = facts.get("explicit_lead_id")
        matched_key = facts.get("explicit_key")

    # 标准键 → standard_id（只接受本次下发过的键）
    standard_map: dict[str, str] = (facts.get("standard_map") or {}).get(matched_key or "", {})
    standards: list[dict] = []
    seen: set[str] = set()
    by_key = {item["key"]: item for item in (extract.get("standards_offered") or [])}
    for key in extract.get("matched_standard_keys") or []:
        text = str(key)
        if text in seen:
            continue
        seen.add(text)
        standard_id = standard_map.get(text)
        if not standard_id:
            warnings.append(f"标准键 {text} 不在本次下发范围，已忽略")
            continue
        standard = by_key.get(text) or {}
        standards.append(
            {
                "standard_id": standard_id,
                "content": standard.get("content") or "",
                #  只能用模型**明确给出**的证据；这里曾经回退成 followup.content /
                #    消息原文，等于宿主替模型"造"了证据（完成记录看上去有证据）。
                #    没有证据就留空 → 确认时不会勾选，并提示去就地补证据（设计稿 05）。
                "evidence": str(extract.get("evidence") or "").strip(),
                "is_hard_gate": bool(standard.get("is_hard_gate")),
            }
        )

    # 金额：「元」→「分」，负数/超范围丢弃并告警
    raw_yuan = extract.get("amount_yuan")
    amount_fen = yuan_to_fen(raw_yuan)
    if raw_yuan not in (None, "") and amount_fen is None:
        warnings.append(f"金额 {raw_yuan!r} 不是有效数值，已忽略")
    if amount_fen == 0:
        amount_fen = None

    risk_codes = {str(code) for code in (facts.get("risk_catalog") or {})}
    risks = [str(code) for code in (extract.get("risk_types") or []) if str(code) in risk_codes]

    method_codes = {str(item["code"]) for item in (facts.get("follow_methods") or [])}
    followup = extract.get("followup") or {}
    method = str(followup.get("method") or "")
    if method not in method_codes:
        method = None

    # 时间承诺：解析 YYYY-MM-DD；过去的时间照收但提示核对（客户可能说的是"已经过了"）
    from app.services import import_service

    followup_date = import_service.parse_date_cell(str(followup.get("followed_at") or ""))
    if followup.get("followed_at") and followup_date is None:
        warnings.append("实际跟进日期无法识别，请人工修正后确认")

    await data.async_set_state(
        "intake_result",
        {
            "resolution_source": resolution.get("source") or "none",
            "resolution_reason": resolution.get("reason") or "",
            "lead_key": matched_key,
            "lead_id": lead_id,
            "followup": (
                {
                    "method": method,
                    "content": followup.get("content") or "",
                    "followed_at": followup_date.isoformat() + "T00:00:00"
                    if followup_date
                    else None,
                }
                if method
                else None
            ),
            "standards": standards,
            "amount_fen": amount_fen,
            "amount_reason": extract.get("amount_reason") or "",
            "risk_types": risks,
            "next_node": extract.get("next_node"),
            "next_node_time": extract.get("next_node_time"),
            "advance_stage": bool(extract.get("advance_stage")),
            "summary": extract.get("summary") or "",
            "warnings": warnings,
        },
        emit=False,
    )
    return facts
