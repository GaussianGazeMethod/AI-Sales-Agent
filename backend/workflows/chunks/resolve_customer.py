"""N1 · 客户定位（模型节点）。

归属：模型判断 message 指向哪个客户；宿主只做键的成员资格校验。
"""

from __future__ import annotations

from typing import Any, cast

from agently import TriggerFlowRuntimeData

from app.ai import keys as key_utils
from app.ai.contracts import IntakeResolve
from app.ai.runtime import MODEL_EXTRACT, run_structured


async def resolve_customer(data: TriggerFlowRuntimeData) -> dict[str, Any]:
    """在宿主下发的候选客户中选择；结果写入 state["intake_resolution"]。"""
    facts = cast(dict[str, Any], data.input)
    candidates = facts.get("candidates") or []
    allowed = {item["key"] for item in candidates}

    if not candidates:
        # 没有任何候选可下发时，宿主直接判定为"未定位"（不是模型失败，也不是降级）
        await data.async_set_state(
            "intake_resolution",
            {
                "source": "model",
                "matched_key": None,
                "reason": "本次没有可下发的候选客户",
                "candidates": [],
            },
            emit=False,
        )
        return facts

    # 把"只能选下发的键"这一可满足规则在第一次请求前就给全（Skills：Rule-First Business Validation）
    parsed = await run_structured(
        alias=MODEL_EXTRACT,
        agent_name="ai-intake-resolve-customer",
        prompt_file="intake_resolve_customer.yaml",
        mappings={
            "message": facts.get("message") or "",
            "candidates": candidates,
            "candidate_scope": facts.get("candidate_scope") or "",
        },
        output_model=IntakeResolve,
        validators=[
            key_utils.offered_key_validator(
                allowed=allowed, field="matched_key", label="候选客户键", allow_null=True
            )
        ],
    )

    matched_key = parsed.get("matched_key")
    matched_key = str(matched_key).strip() if matched_key not in (None, "") else None
    if matched_key is not None and matched_key not in allowed:
        # 校验器已给过纠正机会，仍然越界 → 契约被违反，宿主拒绝（不做猜测）
        raise ValueError(f"客户定位返回了未下发的键：{matched_key!r}")

    await data.async_set_state(
        "intake_resolution",
        {
            "source": "model",
            "matched_key": matched_key,
            "reason": parsed.get("reason") or "",
            "candidates": candidates,
        },
        emit=False,
    )
    return facts
