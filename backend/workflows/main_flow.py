"""AI 录入流程的稳定拓扑（TriggerFlow）。

依据 Agently-Skills：
- `skills/agently-triggerflow/SKILL.md`：拓扑要先声明；用 `match/case` 让分支在图里可见；
  "a later semantic step needs a tool result, system lookup… produced after the first request"
  时必须把后续请求放到**可见的 join 之后** —— 这里正是 N1（客户定位）→ N2（更新项抽取）。
- `skills/agently/references/project-framework.md`：稳定拓扑直接写成可导入的模块，
  不用运行期 DAG 数据（那是 TaskDAG 的低频场景）。

拓扑：

    entry ─┬─(调用方已指定客户)────────────────→ extract_update → validate_intake
           └─(未指定客户)→ resolve_customer → extract_update → validate_intake
                            └── N1 模型 ──┘   └── N2 模型 ──┘   └─ H1 宿主 ─┘

调用方式：宿主（app/services/ai_service.py）先把事实取齐，再 `async_start(facts)`，
读 `await execution.async_close()` 得到 state["intake_result"]。
"""

from __future__ import annotations

from typing import Any

from agently import TriggerFlow

from workflows.chunks.extract_update import extract_update
from workflows.chunks.resolve_customer import resolve_customer
from workflows.chunks.validate_intake import validate_intake


def _has_explicit_lead(data: Any) -> bool:
    """条件分支的判据：调用方是否已明确指定客户（宿主事实，非语义判断）。"""
    return bool((data.input or {}).get("explicit_lead_id"))


INTAKE_FLOW = TriggerFlow(name="ai-intake-draft")
(
    INTAKE_FLOW.match()
    .case(_has_explicit_lead)
    .to(extract_update)
    .case_else()
    .to(resolve_customer)
    .to(extract_update)
    .end_match()
    .to(validate_intake)
)


async def run_intake_flow(facts: dict[str, Any]) -> dict[str, Any]:
    """跑一次录入流程，返回校验后的结果（不落库——落库由服务层负责）。"""
    execution = INTAKE_FLOW.create_execution(auto_close=False)
    await execution.async_start(facts)
    state = await execution.async_close()
    result = state.get("intake_result")
    if not isinstance(result, dict):
        raise RuntimeError("录入流程未产出 intake_result（拓扑或节点异常）")
    return result


__all__ = ["INTAKE_FLOW", "run_intake_flow"]
