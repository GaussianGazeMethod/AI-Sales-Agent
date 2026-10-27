"""Only the persisted followup record is consumed here; no stage or finance decisions."""

import json
from typing import Literal

from pydantic import BaseModel, Field

from app.ai.assistant_facts import ProgressRecord
from app.ai.runtime import MODEL_EXTRACT, run_structured


class RecordProof(BaseModel):
    field: Literal["action_goal", "customer_feedback", "achievement", "next_action"]
    source_key: str
    quote: str = Field(min_length=1, max_length=1500)


class ReviewedRecord(BaseModel):
    record: ProgressRecord
    proofs: list[RecordProof] = Field(max_length=12)
    corrections: list[str] = Field(max_length=4, description="只说明纠正了什么，不输出思维链")
    retained_fields: list[
        Literal["action_goal", "customer_feedback", "achievement", "next_action"]
    ] = Field(
        default_factory=list,
        max_length=4,
        description=(
            "同次补答中，若record字段同时引用P_<字段>和M且未明确更正，必须列出该字段；"
            "这表示最终字段已保留旧事实并补入本轮信息。只列有P与M双来源的实际保留字段。"
        ),
    )
    corrected_fields: list[
        Literal["action_goal", "customer_feedback", "achievement", "next_action"]
    ] = Field(
        default_factory=list,
        max_length=4,
        description=(
            "仅当销售本轮明确更正了上一同次记录的该字段事实时列出；"
            "列出的字段可用本轮M覆盖旧值（旧值仍留在历史事件）。同一被更正事实若也写在其他当前字段，"
            "那些字段也必须逐项列出；未列出的字段不得丢弃仍有效的上一记录事实"
        ),
    )


def build_validator(sources: dict, is_new: bool):
    """记录核对校验器：只做合法来源验证 + 结构化契约执行，不判断语义保留。

    宿主职责（本函数，确定性 D）：
    - 每条 proof 的 quote 必须逐字来自该字段允许的来源；
    - 非空 record 字段必须至少有一条 proof（无新增可留 null，由系统沿用旧值）；
    - 新行动不得复用旧客户反馈/达成作为本次事实；
    - corrected_fields 声明"本轮明确更正"的字段，其新值必须引用本轮 M（更正须落在本轮消息上）。

    "同次补答保留仍有效的旧事实 / 明确更正替换当前"是语义职责，由模型解释、提示词约束，
    并以真实模型正反语义验收证明；宿主不对正文是否包含旧事实做子串判断——那既会误拒忠实
    概括，又能被无意义短引绕过。
    """

    def allowed(field):
        keys = {"M"}
        if field == "action_goal":
            # 意图字段：按计划执行的新行动，其目标本就是上一次记录的目标。
            keys.add("P_action_goal")
        if not is_new:
            keys.add("P_" + field)
        if field in {"action_goal", "next_action"}:
            keys.update(k for k in sources if k.startswith("A"))
            keys.add("P_next_action")
        return keys & sources.keys()

    def validate(value, _context):
        proofs = value.get("proofs") or []
        corrected = set(value.get("corrected_fields") or [])
        retained = set(value.get("retained_fields") or [])

        def proof_feedback(code, field, key, quote, *, allowed_keys=None):
            payload = {
                "code": code,
                "field": field,
                "source_key": key,
                "quote_excerpt": (quote or "")[:64],
                "fix": "只从proof_sources按source_key逐字复制；action_candidates只是候选，不是证据",
            }
            if allowed_keys is not None:
                payload["allowed_source_keys"] = sorted(allowed_keys)
            return {
                "ok": False,
                "reason": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                "payload": payload,
            }

        for proof in proofs:
            field = proof.get("field")
            key = proof.get("source_key")
            quote = proof.get("quote") or ""
            if field not in {
                "action_goal",
                "customer_feedback",
                "achievement",
                "next_action",
            }:
                return {"ok": False, "reason": "记录字段不在允许集合"}
            allowed_keys = allowed(field)
            if key not in allowed_keys:
                return proof_feedback(
                    "proof_source_not_allowed",
                    field,
                    key,
                    quote,
                    allowed_keys=allowed_keys,
                )
            if not quote or quote not in sources[key]:
                return proof_feedback("proof_quote_not_verbatim", field, key, quote)
        record = value.get("record") or {}
        for field, content in record.items():
            if content and not any(p.get("field") == field for p in proofs):
                return {
                    "ok": False,
                    "reason": "非空记录字段必须提供来源引用；无新增事实可以null",
                }
        for field in corrected:
            if record.get(field) and not any(
                p.get("field") == field and p.get("source_key") == "M" for p in proofs
            ):
                return {
                    "ok": False,
                    "reason": (
                        f"更正字段“{field}”必须引用本轮 M 作为覆盖依据；"
                        f"不能只沿用旧记录来源声明更正"
                    ),
                }
        if retained & corrected:
            return {"ok": False, "reason": "明确更正的字段不能同时声明为保留旧值"}
        if is_new and retained:
            return {"ok": False, "reason": "新行动不能声明沿用同次旧字段"}
        proof_sources = {}
        for proof in proofs:
            proof_sources.setdefault(proof.get("field"), set()).add(
                proof.get("source_key")
            )
        for field, keys in proof_sources.items():
            if (
                not is_new
                and field not in corrected
                and record.get(field)
                and f"P_{field}" in keys
                and "M" in keys
                and field not in retained
            ):
                return {
                    "ok": False,
                    "reason": (
                        f"同次字段{field}同时引用旧值和本轮信息时，必须明确声明retained_fields；"
                        "来源proof本身不代表正文已完成保留合并"
                    ),
                }
        return {"ok": True}

    return validate


