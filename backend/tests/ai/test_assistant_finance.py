"""财务宿主契约；语义输出明确替身，真实表现另由脱敏回放验证。"""

import pytest

from app.ai import assistant_finance
from tests.support.finance import assessment


@pytest.mark.asyncio
async def test_increment_uses_existing_project_total_and_keeps_customer_budget(monkeypatch):
    quote = "当前项目可以增补48万元"

    async def model(**kwargs):
        value = assessment(
            project_change="add", project_quote=quote, project_basis="customer_budget"
        )
        assert kwargs["validators"][0](value, {})["ok"]
        return value

    monkeypatch.setattr(assistant_finance, "run_structured", model)
    result = await assistant_finance.review(
        {
            "message": quote,
            "known": {"project_amount_fen": 66000000, "budget_amount_fen": 50000000},
        }
    )
    facts = {u["key"]: u["value"] for u in result["updates"]}
    assert facts["project_amount_fen"] == 114000000
    assert "budget_amount_fen" not in facts
    assert result["updates"][0]["calculation"] == {
        "operation": "add",
        "base_fen": 66000000,
        "delta_fen": 48000000,
    }


@pytest.mark.asyncio
async def test_application_is_forecast_without_confirmed_budget_or_available_funds(monkeypatch):
    quote = "明年按720万元申报，尚待审批"

    async def model(**kwargs):
        return assessment(
            project_change="set",
            project_quote=quote,
            project_basis="application_plan",
            budget_plan_quote=quote,
        )

    monkeypatch.setattr(assistant_finance, "run_structured", model)
    result = await assistant_finance.review({"message": quote, "known": {}})
    facts = {u["key"]: u["value"] for u in result["updates"]}
    assert facts["project_amount_fen"] == 720000000 and facts["budget_plan"] == quote
    assert "budget_amount_fen" not in facts and "funding_available" not in facts


@pytest.mark.asyncio
async def test_quote_and_customer_funds_have_different_amounts(monkeypatch):
    message = "方案报价78万元，今年已有18万元自筹资金可用"

    async def model(**kwargs):
        return assessment(
            project_change="set",
            project_quote="方案报价78万元",
            project_basis="quote",
            proposal_quote="方案报价78万元",
            budget_quote="今年已有18万元自筹资金可用",
            funding_source_quote="自筹资金",
            availability="available",
            availability_quote="今年已有18万元自筹资金可用",
        )

    monkeypatch.setattr(assistant_finance, "run_structured", model)
    result = await assistant_finance.review({"message": message, "known": {}})
    facts = {u["key"]: u["value"] for u in result["updates"]}
    assert facts["project_amount_fen"] == facts["proposal_amount_fen"] == 78000000
    assert facts["budget_amount_fen"] == 18000000 and facts["funding_available"] is True


@pytest.mark.asyncio
async def test_contract_uses_known_award_with_explicit_source(monkeypatch):
    quote = "合同金额沿用中标价"

    async def model(**kwargs):
        value = assessment(
            project_change="set",
            project_quote=quote,
            project_basis="contract",
            contract_quote=quote,
            contract_use_award=True,
        )
        assert kwargs["validators"][0](value, {})["ok"]
        return value

    monkeypatch.setattr(assistant_finance, "run_structured", model)
    result = await assistant_finance.review(
        {"message": quote, "known": {"award_amount_fen": 74000000}}
    )
    assert {u["key"]: u["value"] for u in result["updates"]}["contract_amount_fen"] == 74000000


@pytest.mark.asyncio
async def test_existing_contract_conflict_requires_explicit_before_after_correction_quote(
    monkeypatch,
):
    ambiguous_message = "同一份已经签署的合同金额是86万元。"
    correction_message = "补充更正：合同总价由88万元改为86万元。"
    known = {"contract_amount_fen": 88000000}

    async def capture_validator(**kwargs):
        validate = kwargs["validators"][0]
        message = kwargs["mappings"]["message"]
        if message == ambiguous_message:
            ambiguous = assessment(
                project_change="set",
                project_quote=message,
                project_basis="contract",
                contract_quote=message,
            )
            assert not validate(ambiguous, {})["ok"]
            return assessment()

        explicit = assessment(
            project_change="set",
            project_quote="86万元",
            project_basis="contract",
            contract_quote="86万元",
            contract_change_type="explicit_correction",
            contract_change_quote=correction_message,
        )
        assert validate(explicit, {})["ok"]
        whole_correction_quote = assessment(
            project_change="set",
            project_quote=correction_message,
            project_basis="contract",
            contract_quote=correction_message,
            contract_change_type="explicit_correction",
            contract_change_quote=correction_message,
        )
        rejected = validate(whole_correction_quote, {})
        assert not rejected["ok"]
        assert "只引用一个明确金额" in rejected["reason"]
        return assessment()

    monkeypatch.setattr(assistant_finance, "run_structured", capture_validator)
    await assistant_finance.review({"message": ambiguous_message, "known": known})
    await assistant_finance.review({"message": correction_message, "known": known})


