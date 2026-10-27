"""同客户多项目的摘要事实归属复核；不改写原始跟进记录。"""

from pydantic import BaseModel, Field, StrictBool

from app.ai.runtime import MODEL_EXTRACT, run_structured


class Attribution(BaseModel):
    index: int
    belongs_to_current_project: StrictBool
    requires_clarification: StrictBool
    reason: str = Field(min_length=1, max_length=300)


class Assessment(BaseModel):
    items: list[Attribution]


async def review(context, updates, *, record=None, actions=None, pending_candidates=None):
    record = dict(record or {})
    actions = list(actions or [])
    candidates = []
    references = []
    for index, update in enumerate(updates):
        candidates.append(
            {"index": len(candidates), "candidate_type": "project_fact", **update}
        )
        references.append(("update", index))
    for key, value in record.items():
        if value is None:
            continue
        candidates.append(
            {
                "index": len(candidates),
                "candidate_type": "followup_record",
                "key": key,
                "value": value,
            }
        )
        references.append(("record", key))
    for index, action in enumerate(actions):
        candidates.append(
            {"index": len(candidates), "candidate_type": "action", **action}
        )
        references.append(("action", index))
    pending_candidates = list(pending_candidates or [])
    pending_start = len(candidates)
    for pending in pending_candidates:
        candidate = dict(pending.get("candidate") or {})
        candidates.append(
            {
                "index": len(candidates),
                "candidate_type": "pending_project_candidate",
                **candidate,
                "source_quote": pending.get("source_quote"),
                "source_message": pending.get("source_message"),
            }
        )
        references.append(("pending", pending))

    unchanged = {
        "updates": updates,
        "record": record,
        "actions": actions,
        "clarifications": [],
        "pending_candidates": [],
        "assessment": None,
    }
    if not candidates or not context.get("related_projects"):
        return unchanged

    def validate(value, _):
        indexes = [item.get("index") for item in value.get("items", [])]
        if sorted(indexes) != list(range(len(candidates))):
            return {
                "ok": False,
                "reason": "每个候选index必须恰好判断一次，不得新增、遗漏或重复。",
            }
        return {"ok": True}

    result = await run_structured(
        alias=MODEL_EXTRACT,
        agent_name="assistant-project-attribution",
        prompt_file="assistant_project_attribution.yaml",
        mappings={
            "context": {
                "message": context["message"],
                "current_project": context.get("project"),
                "known_current_facts": context.get("known", {}),
                "related_projects": context["related_projects"],
                "candidates": candidates,
            }
        },
        output_model=Assessment,
        validators=[validate],
    )
    decisions = {item["index"]: item for item in result["items"]}
    accepted = {
        index
        for index, item in decisions.items()
        if item["belongs_to_current_project"]
        and not item["requires_clarification"]
    }
    filtered_updates = []
    filtered_record = dict(record)
    filtered_actions = []
    clarifications = []
    unresolved_candidates = []
    for candidate, reference in zip(candidates, references, strict=True):
        decision = decisions[candidate["index"]]
        kind, source = reference
        if decision["requires_clarification"]:
            pending = {
                "source_kind": (
                    source.get("source_kind")
                    if kind == "pending"
                    else candidate["candidate_type"]
                ),
                "candidate": {
                    key: value
                    for key, value in candidate.items()
                    if key
                    not in {
                        "index",
                        "candidate_type",
                        "source_message",
                        "source_quote",
                    }
                },
                "source_quote": candidate.get("quote") or candidate.get("source_quote"),
                "source_message": (
                    context.get("message")
                    if candidate["index"] < pending_start
                    else source.get("source_message")
                ),
                "reason": decision["reason"],
            }
            unresolved_candidates.append(pending)
            clarifications.append(
                {
                    "candidate_type": candidate["candidate_type"],
                    "value": candidate.get("value") or candidate.get("content"),
                    "reason": decision["reason"],
                }
            )
        if candidate["index"] not in accepted:
            if kind == "record":
                filtered_record[source] = None
            continue
        if kind == "update":
            filtered_updates.append(updates[source])
        elif kind == "action":
            filtered_actions.append(actions[source])
        elif kind == "pending":
            source_kind = source.get("source_kind")
            pending_candidate = source["candidate"]
            if source_kind == "action":
                filtered_actions.append(pending_candidate)
            elif source_kind == "followup_record":
                key = pending_candidate.get("key")
                if key:
                    filtered_record[key] = pending_candidate.get("value")
            else:
                filtered_updates.append(pending_candidate)
    return {
        "updates": filtered_updates,
        "record": filtered_record,
        "actions": filtered_actions,
        "clarifications": clarifications,
        "pending_candidates": unresolved_candidates,
        "assessment": result,
    }
