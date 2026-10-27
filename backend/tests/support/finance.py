"""Shared test builders, extracted without behavior changes."""

def assessment(**fields):
    return {
        "project_change": "unchanged",
        "project_quote": None,
        "project_basis": "unchanged",
        "proposal_quote": None,
        "award_quote": None,
        "contract_quote": None,
        "contract_use_award": False,
        "contract_change_type": "not_applicable",
        "contract_change_quote": None,
        "budget_quote": None,
        "budget_range_quote": None,
        "budget_plan_quote": None,
        "funding_source_quote": None,
        "availability": "unchanged",
        "availability_quote": None,
        "reason": "模型替身仅用于验证宿主执行契约",
        **fields,
    }
