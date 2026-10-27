"""原生绑定线索skill提取增量事实；宿主验证原文及字段类型。"""

from typing import Annotated, Literal

from pydantic import Field, StrictBool

from app.ai.assistant_context import actions
from app.ai.assistant_facts import (
    BooleanFact,
    BusinessExtraction,
    QuotedFact,
)
from app.ai.assistant_router import skill_catalog
from app.ai.runtime import MODEL_EXTRACT, run_structured
from app.core import dicts
from app.core.errors import ValidationError


class LeadTextFact(QuotedFact):
    key: Literal["name", "source", "contact_name", "contact_position"]
    value: str = Field(min_length=1, max_length=2000)


class LeadIntentFact(QuotedFact):
    key: Literal["intent"]
    value: Literal["report_meeting", "wechat_material", "unknown"]


class LeadBooleanFact(BooleanFact):
    key: Literal["info_verified", "rejected", "contacted"]
    value: StrictBool


LeadFactUpdate = Annotated[
    LeadTextFact | LeadIntentFact | LeadBooleanFact, Field(discriminator="key")
]


class LeadIntakeOutput(BusinessExtraction):
    """Business extraction contract narrowed to lead intake's supported facts."""

    text_updates: list[LeadTextFact | LeadIntentFact] = Field(max_length=8)
    flag_updates: list[LeadBooleanFact] = Field(max_length=3)


async def extract_lead(message: str, known: dict, pending_questions: list[str]) -> dict:
    revision = skill_catalog()["lead-intake-validation"]
    execution = known.get("_execution_context", {})
    context = {
        k: v
        for k, v in execution.items()
        if k
        in {
            "record",
            "record_occurred_at",
            "now",
            "mode",
            "simulation_facts",
        }
    }
    context["actions"] = actions(execution.get("actions"))
    context.update(
        message=message,
        known={
            k: v
            for k, v in known.items()
            if k
            in {
                "name",
                "source",
                "contact_name",
                "contact_position",
                "info_verified",
                "intent",
                "rejected",
                "contacted",
            }
        },
        pending_questions=pending_questions,
        sources=dicts.LEAD_SOURCE_LABELS,
    )
    result = await run_structured(
        alias=MODEL_EXTRACT,
        agent_name="sales-lead-intake",
        prompt_file="assistant_business_extract.yaml",
        mappings={"context": context},
        output_model=LeadIntakeOutput,
        required_skills=[revision.revision_ref],
    )
    result["updates"] = result.get(
        "updates",
        [
            *result.get("text_updates", []),
            *result.get("flag_updates", []),
            *result.get("amount_updates", []),
        ],
    )
    seen = set()
    for update in result["updates"]:
        key, value, quote = update["key"], update["value"], update["quote"]
        if key in seen or quote not in message:
            raise ValidationError("提取事实重复或缺少本次原文依据，请重试")
        seen.add(key)
        if key in {"info_verified", "rejected", "contacted"}:
            if not isinstance(value, bool):
                raise ValidationError("核实/拒绝事实必须为明确布尔值")
        elif key.endswith("_fen"):
            if type(value) is not int or value < 0:
                raise ValidationError("金额必须是非负整数分")
        elif key in {"funding_available", "contract_signed"}:
            if type(value) is not bool:
                raise ValidationError("确认字段必须是布尔值")
        elif not isinstance(value, str) or not value.strip():
            raise ValidationError("线索字段必须为非空文本")
        if key == "source" and value not in dicts.LEAD_SOURCE_LABELS:
            raise ValidationError("来源不在已公布字典中")
        if key == "intent" and value not in {"report_meeting", "wechat_material", "unknown"}:
            raise ValidationError("意愿信号不在已确认范围内")
    return result
