"""诊断及需求报告的有界结构化产物。"""

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.ai.assistant_context import report_input
from app.ai.assistant_router import skill_catalog
from app.ai.runtime import MODEL_EXTRACT, run_structured
from app.core.errors import ValidationError


class Section(BaseModel):
    title: str = Field(min_length=1, max_length=20)
    content: str = Field(min_length=1, max_length=250)
    evidence_keys: list[str] = Field(max_length=6)


class Suggestion(BaseModel):
    content: str = Field(min_length=1, max_length=100)
    due_at: str | None


class Analysis(BaseModel):
    sections: list[Section] = Field(min_length=5, max_length=5)
    suggested_actions: list[Suggestion] = Field(max_length=3)
    requirements_status: Literal["ready", "draft", "not_applicable"]
    requirement_gaps: list[str] = Field(max_length=3)

    @model_validator(mode="after")
    def status_shape(self):
        if self.requirements_status == "draft" and not self.requirement_gaps:
            raise ValueError("需求草稿须返回实际缺口")
        if self.requirements_status != "draft" and self.requirement_gaps:
            raise ValueError("非草稿不携带需求缺口")
        return self


TITLES = {
    "project-diagnosis": [
        "项目现状",
        "推进缺口",
        "阻点与风险",
        "推进策略",
        "下一步行动",
    ],
    "project-requirements-report": [
        "建设背景与问题",
        "核心需求与场景",
        "建设范围与约束",
        "资金与客户期待",
        "待确认与下一步",
    ],
}


async def generate(skill_id, context):
    context = report_input(context)
    allowed = {e["key"] for e in context["evidence"]}
    revision = skill_catalog()[skill_id]
    request_provenance = {
        "skill_id": revision.skill_id,
        "skill_revision": revision.revision_ref,
        # This is an offline source candidate only. No runtime production-delivery
        # reference is available to this process, so it must remain null.
        "candidate_delivery_ref": None,
    }

    def validate_report(value, _context):
        if skill_id == "project-requirements-report":
            if value.get("requirements_status") not in {"ready", "draft"}:
                return {"ok": False, "reason": "需求报告须明确ready或draft"}
        elif value.get("requirements_status") != "not_applicable":
            return {"ok": False, "reason": "项目诊断不作需求报告可用性判断"}
        sections = value.get("sections") or []
        if [s.get("title") for s in sections] != TITLES[skill_id]:
            return {"ok": False, "reason": "必须按section_titles原文顺序输出五项"}
        if any(not set(s.get("evidence_keys") or []) <= allowed for s in sections):
            return {
                "ok": False,
                "reason": "evidence_keys只能使用本次提供的键："
                + ",".join(sorted(allowed))
                + "；无依据返回空数组",
            }
        if sum(len(s.get("content") or "") for s in sections) > 800:
            return {
                "ok": False,
                "reason": "五项正文总计不能超过800字符，请压缩重复信息",
            }
        return {"ok": True}

    result = await run_structured(
        alias=MODEL_EXTRACT,
        agent_name="assistant-analysis",
        prompt_file="assistant_analysis.yaml",
        mappings={
            "context": context,
            "titles": TITLES[skill_id],
            "report_kind": skill_id,
        },
        output_model=Analysis,
        required_skills=[revision.revision_ref],
        validators=[validate_report],
        request_provenance=request_provenance,
    )
    if [s["title"] for s in result["sections"]] != TITLES[skill_id]:
        raise ValidationError("报告必须遵循已约定的五项结构")
    if sum(len(s["content"]) for s in result["sections"]) > 800:
        raise ValidationError("报告超过一页内容预算，请重新生成精简报告")
    allowed = {e["key"] for e in context["evidence"]}
    for section in result["sections"]:
        if not set(section["evidence_keys"]) <= allowed:
            raise ValidationError("分析引用了未提供的证据")
    result["rule_provenance"] = {
        key: request_provenance[key]
        for key in (
            "skill_id",
            "skill_revision",
            "prompt_file",
            "prompt_sha256",
            "rendered_prompt_sha256",
            "candidate_delivery_ref",
        )
    }
    return result