def test_unresolved_contract_conflict_blocks_rewrite_and_forces_clarification():
    from app.ai.assistant_facts import _guard_unresolved_contract_amount_conflict
    from app.services.assistant_service import (
        _should_review_record,
        _unresolved_contract_dialogue,
    )

    updates = [
        {"key": "contract_plan", "value": "合同金额86万元", "quote": "合同金额86万元"},
        {"key": "contract_amount_fen", "value": 86000000, "quote": "合同金额86万元"},
        {"key": "payment_plan", "value": "付款节点待定", "quote": "付款节点待定"},
    ]
    retained, unresolved = _guard_unresolved_contract_amount_conflict(
        updates, {"contract_change_type": "unclear"}
    )
    assert unresolved is True
    assert [item["key"] for item in retained] == ["payment_plan"]

    extracted = {
        "_contract_amount_conflict": unresolved,
        "new_record": False,
        "has_financial_change": True,
        "record": {"achievement": "更正合同金额为86万元"},
    }
    assert _should_review_record(extracted) is False
    dialogue = _unresolved_contract_dialogue(extracted)
    assert dialogue["question_topic"] == "financial_fact"
    assert "请确认" in dialogue["question"]
    assert "暂未变更" in dialogue["acknowledgement"]


@pytest.mark.asyncio
async def test_increment_without_base_and_ungrounded_quote_are_rejected(monkeypatch):
    async def model(**kwargs):
        validate = kwargs["validators"][0]
        assert not validate(
            assessment(
                project_change="add", project_quote="增补48万元", project_basis="customer_budget"
            ),
            {},
        )["ok"]
        assert not validate(assessment(funding_source_quote="我编的财政资金"), {})["ok"]
        return assessment()

    monkeypatch.setattr(assistant_finance, "run_structured", model)
    assert not (await assistant_finance.review({"message": "增补48万元", "known": {}}))[
        "updates"
    ]


def test_derived_project_increment_requires_original_base_and_exact_delta():
    from app.ai.assistant_facts import amount_supported

    candidate = {
        "key": "project_amount_fen",
        "value": 114000000,
        "quote": "可以增补48万元",
        "calculation": {"operation": "add", "base_fen": 66000000, "delta_fen": 48000000},
    }
    assert amount_supported(candidate, {"known": {"project_amount_fen": 66000000}})
    assert not amount_supported(candidate, {"known": {"project_amount_fen": 55000000}})
    assert not amount_supported(
        {**candidate, "value": 115000000}, {"known": {"project_amount_fen": 66000000}}
    )


def test_explicit_three_part_quote_is_computed_without_counting_total_twice():
    from app.ai.assistant_facts import amount_supported, explicit_amount_sum

    quote = "AI平台192万+AI平板84万+课改服务60万≈336万"
    assert explicit_amount_sum(quote) == 336000000
    assert explicit_amount_sum("192万+84万+60万") == 336000000
    assert explicit_amount_sum("192万+84万+60万=400万") is None
    assert explicit_amount_sum("预算192万，报价84万，另一项目60万") is None
    assert amount_supported(
        {"key": "proposal_amount_fen", "quote": "192万+84万+60万", "value": 336000000}, {}
    )


def test_phase_equations_sum_subtotals_without_double_counting_components():
    from app.ai.assistant_facts import explicit_amount_sum

    quote = "一期服务60万 + 平台36万（=96万）；二期平台144万 + 平板84万（=228万）"
    assert explicit_amount_sum(quote) == 324000000
    assert explicit_amount_sum("一期60万+36万=100万；二期144万+84万=228万") is None
    assert explicit_amount_sum("一期60万+36万=96万；另一项目324万") is None
