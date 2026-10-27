"""只验证归属复核的宿主契约；模型语义由独立合成探测验证。"""

from app.ai import assistant_project_attribution


async def test_single_project_does_not_add_model_call(monkeypatch):
    async def unexpected(**_):
        raise AssertionError("单项目不调用跨项目归属模型")

    monkeypatch.setattr(assistant_project_attribution, "run_structured", unexpected)
    updates = [{"key": "need", "value": "需求", "quote": "需求"}]
    result = await assistant_project_attribution.review({}, updates)
    assert result == {
        "updates": updates,
        "record": {},
        "actions": [],
        "clarifications": [],
        "pending_candidates": [],
        "assessment": None,
    }


async def test_only_offered_candidates_survive_without_rewriting(monkeypatch):
    updates = [
        {"key": "need", "value": "当前需求", "quote": "当前需求"},
        {"key": "scope", "value": "其他项目", "quote": "其他项目"},
    ]

    async def model(**kwargs):
        validator = kwargs["validators"][0]
        assert not validator({"items": [{"index": 0}, {"index": 0}]}, {})["ok"]
        assert not validator({"items": [{"index": 5}]}, {})["ok"]
        value = {
            "items": [
                {
                    "index": 0,
                    "belongs_to_current_project": True,
                    "requires_clarification": False,
                    "reason": "测试替身",
                },
                {
                    "index": 1,
                    "belongs_to_current_project": False,
                    "requires_clarification": False,
                    "reason": "测试替身",
                },
            ]
        }
        assert validator(value, {})["ok"]
        return value

    monkeypatch.setattr(assistant_project_attribution, "run_structured", model)
    result = await assistant_project_attribution.review(
        {"message": "当前需求；其他项目", "related_projects": [{"name": "其他项目"}]},
        updates,
    )
    assert result["updates"] == [updates[0]]
    assert result["actions"] == []
    assert result["record"] == {}
    assert updates[1]["value"] == "其他项目"


async def test_uncertain_candidate_is_never_saved_even_if_model_boolean_conflicts(
    monkeypatch,
):
    update = {"key": "implementation_progress", "value": "校园助手资料", "quote": "资料"}

    async def model(**kwargs):
        return {
            "items": [
                {
                    "index": 0,
                    "belongs_to_current_project": True,
                    "requires_clarification": True,
                    "reason": "无法区分当前项目和相关项目",
                }
            ]
        }

    monkeypatch.setattr(assistant_project_attribution, "run_structured", model)
    result = await assistant_project_attribution.review(
        {"message": "校园助手资料", "related_projects": [{"name": "另一项目"}]},
        [update],
    )
    assert result["updates"] == []
    assert result["clarifications"] == [
        {
            "candidate_type": "project_fact",
            "value": "校园助手资料",
            "reason": "无法区分当前项目和相关项目",
        }
    ]
