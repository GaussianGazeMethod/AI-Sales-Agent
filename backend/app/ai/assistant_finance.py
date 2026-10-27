"""金额性质与项目归属由模型判断，换算/增补运算由宿主执行。"""

from typing import Literal

from pydantic import BaseModel, Field

from app.ai.runtime import MODEL_EXTRACT, run_structured

FINANCIAL_KEYS = {
    "proposal_amount_fen",
    "award_amount_fen",
    "contract_amount_fen",
    "budget_amount_fen",
    "budget_range",
    "budget_plan",
    "funding_source",
    "funding_available",
    "available_amount_fen",
    "project_amount_fen",
    "project_amount_basis",
}

# Candidate routing is broader than the finance-node output filter: amount/flag
# candidates must reach this reviewer even when the extractor's route hint is false.
FINANCIAL_REVIEW_CANDIDATE_KEYS = FINANCIAL_KEYS | {
    "contract_signed",
    "contract_plan",
    "payment_plan",
}


class FinancialAssessment(BaseModel):
    project_change: Literal["unchanged", "set", "add"]
    project_quote: str | None
    project_basis: Literal[
        "unchanged", "application_plan", "customer_budget", "quote", "award", "contract"
    ]
    proposal_quote: str | None
    award_quote: str | None
    contract_quote: str | None
    contract_use_award: bool
    contract_change_type: Literal["not_applicable", "explicit_correction", "unclear"] = (
        "not_applicable"
    )
    contract_change_quote: str | None = None
    budget_quote: str | None
    budget_range_quote: str | None
    budget_range_state: Literal["unchanged", "confirmed", "explicitly_unknown"] = Field(
        description=(
            "客户本轮明确更新预算范围时填写状态：有来源的肯定范围为confirmed；"
            "客户明确表示范围未知/尚未确定/待确认时为explicitly_unknown；未提及为unchanged。"
        )
    )
    budget_plan_quote: str | None
    funding_source_quote: str | None
    funding_source_state: Literal["unchanged", "confirmed", "explicitly_unknown"] = Field(
        description=(
            "客户本轮明确更新资金来源时填写状态：有来源的肯定来源为confirmed；"
            "客户明确表示来源未知/尚未确定/待确认时为explicitly_unknown；未提及为unchanged。"
        )
    )
    availability: Literal["unchanged", "available", "unavailable"]
    availability_quote: str | None
    reason: str = Field(min_length=1, max_length=500)


