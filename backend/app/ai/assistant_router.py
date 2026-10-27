"""语义路由由Agently模型完成，宿主只校验候选键和对象边界。"""

from functools import lru_cache
from typing import Literal

from agently import Agently
from pydantic import BaseModel, Field, model_validator

from app.ai.business_skills import install_business_skills
from app.ai.keys import offered_key_validator
from app.ai.runtime import MODEL_EXTRACT, run_structured


class RouteDecision(BaseModel):
    skill_key: str | None = Field(description="本次下发的K键；无法确定时null")
    object_key: str | None = Field(description="本次下发的O键；新增线索或对象未知时null")
    transition: Literal["continue", "switch", "new", "clarify"]
    reason: str = Field(
        min_length=1, max_length=300, description="简短可审计选择依据，不输出思维链"
    )
    question: str | None = Field(default=None, max_length=300)

    @model_validator(mode="after")
    def coherent(self):
        if self.transition == "clarify" and not self.question:
            raise ValueError("无法定位任务时必须提出澄清问题")
        if self.transition != "clarify" and not self.skill_key:
            raise ValueError("非澄清路由必须选择skill")
        return self


@lru_cache(maxsize=1)
def skill_catalog():
    return install_business_skills(Agently.skill_library)


async def route_message(context: dict) -> dict:
    revisions = skill_catalog()
    bindings = {f"K{i}": r for i, r in enumerate(revisions.values(), 1)}

    def validate_lifecycle(value, _context):
        obj = next((o for o in context["objects"] if o["key"] == value.get("object_key")), None)
        revision = bindings.get(value.get("skill_key"))
        if (
            obj
            and obj.get("current_stage")
            and revision
            and revision.skill_id == "lead-intake-validation"
        ):
            return {
                "ok": False,
                "reason": (
                    "所选对象current_stage非空，已转商机；不能继续线索验证，"
                    "请按本轮意图选择项目跟进/查询/报告能力。"
                ),
            }
        return {"ok": True}

    result = await run_structured(
        alias=MODEL_EXTRACT,
        agent_name="sales-assistant-router",
        prompt_file="assistant_route.yaml",
        mappings={
            "message": context["message"],
            "task": context["task"],
            "history": context["history"],
            "objects": context["objects"],
            "candidate_scope": context["candidate_scope"],
            "skills": [
                {"key": k, "name": r.name, "description": r.description}
                for k, r in bindings.items()
            ],
        },
        output_model=RouteDecision,
        validators=[
            validate_lifecycle,
            offered_key_validator(
                allowed=bindings, field="skill_key", label="技能键", allow_null=True
            ),
            offered_key_validator(
                allowed=[o["key"] for o in context["objects"]],
                field="object_key",
                label="对象键",
                allow_null=True,
            ),
        ],
    )
    # 宿主再次校验；模型不复制数据库ID或修订标识。
    skill = bindings.get(result["skill_key"])
    if result["skill_key"] and skill is None:
        raise ValueError("模型返回候选外技能")
    if result["object_key"] not in {None, *(o["key"] for o in context["objects"])}:
        raise ValueError("模型返回候选外对象")
    return {
        **result,
        "skill_id": skill.skill_id if skill else None,
        "skill_revision": skill.revision_ref if skill else None,
    }