async def review(context, extracted):
    sources = {"M": context["message"]}
    previous = context.get("record") or {}
    for field in ("action_goal", "customer_feedback", "achievement", "next_action"):
        if previous.get(field):
            sources["P_" + field] = previous[field]
    for action in context.get("actions", []):
        sources[action["key"]] = action["content"]
    prior_actions = {item["key"]: item for item in context.get("actions", [])}
    action_candidates = []
    for action in extracted.get("actions", []):
        key = action.get("action_key")
        prior = prior_actions.get(key) if key else None
        due_at = action.get("due_at")
        if (
            action.get("operation") == "update"
            and due_at is None
            and not action.get("due_at_pending")
            and prior
        ):
            due_at = prior.get("due_at")
        action_candidates.append(
            {
                "operation": action.get("operation"),
                "target_action_key": key,
                "candidate_content": action.get("content") or (prior or {}).get("content"),
                "normalized_due_at": due_at,
                "normalized_due_at_basis": action.get("due_at_basis")
                or ((prior or {}).get("date_basis") or {}).get("due_at_basis"),
            }
        )
    is_new = extracted["new_record"]
    validate = build_validator(sources, is_new)
    proof_failure = {}

    def validate_or_request_confirmation(value, validation_context):
        result = validate(value, validation_context)
        diagnostic = result.get("payload") or {}
        if diagnostic.get("code") in {
            "proof_source_not_allowed",
            "proof_quote_not_verbatim",
        }:
            # Preserve the strict rejection as a review result, but do not let a bad
            # citation alone consume Agently's native retries or fail the turn.
            # assistant_service must withhold the business write until the exact
            # structured record is explicitly confirmed by the salesperson.
            proof_failure.update(diagnostic)
            return {"ok": True, "reason": "proof rejected; explicit record confirmation required"}
        return result

    reviewed = await run_structured(
        alias=MODEL_EXTRACT,
        agent_name="assistant-record-review",
        prompt_file="assistant_record_review.yaml",
        mappings={
            "context": {
                "message": context["message"],
                "current_project": context.get("project"),
                "related_projects": context.get("related_projects", []),
                "screened_project_facts": context.get("screened_project_facts", []),
                "screened_project_actions": context.get("screened_project_actions", []),
                "screened_project_record": context.get("screened_project_record", {}),
                "previous_date": context.get("record_occurred_at"),
                "occurred_at": extracted.get("occurred_at"),
                "new_record": is_new,
                "event_relation": extracted.get("event_relation"),
                "draft": extracted["record"],
                "action_candidates": action_candidates,
                "proof_sources": [
                    {"source_key": k, "source_content": v} for k, v in sources.items()
                ],
            }
        },
        output_model=ReviewedRecord,
        validators=[validate_or_request_confirmation],
    )
    reviewed["proof_validation"] = (
        {"status": "rejected", "diagnostic": proof_failure}
        if proof_failure
        else {"status": "accepted"}
    )
    return reviewed