async def review(context):
    from app.ai.assistant_facts import (
        amount_values,
        explicit_amount_sum,
        literal_source_quote,
    )

    message = context["message"]
    known = {
        k: v
        for k, v in (context.get("known") or {}).items()
        if k in FINANCIAL_KEYS or k in {"contract_plan", "payment_plan"}
    }
    base = known.get("project_amount_fen", context.get("project_amount_fen"))
    if type(base) is not int or base <= 0:
        base = None

    def numbers(quote):
        return set(amount_values(quote or ""))

    def amount(quote, *, use_award=False):
        values = numbers(quote)
        if len(values) == 1:
            return next(iter(values))
        total = explicit_amount_sum(quote or "")
        if total is not None:
            return total
        if not values and use_award and type(known.get("award_amount_fen")) is int:
            return known["award_amount_fen"]
        return None

    def validate(value, _):
        for key, quote in value.items():
            if (
                key.endswith("_quote")
                and quote is not None
                and not literal_source_quote(message, quote)
            ):
                return {
                    "ok": False,
                    "reason": f"{key}必须逐字引用本轮消息，不能引用旧记录或拼接。",
                }
        for state_key, quote_key in (
            ("budget_range_state", "budget_range_quote"),
            ("funding_source_state", "funding_source_quote"),
        ):
            state = value.get(state_key, "unchanged")
            quote = value.get(quote_key)
            if (state == "unchanged") != (quote is None):
                return {
                    "ok": False,
                "reason": (
                    f"{state_key}与{quote_key}必须一致：未提及为unchanged/null；"
                    "明确更新必须引用本轮原话。"
                ),
                }
        for key in ("proposal_quote", "award_quote", "contract_quote", "budget_quote"):
            quote = value.get(key)
            if (
                quote
                and amount(
                    quote,
                    use_award=key == "contract_quote" and value.get("contract_use_award"),
                )
                is None
            ):
                return {
                    "ok": False,
                    "reason": (
                        f"{key}须只引用一个明确金额；合同沿用中标须有已知中标价。"
                        "范围放budget_range_quote，申报计划放budget_plan_quote。"
                        "本轮无金额数字时，该字段填null并保持project_change=unchanged；"
                        "不得拿旧分期金额推算取消后的新合同价。"
                    ),
                }
        known_contract = known.get("contract_amount_fen")
        contract_quote = value.get("contract_quote")
        contract_value = amount(
            contract_quote,
            use_award=value.get("contract_use_award", False),
        ) if contract_quote else None
        project_contract_value = (
            amount(value.get("project_quote"))
            if value.get("project_basis") == "contract"
            and value.get("project_change") != "unchanged"
            else None
        )
        candidate_contract_value = contract_value or project_contract_value
        if (
            type(known_contract) is int
            and candidate_contract_value is not None
            and candidate_contract_value != known_contract
        ):
            change_quote = value.get("contract_change_quote")
            change_type = value.get("contract_change_type", "not_applicable")
            if (
                contract_quote is None
                or (project_contract_value is not None and project_contract_value != contract_value)
                or change_type != "explicit_correction"
                or not change_quote
            ):
                return {
                    "ok": False,
                    "reason": (
                        "已知同一合同金额与本轮不同；仅在本轮明确说明更正/变更且引用同时包含原金额和新金额时更新，"
                        "否则保持合同与项目金额不变并向销售澄清。"
                    ),
                }
            quoted_values = numbers(change_quote)
            if not {known_contract, candidate_contract_value}.issubset(quoted_values):
                return {
                    "ok": False,
                    "reason": "合同金额更正依据必须逐字引用同时包含原金额和新金额的本轮内容。",
                }
        elif value.get("contract_change_type", "not_applicable") == "explicit_correction":
            return {
                "ok": False,
                "reason": "没有已知合同金额冲突时不得声明为合同金额更正。",
            }
        if value["project_change"] != "unchanged":
            quote = value.get("project_quote")
            inherited = value["project_basis"] == "contract" and value.get("contract_use_award")
            if not quote or amount(quote, use_award=inherited) is None:
                return {
                    "ok": False,
                    "reason": "项目金额变化须引用一个总额或增量；不要把几个项目金额混在一个引文。",
                }
            if value["project_change"] == "add" and base is None:
                return {
                    "ok": False,
                    "reason": (
                        "没有已知当前项目金额，不能累加增补；"
                        "明确总额用set，否则unchanged并保留预算计划。"
                    ),
                }
            if value["project_change"] == "add" and len(numbers(quote)) != 1:
                return {
                    "ok": False,
                    "reason": "增补只引用本轮增量金额，不把旧总额包含在增量引文中。",
                }
            if value["project_basis"] == "unchanged":
                return {"ok": False, "reason": "金额变化须明确项目金额性质。"}
        if value["availability"] != "unchanged" and not value.get("availability_quote"):
            return {
                "ok": False,
                "reason": "资金可用状态必须有本轮客户明确依据，不能用申报或到账预期推断。",
            }
        return {"ok": True}

    result = await run_structured(
        alias=MODEL_EXTRACT,
        agent_name="assistant-financial-facts",
        prompt_file="assistant_finance.yaml",
        mappings={
            "message": message,
            "project": context.get("project"),
            "customer": context.get("customer"),
            "known": known,
            "project_amount_fen": base,
            "recent_record": context.get("record", {}),
            "related_projects": context.get("related_projects", []),
        },
        output_model=FinancialAssessment,
        validators=[validate],
    )
    updates = []

    def put(key, value, quote, *, knowledge_state=None):
        item = {
            "key": key,
            "value": value,
            "quote": literal_source_quote(message, quote) or quote,
        }
        if knowledge_state is not None:
            item["knowledge_state"] = knowledge_state
        updates.append(item)

    for key, qkey in [
        ("proposal_amount_fen", "proposal_quote"),
        ("award_amount_fen", "award_quote"),
        ("contract_amount_fen", "contract_quote"),
        ("budget_amount_fen", "budget_quote"),
    ]:
        if result[qkey]:
            put(
                key,
                amount(
                    result[qkey],
                    use_award=qkey == "contract_quote" and result["contract_use_award"],
                ),
                result[qkey],
                knowledge_state="confirmed" if key == "budget_amount_fen" else None,
            )
    if result.get("budget_range_state", "unchanged") != "unchanged":
        put(
            "budget_range",
            result["budget_range_quote"],
            result["budget_range_quote"],
            knowledge_state=result.get("budget_range_state", "unchanged"),
        )
    if result["budget_plan_quote"]:
        put("budget_plan", result["budget_plan_quote"], result["budget_plan_quote"])
    if result.get("funding_source_state", "unchanged") != "unchanged":
        put(
            "funding_source",
            result["funding_source_quote"],
            result["funding_source_quote"],
            knowledge_state=result.get("funding_source_state", "unchanged"),
        )
    if result["availability"] != "unchanged":
        put(
            "funding_available",
            result["availability"] == "available",
            result["availability_quote"],
        )
        if (
            result["availability"] == "available"
            and amount(result["availability_quote"]) is not None
        ):
            put(
                "available_amount_fen",
                amount(result["availability_quote"]),
                result["availability_quote"],
                knowledge_state="confirmed",
            )
    if result["project_change"] != "unchanged":
        delta = amount(
            result["project_quote"],
            use_award=result["project_basis"] == "contract" and result["contract_use_award"],
        )
        total = base + delta if result["project_change"] == "add" else delta
        put(
            "project_amount_fen",
            total,
            result["project_quote"],
            knowledge_state="confirmed",
        )
        if result["project_change"] == "add":
            updates[-1]["calculation"] = {
                "operation": "add",
                "base_fen": base,
                "delta_fen": delta,
            }
        put(
            "project_amount_basis",
            result["project_basis"],
            result["project_quote"],
            knowledge_state="confirmed",
        )
    return {"updates": updates, "assessment": result}
