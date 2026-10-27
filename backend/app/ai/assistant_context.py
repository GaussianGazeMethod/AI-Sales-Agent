"""Consumer-specific hot projections; canonical IDs and cold records stay Host-side."""

from collections.abc import Mapping

from agently.core import TaskContext
from agently.types.data import ContextBudget, ContextReadIntent

from app.core.sales_rules import condition_prompt_block

PROJECT_FACTS = {
    "need",
    "scenario",
    "scope",
    "success_expectation",
    "implementation_progress",
    "funding_source",
    "budget_range",
    "budget_plan",
    "funding_available",
    "available_amount_fen",
    "budget_amount_fen",
    "project_amount_fen",
    "project_amount_basis",
    "proposal_amount_fen",
    "award_amount_fen",
    "contract_amount_fen",
    "contract_plan",
    "contract_arrangement_status",
    "payment_plan",
    "decision_roles",
    "approval_path",
    "decision_chain",
    "procurement_step",
    "next_node",
    "next_node_time",
    "trust_obstacle",
    "product_obstacle",
    "contract_signed",
    # 「方案与报价」固定双条件的累计状态（唯一定义见 app/core/sales_rules.py）。
    # 只由宿主按已逐字核验的分条件结果写入，模型提取契约不接受这两个键。
    "proposal_completion_state",
    "quote_completion_state",
}


def facts(values):
    return {k: v for k, v in (values or {}).items() if k in PROJECT_FACTS}


def actions(values):
    fields = {
        "key",
        "content",
        "due_at",
        "status",
        "effect",
        "customer_confirmed",
        "date_basis",
    }
    return [{k: v for k, v in item.items() if k in fields} for item in (values or [])]


def evidence(values):
    return [
        {k: v for k, v in item.items() if k in {"key", "content", "occurred_at"}}
        for item in values
        if item.get("content")
    ]


def stage_input(context):
    fields = {
        "message",
        "customer",
        "project",
        "organization_type",
        "stage",
        "now",
        "contract_completion",
        "contract_arrangement_status",
        "related_projects",
        "customer_identity",
    }
    result = {k: v for k, v in context.items() if k in fields}
    result["facts"] = facts(context.get("facts"))
    result["evidence"] = evidence(context.get("evidence", []))
    result["standards"] = [
        {
            k: v
            for k, v in s.items()
            if k in {"key", "stage", "seq", "content", "hard_gate", "evidence_hint"}
        }
        for s in context.get("standards", [])
    ]
    result["evidence_catalog"] = context.get("evidence_catalog", [])
    # 固定双条件的定义、三态、AND 与修订由 app/core/sales_rules.py 唯一维护，
    # 这里按规则原样交付给节点；Prompt 不另存一份条件（Spec20 §12）。
    result["condition_rule"] = condition_prompt_block()
    # 历史累计条件状态（含各自来源日期）单独成槽：与 facts 里平铺的旧状态区分开，
    # 让模型能判断本轮是不是更晚的新事实（C03）。控制面 condition_prior_times 不下发。
    result["prior_condition_states"] = dict(context.get("prior_condition_states") or {})
    return result


def report_input(context):
    fields = {"message", "customer", "project", "organization_type", "stage", "now", "record"}
    result = {k: v for k, v in context.items() if k in fields}
    result["facts"] = facts(context.get("facts"))
    result["evidence"] = evidence(context.get("evidence", []))
    result["actions"] = actions(context.get("actions"))
    return result


async def package_input(agent_name, mappings):
    """Use native TaskContext/ContextReader for required, lossless, bounded input.

    Native package metadata remains available to audit, never becomes model
    input. No relevance heuristic, silent truncation or second context store.
    """
    budget = 16000 if agent_name == "assistant-stage-judge" else 12000
    task = TaskContext(task_id=agent_name)
    task.put(role="state", content=mappings, entry_id="request-input", required=True)
    package = await task.reader(
        consumer="model_request",
        phase=agent_name,
        budget=ContextBudget(max_chars=budget, max_blocks=1, max_block_chars=budget),
    ).async_read(ContextReadIntent(query="本节点已授权且必要的业务输入"))
    if len(package.blocks) != 1 or package.blocks[0].completeness != "complete":
        raise ValueError("必要上下文无法完整放入预算，不能静默截断")
    def mutable(value):
        if isinstance(value, Mapping):
            return {key: mutable(item) for key, item in value.items()}
        if isinstance(value, (tuple, list)):
            return [mutable(item) for item in value]
        return value

    # Native packages freeze mappings and lists. The YAML prompt API requires
    # concrete dictionaries, including nested mappings used by placeholders.
    return mutable(package.blocks[0].content), package.to_dict()
