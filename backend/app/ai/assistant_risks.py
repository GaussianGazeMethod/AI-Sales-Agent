"""复核已有R2/R6障碍的新证据；未知不解除，已接受替代只解除本项目范围。"""

from typing import Literal

from pydantic import BaseModel, Field

from app.ai.runtime import MODEL_EXTRACT, run_structured


class RiskDecision(BaseModel):
    state: Literal["resolved", "active", "unchanged"]
    quote: str | None
    reason: str = Field(min_length=1, max_length=500)


class RiskReview(BaseModel):
    trust_obstacle: RiskDecision
    product_obstacle: RiskDecision


async def review(*, message, known, updates):
    obstacles = {
        k: known[k]
        for k in ("trust_obstacle", "product_obstacle")
        if known.get(k) and not str(known[k]).startswith("已解除：")
    }
    if not obstacles:
        return []

    def validate(value, _context):
        items = {k: value.get(k) for k in ("trust_obstacle", "product_obstacle")}
        for field, item in items.items():
            if item is None:
                return {
                    "ok": False,
                    "reason": "两个字段均需状态对象；未给出的障碍填unchanged。",
                }
            if field not in obstacles and item["state"] != "unchanged":
                return {
                    "ok": False,
                    "reason": "未给出的障碍不可改变状态，请填unchanged。",
                }
            if item["state"] != "unchanged" and (
                not item.get("quote") or item["quote"] not in message
            ):
                return {
                    "ok": False,
                    "reason": (
                        f"{field}的{item['state']}缺少本轮连续原文。"
                        "若本轮未明确反馈该障碍的新变化，请返回unchanged，"
                        "不复述旧障碍填active。"
                    ),
                }
        return {"ok": True}

    result = await run_structured(
        alias=MODEL_EXTRACT,
        agent_name="assistant-existing-risk-review",
        prompt_file="assistant_risk_review.yaml",
        mappings={
            "message": message,
            "obstacles": obstacles,
            "known_scope": known.get("scope"),
            "updates": updates,
        },
        output_model=RiskReview,
        validators=[validate],
    )
    return [
        {
            "key": field,
            "value": ("已解除：" if i["state"] == "resolved" else "") + i["reason"],
            "quote": i["quote"],
        }
        for field, i in result.items()
        if field in obstacles and i["state"] != "unchanged"
    ]
