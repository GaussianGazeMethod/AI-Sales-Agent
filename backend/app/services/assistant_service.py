"""应用会话状态和幂等路由回执。模型等待期间释放数据库连接。"""

import asyncio
import copy
import hashlib
import json
import logging
import traceback
import uuid
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import (
    assistant_analysis,
    assistant_facts,
    assistant_router,
    assistant_stage_judge,
    lead_intake,
)
from app.config import get_settings
from app.core.errors import (
    AiUnavailableError,
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
)
from app.models.assistant import AssistantConversation, AssistantTurn
from app.models.lead import Lead
from app.services import (
    assistant_leads,
    assistant_projects,
    dossier_service,
    lead_service,
)

_MODEL_SLOTS = asyncio.Semaphore(4)


def _record_confirmation_version(lead_id: str, source_turn_id: str, record: dict) -> str:
    canonical = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    binding = f"{lead_id}\n{source_turn_id}\n{canonical}".encode("utf-8")
    return "record-v1-" + hashlib.sha256(binding).hexdigest()[:16]


def _record_confirmation_prompt(version_id: str, record: dict) -> str:
    labels = {
        "action_goal": "行动目标",
        "customer_feedback": "客户反馈",
        "achievement": "达成情况",
        "next_action": "下一步行动",
    }
    lines = [f"版本：{version_id}"]
    lines.extend(
        f"{label}：{record.get(field) if record.get(field) is not None else '（未提供）'}"
        for field, label in labels.items()
    )
    return (
        "记录引用核对发现异常，本条尚未保存。请核对并确认以下结构化记录；"
        "如需修改，请直接说明修改内容。\n"
        + "\n".join(lines)
    )


async def _await_record_confirmation(
    db,
    *,
    turn,
    conversation,
    request_key: str,
    revision: int,
    next_task: dict,
    suspended: list,
    lead_id: str,
    pending: dict,
    prefix: str = "",
):
    next_task["pending_record_confirmation"] = pending
    next_task["state"] = "awaiting_user"
    prompt = _record_confirmation_prompt(pending["version_id"], pending["record"])
    visible_task = {
        key: value
        for key, value in next_task.items()
        if key != "pending_record_confirmation"
    }
    result = {
        "turn_id": turn.id,
        "task": visible_task,
        "business_executed": False,
        "overall_status": "awaiting_confirmation",
        "question": prompt,
        "reply": prefix + prompt,
        "record_confirmation": {
            "status": "awaiting_confirmation",
            "version_id": pending["version_id"],
            "record": pending["record"],
            "source_turn_id": pending["source_turn_id"],
            "proof_validation": pending["proof_validation"],
        },
        "operation_receipts": [
            {"operation": "followup", "status": "pending_confirmation"},
            {"operation": "stage", "status": "not_run"},
        ],
    }
    turn.status = "routed"
    turn.result = result
    turn.checkpoint = {
        "kind": "record_confirmation",
        "lead_id": lead_id,
        "source_turn_id": pending["source_turn_id"],
        "version_id": pending["version_id"],
    }
    changed = await db.execute(
        update(AssistantConversation)
        .where(
            AssistantConversation.id == conversation.id,
            AssistantConversation.active_request == request_key,
            AssistantConversation.revision == revision + 1,
        )
        .values(
            task=next_task,
            lead_id=lead_id,
            suspended_tasks=suspended[-20:],
            active_request=None,
            processing_since=None,
            revision=revision + 2,
        )
    )
    if changed.rowcount != 1:
        raise ConflictError("待确认版本已过期，未覆盖新的会话状态")
    await db.commit()
    return result


async def _expire_record_confirmation(
    db,
    *,
    turn,
    conversation,
    request_key: str,
    revision: int,
    next_task: dict,
    suspended: list,
    lead_id: str,
    pending: dict,
):
    next_task.pop("pending_record_confirmation", None)
    next_task["state"] = "ready"
    result = {
        "turn_id": turn.id,
        "task": next_task,
        "business_executed": False,
        "overall_status": "confirmation_expired",
        "question": "项目档案已变化，旧版本已失效且未保存。请基于最新档案重新提交本次跟进。",
        "reply": "项目档案已变化，旧版本已失效且未保存。请基于最新档案重新提交本次跟进。",
        "record_confirmation": {
            "status": "expired",
            "version_id": pending["version_id"],
            "source_turn_id": pending["source_turn_id"],
        },
        "operation_receipts": [
            {"operation": "followup", "status": "not_saved"},
            {"operation": "stage", "status": "not_run"},
        ],
    }
    turn.status = "routed"
    turn.result = result
    turn.checkpoint = None
    changed = await db.execute(
        update(AssistantConversation)
        .where(
            AssistantConversation.id == conversation.id,
            AssistantConversation.active_request == request_key,
            AssistantConversation.revision == revision + 1,
        )
        .values(
            task=next_task,
            lead_id=lead_id,
            suspended_tasks=suspended[-20:],
            active_request=None,
            processing_since=None,
            revision=revision + 2,
        )
    )
    if changed.rowcount != 1:
        raise ConflictError("待确认版本已过期，未覆盖新的会话状态")
    await db.commit()
    return result


def _should_review_record(extracted: dict) -> bool:
    """Run the record-review model whenever the extraction proposes record content."""
    if extracted.get("_contract_amount_conflict"):
        return False
    if (
        extracted.get("new_record")
        or extracted.get("has_financial_change")
        or extracted.get("event_relation") == "next_action_plan"
        or any(
            update.get("key") == "implementation_progress"
            for update in extracted.get("updates") or []
        )
    ):
        return True
    return any(value is not None for value in (extracted.get("record") or {}).values())


def _unresolved_contract_dialogue(extracted: dict) -> dict | None:
    if not extracted.get("_contract_amount_conflict"):
        return None
    return {
        "pending_question_answered": None,
        "pending_question_index": None,
        "decision_basis": "本轮合同金额与现有档案不同，但没有明确更正依据，需先向销售确认。",
        "question_topic": "financial_fact",
        "question": (
            "当前档案中的已签合同金额与本轮所述金额不一致。"
            "请确认这是对合同金额的正式更正，还是仍以原金额为准？"
        ),
        "acknowledgement": "本轮原话已保留，合同金额暂未变更。",
    }


def _recovery_attempt_snapshot(result: dict) -> dict:
    """Keep the failed operation receipt when a same-key retry replaces the turn result."""
    return {
        "overall_status": result.get("overall_status"),
        "business_executed": result.get("business_executed"),
        "reply": result.get("reply"),
        "error": result.get("error"),
        "operation_receipts": copy.deepcopy(result.get("operation_receipts") or []),
    }


async def _conversation(db, user, conversation_id):
    conversation = await db.get(AssistantConversation, conversation_id)
    if conversation is None:
        raise NotFoundError("会话不存在")
    if conversation.owner_id != user.id:
        raise PermissionDeniedError("只能访问本人的AI会话")
    return conversation


async def _check_object(db, user, lead_id):
    if lead_id:
        lead = await lead_service.get_lead(db, lead_id)
        await lead_service.assert_can_view_lead(db, user, lead)
        return lead
    return None


async def create_conversation(db, *, user, lead_id=None):
    await _check_object(db, user, lead_id)
    conversation = AssistantConversation(owner_id=user.id, lead_id=lead_id)
    db.add(conversation)
    await db.flush()
    return {"id": conversation.id, "lead_id": lead_id, "task": {}, "revision": 0}


async def read_conversation(db, *, user, conversation_id):
    conversation = await _conversation(db, user, conversation_id)
    await _check_object(db, user, conversation.lead_id)
    turns = (
        (
            await db.execute(
                select(AssistantTurn)
                .where(AssistantTurn.conversation_id == conversation.id)
                .order_by(AssistantTurn.created_at, AssistantTurn.id)
            )
        )
        .scalars()
        .all()
    )


    referenced = {
        t.requested_lead_id or ((t.result or {}).get("task") or {}).get("lead_id")
        for t in turns
    }
    referenced.update(t.get("lead_id") for t in (conversation.suspended_tasks or []))
    for linked_id in referenced:
        await _check_object(db, user, linked_id)
    return {
        "id": conversation.id,
        "lead_id": conversation.lead_id,
        "task": conversation.task,
        "suspended_tasks": conversation.suspended_tasks,
        "revision": conversation.revision,
        "turns": [
            {
                "id": t.id,
                "request_key": t.request_key,
                "content": t.content,
                "status": t.status,
                "requested_lead_id": t.requested_lead_id,
                "result": t.result,
                "error": t.error,
            }
            for t in turns
        ],
    }


async def _review_final_record(project_context: dict, extracted: dict) -> dict:
    """Run the ordinary proof review, then keep its output inside the selected project."""
    from app.ai import assistant_project_attribution, assistant_record_review

    review_context = project_context
    if project_context.get("related_projects"):
        screened_facts = [
            item for item in extracted.get("updates", []) if item.get("quote")
        ]
        screened_actions = [
            item for item in extracted.get("actions", []) if item.get("quote")
        ]
        review_context = {
            **project_context,
            "screened_project_facts": screened_facts,
            "screened_project_actions": screened_actions,
            "screened_project_record": extracted.get("record", {}),
        }
    reviewed = await assistant_record_review.review(review_context, extracted)
    if not project_context.get("related_projects"):
        return reviewed

    attribution = await assistant_project_attribution.review(
        project_context, [], record=reviewed.get("record"), actions=[]
    )
    reviewed = {**reviewed, "record": attribution["record"]}
    extracted["record"] = reviewed["record"]
    if attribution.get("pending_candidates"):
        extracted["_pending_project_candidates"] = [
            *extracted.get("_pending_project_candidates", []),
            *attribution["pending_candidates"],
        ]
    clarifications = attribution.get("clarifications", [])
    if clarifications:
        unresolved = clarifications[0]
        value = str(unresolved.get("value") or "这项内容")[:80]
        project = str(project_context.get("project") or "当前项目")
        extracted["question"] = (
            f"“{value}”应归入当前项目“{project}”，还是同客户的其他项目？"
        )
        interaction = extracted.get("interaction") or {}
        interaction.update(
            decision="ask",
            question_topic="project_attribution",
            reason="最终记录核对后的结构化候选项目归属无法确定，需销售澄清。",
        )
        extracted["interaction"] = interaction
    return reviewed


async def send_message(
    db: AsyncSession,
    *,
    user,
    conversation_id: str,
    request_key: str,
    content: str,
    lead_id: str | None = None,
):
    conversation = await _conversation(db, user, conversation_id)
    await _check_object(db, user, conversation.lead_id)
    await _check_object(db, user, lead_id)
    prior = (
        await db.execute(
            select(AssistantTurn).where(
                AssistantTurn.conversation_id == conversation_id,
                AssistantTurn.request_key == request_key,
            )
        )
    ).scalar_one_or_none()
    resume_checkpoint = (
        prior.checkpoint
        if prior and prior.result and prior.result.get("overall_status") == "partial"
        else None
    )
    recovery_history = []
    if prior:
        if prior.content != content or prior.requested_lead_id != lead_id:
            raise ConflictError("同一请求标识不能用于不同消息")
        if prior.status == "routed" and not resume_checkpoint:
            return prior.result
    if resume_checkpoint:
        await _check_object(db, user, resume_checkpoint["lead_id"])
        saved_dossier = await dossier_service.get(db, resume_checkpoint["lead_id"])
        if (
            saved_dossier is None
            or saved_dossier.revision != resume_checkpoint["dossier_revision"]
        ):
            raise ConflictError(
                "档案已有后续变化，已保存跟进保持不变，请核对后恢复阶段判断"
            )
        recovery_history = copy.deepcopy(
            (prior.result or {}).get("recovery_history") or []
        )
        recovery_history.append(_recovery_attempt_snapshot(prior.result or {}))
        resume_checkpoint = copy.deepcopy(resume_checkpoint)
        resume_checkpoint["recovery_history"] = recovery_history
    # 允许服务重启后回收已过期的纯路由请求；本阶段没有业务副作用。
    timeout = get_settings().LLM_TIMEOUT_SECONDS
    if conversation.active_request:
        if conversation.processing_since and conversation.processing_since < (
            datetime.now() - timedelta(seconds=timeout + 30)
        ):
            await db.execute(
                update(AssistantTurn)
                .where(
                    AssistantTurn.conversation_id == conversation_id,
                    AssistantTurn.request_key == conversation.active_request,
                    AssistantTurn.status == "processing",
                )
                .values(status="failed", error="路由执行超时或服务中断，可重试原消息")
            )
        else:
            raise ConflictError("此会话正在处理消息，请等待结果后重试")

    revision = conversation.revision
    reserved = await db.execute(
        update(AssistantConversation)
        .where(
            AssistantConversation.id == conversation_id,
            AssistantConversation.revision == revision,
            AssistantConversation.active_request == conversation.active_request,
        )
        .values(
            active_request=request_key,
            processing_since=datetime.now(),
            revision=revision + 1,
        )
    )
    if reserved.rowcount != 1:
        raise ConflictError("会话已更新，请重新读取后重试")
    turn = prior or AssistantTurn(
        conversation_id=conversation_id,
        request_key=request_key,
        requested_lead_id=lead_id,
        content=content,
    )
    if resume_checkpoint:
        turn.checkpoint = resume_checkpoint
    turn.status, turn.error = "processing", None
    db.add(turn)
    await db.flush()
    turn_id = turn.id
    if resume_checkpoint and resume_checkpoint.get("kind") == "dialogue":
        await db.commit()
        return await _settle_dialogue(db, user, turn, conversation_id, request_key)
    current_id = (
        resume_checkpoint["lead_id"]
        if resume_checkpoint
        else (lead_id or conversation.lead_id)
    )
    current = await _check_object(db, user, current_id)
    rows = (
        (
            await db.execute(
                select(Lead)
                .where(
                    Lead.is_deleted.is_(False),
                    Lead.owner_id == user.id,
                )
                .order_by(Lead.updated_at.desc(), Lead.id)
                .limit(40)
            )
        )
        .scalars()
        .all()
    )
    if current and current.id not in {r.id for r in rows}:
        rows.insert(0, current)
    objects = [
        {
            "key": f"O{i}",
            "customer": r.name,
            "project": r.project_name,
            "status": r.status,
            "current_stage": r.current_stage,
            "current": r.id == current_id,
        }
        for i, r in enumerate(rows, 1)
    ]
    identities = {f"O{i}": r.id for i, r in enumerate(rows, 1)}
    task = dict(conversation.task or {})
    suspended = list(conversation.suspended_tasks or [])
    history = (
        (
            await db.execute(
                select(AssistantTurn)
                .where(
                    AssistantTurn.conversation_id == conversation_id,
                    AssistantTurn.id != turn_id,
                    AssistantTurn.status == "routed",
                )
                .order_by(AssistantTurn.created_at.desc(), AssistantTurn.id.desc())
                .limit(6)
            )
        )
        .scalars()
        .all()
    )
    for prior_turn in history:
        linked_id = ((prior_turn.result or {}).get("task") or {}).get("lead_id")
        await _check_object(db, user, linked_id)
    context = {
        "message": content,
        "task": {
            "skill": task.get("skill_id"),
            "state": task.get("state"),
            "pending_questions": task.get("pending_questions", []),
            "object_key": next(
                (
                    k
                    for k, v in identities.items()
                    if v == (task.get("lead_id") or current_id)
                ),
                None,
            ),
        },
        "history": [
            {"message": t.content, "question": (t.result or {}).get("question")}
            for t in reversed(history)
        ],
        "objects": objects,
        "candidate_scope": "本人最近更新40条线索/项目及明确指定对象；不是客户全集",
    }
    # 已保存消息后提交；模型和等待并发槽时不占数据库事务/连接。
    await db.commit()
    deadline = asyncio.get_running_loop().time() + timeout
    failed_step = "意图识别"
    try:

        async def dispatch():
            async with _MODEL_SLOTS:
                return await assistant_router.route_message(context)

        if resume_checkpoint:
            decision = {
                "skill_id": "sales-project-followup",
                "skill_revision": resume_checkpoint["skill_revision"],
                "object_key": next(k for k, v in identities.items() if v == current_id),
                "transition": "continue"
                if (
                    task.get("lead_id") == current_id
                    and task.get("skill_id") == "sales-project-followup"
                )
                else "switch",
                "reason": "恢复原请求已保存跟进后的阶段操作",
                "question": None,
            }
        else:
            decision = await asyncio.wait_for(dispatch(), timeout=timeout)
        failed_step = "任务校验"
        target_id = identities.get(decision["object_key"])
        if decision["transition"] != "clarify":
            if not target_id and decision["skill_id"] != "lead-intake-validation":
                raise ValidationError("此能力需要明确项目，请选择项目后再试")
            if decision["transition"] == "continue" and task:
                if target_id != task.get("lead_id") or decision["skill_id"] != task.get(
                    "skill_id"
                ):
                    raise ValidationError("任务延续不能悄悄更换项目或skill")
        # 模型返回后重新校验权限及请求所有权，防止过期回执覆盖新状态。
        await _check_object(db, user, target_id)
        if decision["transition"] == "clarify":
            next_task = {
                **task,
                "state": "awaiting_user",
                "pending_questions": [decision["question"]],
            }
            selected_id = current_id
        else:
            switched = task and (
                decision["transition"] == "new"
                or target_id != task.get("lead_id")
                or decision["skill_id"] != task.get("skill_id")
            )
            if switched:
                suspended.append({**task, "state": "suspended"})
            restored = next(
                (
                    t
                    for t in suspended
                    if decision["transition"] != "new"
                    and target_id is not None
                    and t.get("lead_id") == target_id
                    and t.get("skill_id") == decision["skill_id"]
                ),
                None,
            )
            if restored:
                suspended.remove(restored)
            continued_questions = (restored or (task if not switched else {})).get(
                "pending_questions", []
            )
            next_task = {
                **(restored or (task if not switched else {})),
                "task_id": (restored or (task if not switched else {})).get("task_id")
                or uuid.uuid4().hex,
                "skill_id": decision["skill_id"],
                # A continuing or restored conversation stays bound to the
                # revision it already consumed. Only a genuinely new task adopts
                # the router's current catalog revision.
                "skill_revision": (
                    (restored or (task if not switched else {})).get("skill_revision")
                    or decision["skill_revision"]
                ),
                "lead_id": target_id,
                "state": "ready",
                "pending_questions": [],
            }
            selected_id = target_id
        dialogue_work = None
        extracted = None
        analysis_result = None
        analysis_skill_id = None
        analysis_context_used = None
        workspace_result = None
        business_context = None
        confirmation_source_turn = None
        confirmation_receipt = None
        business_message = content
        business_turn_id = turn_id
        confirmed_pending_record = False
        if (
            decision["transition"] != "clarify"
            and decision["skill_id"] == "lead-intake-validation"
        ):
            base = await assistant_leads.snapshot(db, user, selected_id)
            business_context = (
                await dossier_service.snapshot(db, user, selected_id, compact=True)
                if selected_id
                else {"revision": None, "facts": {}, "actions": [], "record": {}}
            )
            known = {
                **(next_task.get("facts") or {}),
                **base,
                **business_context["facts"],
            }
            next_task["facts"] = known
            next_task.setdefault("original_fields", base)
            await db.commit()

            async def extract():
                async with _MODEL_SLOTS:
                    return await lead_intake.extract_lead(
                        content,
                        {
                            **known,
                            "_execution_context": {
                                **business_context,
                                "actions": [
                                    {k: v for k, v in a.items() if k != "id"}
                                    for a in business_context["actions"]
                                ],
                                "now": datetime.now().isoformat(),
                                "record": next_task.get(
                                    "record", business_context.get("record", {})
                                ),
                                "mode": next_task.get("mode", "live"),
                                "simulation_facts": next_task.get(
                                    "simulation_facts", {}
                                ),
                            },
                        },
                        continued_questions,
                    )

            remaining = max(0.01, deadline - asyncio.get_running_loop().time())
            failed_step = "线索整理"
            extracted = await asyncio.wait_for(extract(), timeout=remaining)
            await _check_object(db, user, selected_id)
        if (
            decision["transition"] != "clarify"
            and decision["skill_id"] == "sales-project-followup"
        ):
            business_context = await assistant_projects.prepare(
                db, user, selected_id, content
            )
            project_context = {
                **business_context,
                "message": content,
                "known": business_context["facts"],
                "now": datetime.now().isoformat(),
                "pending_questions": continued_questions,
                "asked_topics": next_task.get("asked_topics", []),
                "record": next_task.get("record", business_context.get("record", {})),
                "record_occurred_at": next_task.get(
                    "record_occurred_at", business_context.get("record_occurred_at")
                ),
                "mode": next_task.get("mode", "live"),
                "skill_revision": next_task.get("skill_revision"),
                "simulation_facts": next_task.get("simulation_facts", {}),
                "pending_project_candidates": next_task.get(
                    "pending_project_candidates", []
                ),
                "approved_rules": (
                    "需求报告预算未知不阻止报价；答辩按实际采购流程；"
                    "双方签约完成代表签约手续完成，交付启动另行承接"
                ),
            }
            pending_confirmation = next_task.get("pending_record_confirmation")
            if pending_confirmation and pending_confirmation.get("project_id") == selected_id:
                project_context["pending_record_confirmation"] = {
                    "version_id": pending_confirmation["version_id"],
                    "record": pending_confirmation["record"],
                }
            project_context["actions"] = [
                {k: v for k, v in a.items() if k != "id"}
                for a in project_context["actions"]
            ]
            project_context["standards"] = [
                {k: v for k, v in s.items() if k != "id"}
                for s in project_context["standards"]
            ]
            next_task.setdefault("record", business_context.get("record", {}))
            await db.commit()

            async def extract_project():
                async with _MODEL_SLOTS:
                    return await assistant_facts.extract(
                        "sales-project-followup", project_context
                    )

            if resume_checkpoint:
                extracted = dict(resume_checkpoint["extracted"])
                extracted["_saved_followup"] = resume_checkpoint
                extracted.pop("_stage_unavailable", None)
                extracted["new_record"] = False
            else:
                failed_step = "项目跟进整理"
                extracted = await asyncio.wait_for(
                    extract_project(),
                    timeout=max(0.01, deadline - asyncio.get_running_loop().time()),
                )
                await _check_object(db, user, selected_id)
                pending_confirmation = next_task.get("pending_record_confirmation")
                if (
                    pending_confirmation
                    and pending_confirmation.get("project_id") == selected_id
                    and extracted.get("mode") != "simulation"
                ):
                    interaction = extracted.get("interaction") or {}
                    confirmation_action = interaction.get("record_confirmation_action") or "none"
                    requested_version = interaction.get("record_confirmation_version")
                    if confirmation_action == "confirm_current_version":
                        if requested_version != pending_confirmation["version_id"]:
                            return await _await_record_confirmation(
                                db,
                                turn=turn,
                                conversation=conversation,
                                request_key=request_key,
                                revision=revision,
                                next_task=next_task,
                                suspended=suspended,
                                lead_id=selected_id,
                                pending=pending_confirmation,
                                prefix="确认内容没有绑定当前展示版本，因此本次没有保存。\n",
                            )
                        if business_context["revision"] != pending_confirmation["dossier_revision"]:
                            return await _expire_record_confirmation(
                                db,
                                turn=turn,
                                conversation=conversation,
                                request_key=request_key,
                                revision=revision,
                                next_task=next_task,
                                suspended=suspended,
                                lead_id=selected_id,
                                pending=pending_confirmation,
                            )
                        canonical_pending = json.dumps(
                            pending_confirmation["record"],
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        )
                        expected_record_sha256 = hashlib.sha256(
                            canonical_pending.encode("utf-8")
                        ).hexdigest()
                        if (
                            pending_confirmation.get("record_sha256")
                            != expected_record_sha256
                            or pending_confirmation.get("version_id")
                            != _record_confirmation_version(
                                selected_id,
                                pending_confirmation["source_turn_id"],
                                pending_confirmation["record"],
                            )
                        ):
                            raise ConflictError("待确认记录版本校验失败，不能保存该版本")
                        confirmation_source_turn = await db.get(
                            AssistantTurn, pending_confirmation["source_turn_id"]
                        )
                        if (
                            confirmation_source_turn is None
                            or confirmation_source_turn.conversation_id != conversation_id
                            or confirmation_source_turn.requested_lead_id not in {None, selected_id}
                            or hashlib.sha256(
                                confirmation_source_turn.content.encode("utf-8")
                            ).hexdigest()
                            != pending_confirmation["source_message_sha256"]
                            or (confirmation_source_turn.result or {}).get(
                                "record_confirmation", {}
                            ).get("version_id")
                            != pending_confirmation["version_id"]
                            or (confirmation_source_turn.result or {}).get(
                                "record_confirmation", {}
                            ).get("record")
                            != pending_confirmation["record"]
                        ):
                            raise ConflictError("待确认原始消息或项目归属已变化，不能保存该版本")
                        extracted = copy.deepcopy(pending_confirmation["extracted"])
                        extracted["record"] = copy.deepcopy(pending_confirmation["record"])
                        extracted["_record_review"] = copy.deepcopy(
                            pending_confirmation["record_review"]
                        )
                        confirmation_receipt = {
                            "status": "confirmed",
                            "version_id": pending_confirmation["version_id"],
                            "record_sha256": pending_confirmation["record_sha256"],
                            "project_id": selected_id,
                            "source_turn_id": pending_confirmation["source_turn_id"],
                            "confirmation_turn_id": turn_id,
                            "confirmation_message_sha256": hashlib.sha256(
                                content.encode("utf-8")
                            ).hexdigest(),
                            "proof_validation": pending_confirmation["proof_validation"],
                        }
                        extracted["_record_confirmation"] = confirmation_receipt
                        business_message = confirmation_source_turn.content
                        business_turn_id = confirmation_source_turn.id
                        project_context["message"] = business_message
                        project_context["evidence"][0]["content"] = business_message
                        business_context["evidence"][0]["content"] = business_message
                        next_task.pop("pending_record_confirmation", None)
                        confirmed_pending_record = True
                    elif confirmation_action == "edit_record":
                        # The salesperson asks for a new candidate. It follows the
                        # ordinary extraction and strict proof review path; it does
                        # not approve the currently displayed version.
                        next_task.pop("pending_record_confirmation", None)
                    else:
                        return await _await_record_confirmation(
                            db,
                            turn=turn,
                            conversation=conversation,
                            request_key=request_key,
                            revision=revision,
                            next_task=next_task,
                            suspended=suspended,
                            lead_id=selected_id,
                            pending=pending_confirmation,
                            prefix="尚未明确确认当前版本，因此没有保存。\n",
                        )
                if extracted.get("_contract_amount_conflict"):
                    # The raw message remains in FollowUp.content; do not persist a
                    # model-rewritten structured record before the salesperson clarifies.
                    extracted["record"] = {}
                if (
                    not confirmed_pending_record
                    and extracted.get("mode") != "simulation"
                    and _should_review_record(extracted)
                ):
                    await db.commit()
                    failed_step = "跟进记录核对"

                    async def review_record():
                        async with _MODEL_SLOTS:
                            return await _review_final_record(project_context, extracted)

                    reviewed = await asyncio.wait_for(
                        review_record(),
                        timeout=max(0.01, deadline - asyncio.get_running_loop().time()),
                    )
                    extracted["record"] = reviewed["record"]
                    extracted["_record_review"] = reviewed
                    await _check_object(db, user, selected_id)
                    if (reviewed.get("proof_validation") or {}).get("status") == "rejected":
                        record = reviewed["record"]
                        canonical_record = json.dumps(
                            record, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                        )
                        version_id = _record_confirmation_version(
                            selected_id, turn_id, record
                        )
                        pending_confirmation = {
                            "project_id": selected_id,
                            "source_turn_id": turn_id,
                            "source_message_sha256": hashlib.sha256(
                                content.encode("utf-8")
                            ).hexdigest(),
                            "dossier_revision": business_context["revision"],
                            "record": record,
                            "record_sha256": hashlib.sha256(
                                canonical_record.encode("utf-8")
                            ).hexdigest(),
                            "version_id": version_id,
                            "extracted": copy.deepcopy(extracted),
                            "proof_validation": reviewed["proof_validation"],
                            "record_review": reviewed,
                        }
                        return await _await_record_confirmation(
                            db,
                            turn=turn,
                            conversation=conversation,
                            request_key=request_key,
                            revision=revision,
                            next_task=next_task,
                            suspended=suspended,
                            lead_id=selected_id,
                            pending=pending_confirmation,
                        )
            projected = {
                **business_context["facts"],
                **{u["key"]: u["value"] for u in extracted["updates"]},
            }
            updated_requirement_fields = {
                update["key"]
                for update in extracted["updates"]
                if update.get("key") in {"need", "scenario", "scope"}
                and projected.get(update["key"])
                != business_context["facts"].get(update["key"])
            }
            has_requirements_candidate = bool(
                business_context.get("requirements_report_candidate")
                or business_context.get("requirements_report")
            )
            material_requirements_change = bool(updated_requirement_fields)
            report_stage = business_context.get("stage")
            can_create_requirements_report = (
                report_stage == "s2_customer_reached" and not has_requirements_candidate
            )
            can_update_requirements_report = (
                has_requirements_candidate
                and report_stage in {"s2_customer_reached", "s3_proposal_quote"}
                and material_requirements_change
            )
            if (
                extracted.get("mode") != "simulation"
                and not resume_checkpoint
                and (
                    can_create_requirements_report
                    or can_update_requirements_report
                )
                and projected.get("need")
                and (extracted.get("interaction") or {}).get("need_maturity")
                == "specific"
            ):
                analysis_skill_id = "project-requirements-report"
                report_context = {
                    **business_context,
                    "facts": projected,
                    "message": "根据客户事实整理一页需求报告",
                }
                report_context["evidence"] = [
                    {"key": "E0", "content": content},
                    *[
                        e
                        for e in business_context["evidence"]
                        if e["key"] != "E0" and e["content"]
                    ],
                ]
                analysis_context_used = report_context
                await db.commit()

                async def auto_report():
                    async with _MODEL_SLOTS:
                        return await assistant_analysis.generate(
                            analysis_skill_id, report_context
                        )

                failed_step = "需求报告生成"
                analysis_result = await asyncio.wait_for(
                    auto_report(),
                    timeout=max(0.01, deadline - asyncio.get_running_loop().time()),
                )
                await _check_object(db, user, selected_id)
        if (
            extracted is not None
            and extracted.get("mode") != "simulation"
            and decision["skill_id"] == "sales-project-followup"
        ):
            # T10/R06宿主保证：分期签约歧义（unclear/explicitly_pending）跨轮保持，
            # 只有本轮明确single/multiple才解锁经费硬门槛；"已询问时保留未明确"
            # 不再依赖模型每轮自觉重报（spec/18:15、spec/03:272）。
            raw_arrangement = (extracted.get("interaction") or {}).get(
                "contract_arrangement_status"
            )
            archived_dossier = await dossier_service.get(db, selected_id)
            prior_arrangement = business_context["facts"].get(
                "contract_arrangement_status", next_task.get("contract_arrangement_status")
            )
            prior_time = (
                dossier_service.proof_effective_time(
                    (archived_dossier.evidence or {}).get("contract_arrangement_status")
                )
                if archived_dossier
                else None
            )
            incoming_time = (
                assistant_facts.parse_time(extracted.get("occurred_at")) or datetime.now()
            )
            if prior_time and incoming_time < prior_time:
                # S03：这里的时序基准必须与档案写入护栏（dossier_service.persist）一致，
                # 用该字段「最近一次确认/变更」的业务发生时间，而不是首次确立时间；
                # 否则档案仍保持 unclear，阶段输入和会话任务却被更早的补录提前解锁。
                raw_arrangement = None  # 历史补录不能解除较新的项目约束。
            blocked_states = {"unclear", "explicitly_pending"}
            resolved_states = {"single", "multiple"}
            if raw_arrangement in blocked_states | resolved_states:
                effective_arrangement = raw_arrangement
            elif (
                raw_arrangement in {None, "not_discussed"}
                and prior_arrangement in blocked_states | resolved_states
            ):
                effective_arrangement = prior_arrangement
            else:
                effective_arrangement = raw_arrangement
            next_task["contract_arrangement_status"] = effective_arrangement
            if raw_arrangement in blocked_states | resolved_states:
                extracted["updates"].append(
                    {
                        "key": "contract_arrangement_status",
                        "value": raw_arrangement,
                        "quote": content,
                        "update_mode": "replace",
                    }
                )
            judge_context = {
                **project_context,
                "contract_completion": extracted.get("_contract_completion"),
                "contract_arrangement_status": effective_arrangement,
                # 本轮事实的业务发生时间（补录时早于当前时刻），
                # 判定层用它给本轮条件候选定时序，判断是否是事后补录。
                "turn_occurred_at": extracted.get("occurred_at"),
                "facts": {
                    **business_context["facts"],
                    **{u["key"]: u["value"] for u in extracted["updates"]},
                },
            }
            from app.core.dicts import STAGE_ORDER

            current_index = STAGE_ORDER.index(project_context["stage"])
            judge_context["standards"] = [
                s
                for s in project_context["standards"]
                if s["key"] not in project_context.get("completed_standard_keys", [])
                and (
                    # 当前环节全部；后续环节只下发硬门槛。
                    s["stage"] == project_context["stage"]
                    or (STAGE_ORDER.index(s["stage"]) > current_index and s["hard_gate"])
                    # 已推进后，前阶段仍未达成的标准仍可被本轮反馈补足（Spec20 §3.2）：
                    # 只补记达成与来源，不回退阶段，也不把非硬门槛变成推进门槛。
                    or STAGE_ORDER.index(s["stage"]) < current_index
                )
            ]
            if (
                analysis_result is not None
                and analysis_result.get("requirements_status") == "ready"
                and business_context.get("stage") == "s2_customer_reached"
            ):
                report_text = "\n".join(
                    s["title"] + "：" + s["content"]
                    for s in analysis_result["sections"]
                )
                report_source = {
                    "key": "ERNEW",
                    "content": "已根据客户事实整理需求报告：" + report_text,
                }
                judge_context["evidence"] = [*judge_context["evidence"], report_source]
                business_context["evidence"] = judge_context["evidence"]
            await db.commit()

            async def judge_stage():
                async with _MODEL_SLOTS:
                    return await assistant_stage_judge.judge(judge_context)

            failed_step = "阶段判断"
            try:
                stage_judgements = await asyncio.wait_for(
                    judge_stage(),
                    timeout=max(0.01, deadline - asyncio.get_running_loop().time()),
                )
            except Exception as exc:
                if isinstance(
                    exc, (PermissionDeniedError, ConflictError, NotFoundError)
                ):
                    raise
                # 判断失败不能丢弃已校验的跟进；本轮不执行任何阶段操作。
                extracted["_stage_unavailable"] = type(exc).__name__
                stage_judgements = {"items": []}
                logging.getLogger(__name__).warning(
                    "阶段判断暂停 type=%s", type(exc).__name__
                )
            extracted["standards"] = [
                {
                    **{
                        k: item[k]
                        for k in ("standard_key", "evidence_key", "quote", "reason")
                    },
                    # 固定双条件的分项结果随判定交付宿主（结构与来源，不只是展示备注）。
                    "conditions": item.get("conditions") or [],
                }
                for item in stage_judgements["items"]
                if item["achieved"]
            ]
            # 条件状态要跨轮/跨会话累计：本轮未达成标准的已核验条件也必须落库。
            extracted["_condition_states"] = [
                {
                    "standard_key": item["standard_key"],
                    "conditions": item.get("conditions") or [],
                }
                for item in stage_judgements["items"]
                if item.get("conditions")
            ]
            extracted["_judgements"] = stage_judgements["items"]
            business_context["evidence"].extend(
                stage_judgements.get("_expanded_evidence", [])
            )
            await _check_object(db, user, selected_id)
            if analysis_result is not None:
                project_context["requirements_assessment"] = {
                    "status": analysis_result.get("requirements_status"),
                    "gaps": analysis_result.get("requirement_gaps", []),
                }
            if not extracted.get("_stage_unavailable"):
                dialogue_work = {
                    "context": {
                        k: project_context.get(k)
                        for k in (
                            "message",
                            "project",
                            "stage",
                            "known",
                            "record",
                            "record_occurred_at",
                            "asked_topics",
                            "pending_questions",
                            "actions",
                            "standards",
                            "requirements_assessment",
                        )
                    },
                    "extracted": dict(extracted),
                    "stage_result": stage_judgements,
                }
                extracted["question"] = None
                extracted["interaction"] = {
                    **(extracted.get("interaction") or {}),
                    "decision": "close",
                    "question_topic": "none",
                }
        if decision["transition"] != "clarify" and decision["skill_id"] in {
            "project-diagnosis",
            "project-requirements-report",
            "project-dossier-management",
        }:
            business_context = await dossier_service.snapshot(
                db, user, selected_id, compact=True
            )
            if decision["skill_id"] == "project-dossier-management":
                workspace_result = {
                    "facts": business_context["facts"],
                    "record": business_context["record"],
                    "stage": business_context["stage"],
                    "customer": business_context["customer"],
                    "revision": business_context["revision"],
                }
            else:
                analysis_skill_id = decision["skill_id"]
                analysis_context = {
                    **business_context,
                    "message": content,
                    "now": datetime.now().isoformat(),
                }
                analysis_context["evidence"] = [
                    e for e in analysis_context["evidence"] if e["content"]
                ]
                analysis_context["actions"] = [
                    {k: v for k, v in a.items() if k != "id"}
                    for a in analysis_context["actions"]
                ]
                analysis_context_used = analysis_context
                await db.commit()

                async def analyze():
                    async with _MODEL_SLOTS:
                        return await assistant_analysis.generate(
                            decision["skill_id"], analysis_context
                        )

                failed_step = "报告分析"
                analysis_result = await asyncio.wait_for(
                    analyze(),
                    timeout=max(0.01, deadline - asyncio.get_running_loop().time()),
                )
                await _check_object(db, user, selected_id)
        result = {
            "turn_id": turn_id,
            "task": next_task,
            "question": decision.get("question"),
            "reason": decision["reason"],
            "business_executed": False,
            "reply": decision.get("question")
            or "已完成任务路由，业务skill执行尚未接入。",
        }
        failed_step = "业务保存"
        changed = await db.execute(
            update(AssistantConversation)
            .where(
                AssistantConversation.id == conversation_id,
                AssistantConversation.active_request == request_key,
                AssistantConversation.revision == revision + 1,
            )
            .values(
                task=next_task,
                lead_id=selected_id,
                suspended_tasks=suspended[-20:],
                active_request=None,
                processing_since=None,
                revision=revision + 2,
            )
        )
        if changed.rowcount != 1:
            raise ConflictError("路由结果已过期，未覆盖新的会话状态")
        if workspace_result is not None:
            result.update(
                dossier=workspace_result,
                reply="已读取项目档案。",
                business_executed=False,
            )
        if analysis_result is not None:
            from app.models.ai import AiDiagnosis

            current_dossier = await dossier_service.get(db, selected_id)
            if (
                current_dossier.revision if current_dossier else None
            ) != business_context["revision"]:
                raise ConflictError("项目事实已变化，请重新生成报告")
            trigger = (
                "requirements"
                if analysis_skill_id == "project-requirements-report"
                else "manual"
            )
            report_evidence = (
                analysis_context_used.get("evidence")
                if analysis_context_used
                else business_context["evidence"]
            )
            report_structured = {
                **analysis_result,
                "evidence": report_evidence,
                "dossier_revision": business_context["revision"] or 0,
            }
            previous_reports = list(
                (
                    await db.execute(
                        select(AiDiagnosis)
                        .where(
                            AiDiagnosis.lead_id == selected_id,
                            AiDiagnosis.trigger == trigger,
                        )
                        .order_by(AiDiagnosis.created_at, AiDiagnosis.id)
                    )
                )
                .scalars()
                .all()
            )
            previous = previous_reports[-1] if previous_reports else None
            report_structured.update(
                analysis_version=len(previous_reports) + 1,
                supersedes_report_id=previous.id if previous else None,
                source_turn_id=turn_id,
                source_message_sha256=hashlib.sha256(
                    content.encode("utf-8")
                ).hexdigest(),
            )
            report_basis = {
                "facts": (analysis_context_used or {}).get("facts")
                or business_context["facts"],
                "evidence": report_evidence or [],
                "dossier_revision": business_context["revision"] or 0,
            }
            report_structured["analysis_basis_sha256"] = hashlib.sha256(
                json.dumps(
                    report_basis,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            if trigger == "requirements":
                report_structured["report_version"] = len(previous_reports) + 1
            report = AiDiagnosis(
                lead_id=selected_id,
                lead_name=business_context["customer"],
                stage=business_context["stage"],
                trigger=trigger,
                content="\n\n".join(
                    s["title"] + "\n" + s["content"]
                    for s in analysis_result["sections"]
                ),
                structured=report_structured,
                created_by=user.id,
                created_by_name=user.name,
                parse_mode="llm",
            )
            db.add(report)
            await db.flush()
            if (
                analysis_skill_id == "project-requirements-report"
                and analysis_result.get("requirements_status") == "ready"
            ):
                report_lead = await lead_service.get_lead(db, selected_id)
                lead_service.assert_can_edit(user, report_lead)
                if extracted is not None:
                    extracted["_requirements_report"] = {
                        "id": report.id,
                        "content": report.content,
                    }
                elif report_lead.current_stage == "s2_customer_reached" and all(
                    business_context["facts"].get(k) for k in ("need", "scenario")
                ):
                    from app.services import stage_service

                    standards = await stage_service.list_standards(
                        db, report_lead.current_stage
                    )
                    gate = next((s for s in standards if s.seq == "2"), None)
                    if gate:
                        await stage_service.toggle_standard(
                            db,
                            user=user,
                            lead=report_lead,
                            standard_id=gate.id,
                            is_done=True,
                            evidence=f"AI需求报告{report.id}：" + report.content,
                            source="ai",
                        )
                        progress = await stage_service.stage_progress(db, report_lead)
                        if progress["hard_gate_passed"]:
                            await stage_service.advance_stage(
                                db,
                                user=user,
                                lead=report_lead,
                                basis=f"AI已根据客户事实生成需求报告{report.id}",
                            )
                            result["stage"] = {
                                "before": "s2_customer_reached",
                                "after": report_lead.current_stage,
                                "status": report_lead.status,
                                "gaps": [],
                            }
            result.update(
                report={
                    "id": report.id,
                    **analysis_result,
                    **{
                        key: report_structured[key]
                        for key in (
                            "analysis_version",
                            "report_version",
                            "supersedes_report_id",
                            "source_turn_id",
                            "source_message_sha256",
                            "analysis_basis_sha256",
                        )
                        if key in report_structured
                    },
                },
                business_executed=True,
                reply="报告已生成并保存。建议采纳后进入待办。",
            )
        if extracted is not None and extracted.get("mode") == "simulation":
            preview_facts = dict(next_task.get("simulation_facts") or {})
            preview_facts.update({u["key"]: u["value"] for u in extracted["updates"]})
            next_task.update(
                simulation_facts=preview_facts,
                mode="simulation",
                state="awaiting_user" if extracted.get("question") else "ready",
                pending_questions=[extracted["question"]]
                if extracted.get("question")
                else [],
            )
            result.update(
                task=next_task,
                reply="模拟讨论，未写入业务记录。" + (extracted.get("question") or ""),
                question=extracted.get("question"),
                interaction=extracted.get("interaction"),
                preview=extracted,
            )
            await db.execute(
                update(AssistantConversation)
                .where(AssistantConversation.id == conversation_id)
                .values(task=next_task)
            )
        elif extracted is not None and decision["skill_id"] == "sales-project-followup":
            next_task["mode"] = extracted.get("mode", "live")
            if extracted.get("_pending_project_candidates"):
                next_task["pending_project_candidates"] = copy.deepcopy(
                    extracted["_pending_project_candidates"]
                )
            else:
                next_task.pop("pending_project_candidates", None)
            next_task, outcome, reply = await assistant_projects.apply(
                db,
                user=user,
                task=next_task,
                extracted=extracted,
                context=business_context,
                message=business_message,
                turn_id=business_turn_id,
            )
            result.update(
                task=next_task,
                business_executed=True,
                reply=reply,
                question=extracted.get("question"),
                interaction=extracted.get("interaction"),
                standard_judgements=extracted.get("_judgements", []),
                amount_normalizations=extracted.get("_amount_normalizations", []),
                finance_review=extracted.get("_finance_review"),
                project_attribution=extracted.get("_project_attribution"),
                record_review=extracted.get("_record_review"),
                **outcome,
            )
            if confirmation_receipt:
                saved_confirmation = {
                    **confirmation_receipt,
                    "status": "saved",
                    "record_id": outcome["followup_id"],
                }
                next_task.setdefault("record_confirmations", []).append(saved_confirmation)
                result["record_confirmation"] = saved_confirmation
                if confirmation_source_turn is not None:
                    confirmation_source_turn.result = {
                        **(confirmation_source_turn.result or {}),
                        "overall_status": "confirmed_and_saved",
                        "business_executed": True,
                        "record_confirmation": saved_confirmation,
                    }
                    confirmation_source_turn.checkpoint = None
            partial = bool(extracted.get("_stage_unavailable"))
            result["overall_status"] = "partial" if partial else "succeeded"
            result["operation_receipts"] = [
                {
                    "operation": "followup",
                    "status": "succeeded",
                    "record_id": outcome["followup_id"],
                },
                {
                    "operation": "stage",
                    "status": "failed" if partial else "succeeded",
                    "retryable": partial,
                    "error": "阶段判断未完成" if partial else None,
                },
            ]
            if confirmation_receipt:
                result["operation_receipts"].append(
                    {
                        "operation": "record_confirmation",
                        "status": "succeeded",
                        "version_id": confirmation_receipt["version_id"],
                    }
                )
            if partial:
                result["reply"] = (
                    "跟进已保存，阶段判断未完成。可重试原消息恢复判断，不会重复记录。"
                )
                result["question"] = None
                result["interaction"] = {
                    **(extracted.get("interaction") or {}),
                    "decision": "close",
                    "question_topic": "none",
                    "reason": "阶段判断未完成，先保存有效跟进并等待恢复",
                }
                next_task["pending_questions"] = []
                next_task["state"] = "ready"
                # 检查点只保存在原消息内部列，不把执行控制数据暴露给LLM。
                recovery = {
                    k: v for k, v in extracted.items() if k != "_saved_followup"
                }
                turn.checkpoint = {
                    "kind": "stage",
                    "lead_id": selected_id,
                    "followup_id": outcome["followup_id"],
                    "dossier_revision": outcome["dossier_revision"],
                    "actions": outcome["actions"],
                    "skill_revision": next_task["skill_revision"],
                    "extracted": recovery,
                    "recovery_history": copy.deepcopy(recovery_history),
                }
            else:
                turn.checkpoint = None
            await db.flush()
            await db.execute(
                update(AssistantConversation)
                .where(AssistantConversation.id == conversation_id)
                .values(task=next_task, lead_id=next_task.get("lead_id"))
            )
        elif extracted is not None:
            next_task["mode"] = extracted.get("mode", "live")
            if extracted.get("new_record"):
                next_task.pop("contact_followup_id", None)
            next_task, executed, reply = await assistant_leads.apply(
                db,
                user=user,
                task=next_task,
                extracted=extracted,
                message=content,
                turn_id=turn_id,
            )
            if next_task.get("lead_id"):
                saved_lead = await lead_service.get_lead(db, next_task["lead_id"])
                dossier = await dossier_service.persist(
                    db,
                    user=user,
                    lead=saved_lead,
                    task=next_task,
                    extracted=extracted,
                    message=content,
                    turn_id=turn_id,
                    revision=business_context["revision"],
                    followup_id=next_task.get("contact_followup_id"),
                )
                action_results = await dossier_service.apply_actions(
                    db,
                    user=user,
                    lead=saved_lead,
                    extracted=extracted,
                    context=business_context,
                    turn_id=turn_id,
                )
                if saved_lead.current_stage:
                    await dossier_service.refresh_risks(
                        db, lead=saved_lead, dossier=dossier
                    )
                    from app.services import stage_service

                    stage_progress = await stage_service.stage_progress(db, saved_lead)
                    result["stage"] = {
                        "before": business_context.get("stage"),
                        "after": saved_lead.current_stage,
                        "status": saved_lead.status,
                        "gaps": stage_progress["missing_hard_gates"],
                    }
                result.update(actions=action_results, dossier_revision=dossier.revision)
            result.update(
                task=next_task,
                business_executed=executed,
                reply=reply,
                question=next_task.get("pending_questions", [None])[0]
                if next_task.get("pending_questions")
                else None,
            )
            await db.execute(
                update(AssistantConversation)
                .where(
                    AssistantConversation.id == conversation_id,
                )
                .values(task=next_task, lead_id=next_task.get("lead_id"))
            )
        if result.get("report"):
            receipts = result.setdefault("operation_receipts", [])
            receipts.append(
                {
                    "operation": "report",
                    "status": "succeeded",
                    "report_id": result["report"]["id"],
                    "version": result["report"].get("report_version")
                    or result["report"].get("analysis_version"),
                }
            )
        if resume_checkpoint:
            result["recovery_history"] = copy.deepcopy(recovery_history)
        await db.execute(
            update(AssistantTurn)
            .where(AssistantTurn.id == turn_id)
            .values(
                status="routed",
                result=result,
            )
        )
        if dialogue_work is not None and result.get("overall_status") == "succeeded":
            turn.checkpoint = {
                "kind": "dialogue",
                "lead_id": selected_id,
                "dossier_revision": result["dossier_revision"],
                # 与stage型检查点同等锚定：中断轮的生效规则版本必须可追溯（T09发布快照）。
                "skill_revision": next_task.get("skill_revision"),
                "work": dialogue_work,
                "recovery_history": copy.deepcopy(
                    result.get("recovery_history") or recovery_history
                ),
            }
            result["overall_status"] = "partial"
            result["operation_receipts"].append(
                {
                    "operation": "dialogue",
                    "status": "failed",
                    "retryable": True,
                    "error": "收尾回复待完成",
                }
            )
            turn.result = result
            await db.execute(
                update(AssistantConversation)
                .where(AssistantConversation.id == conversation_id)
                .values(active_request=request_key, processing_since=datetime.now())
            )
            await db.commit()
            return await _settle_dialogue(db, user, turn, conversation_id, request_key)
        await db.commit()
        return result
    except BaseException as exc:
        logging.getLogger(__name__).warning(
            "助手请求失败 step=%s type=%s stack=%s",
            failed_step,
            type(exc).__name__,
            "".join(traceback.format_tb(exc.__traceback__)),
        )
        await db.rollback()
        # 取消也结算路由状态；业务原文保留，不记录provider异常细节或密钥。
        released = await db.execute(
            update(AssistantConversation)
            .where(
                AssistantConversation.id == conversation_id,
                AssistantConversation.active_request == request_key,
                AssistantConversation.revision == revision + 1,
            )
            .values(active_request=None, processing_since=None)
        )
        if released.rowcount == 1:
            await db.execute(
                update(AssistantTurn)
                .where(
                    AssistantTurn.id == turn_id,
                    AssistantTurn.status == "processing",
                )
                .values(
                    status="failed", error=f"{failed_step}未完成，可使用原请求标识重试"
                )
            )
        await db.commit()
        if isinstance(
            exc,
            (
                ValidationError,
                ConflictError,
                PermissionDeniedError,
                NotFoundError,
                asyncio.CancelledError,
            ),
        ):
            raise
        raise AiUnavailableError(f"{failed_step}未完成，消息已保留，可重试") from exc


async def _settle_dialogue(db, user, turn, conversation_id, request_key):
    """业务已提交后的同一主节点续接；失败只恢复回复。"""
    checkpoint = turn.checkpoint
    work = checkpoint["work"]
    result = dict(turn.result)
    result["recovery_history"] = copy.deepcopy(
        checkpoint.get("recovery_history") or result.get("recovery_history") or []
    )
    context = dict(work["context"])
    context["skill_revision"] = checkpoint.get("skill_revision")
    context["known"] = dict(result["task"].get("facts") or {})
    context["execution_result"] = {
        k: result.get(k) for k in ("business_executed", "record", "stage", "actions")
    }
    prior_pending = (
        []
        if work["extracted"].get("new_record")
        else list(work["context"].get("pending_questions") or [])
    )
    await db.commit()
    try:
        dialogue = _unresolved_contract_dialogue(work["extracted"])
        if dialogue is None:
            async with _MODEL_SLOTS:
                dialogue = await asyncio.wait_for(
                    assistant_facts.continue_followup(
                        context, work["extracted"], work["stage_result"]
                    ),
                    timeout=get_settings().LLM_TIMEOUT_SECONDS,
                )
        pending_index = dialogue.get("pending_question_index")
        if dialogue.get("pending_question_answered") is True and pending_index is not None:
            raise ValidationError("已回答的问题不能再次作为未答引用")
        if pending_index is not None and not (
            type(pending_index) is int and 0 <= pending_index < len(prior_pending)
        ):
            raise ValidationError("待答问题引用不属于本轮上下文")
    except Exception as exc:
        logging.getLogger(__name__).warning("已保存跟进的收尾回复暂停 type=%s", type(exc).__name__)
        result["reply"] = "跟进已保存，收尾回复未完成。可重试，不会重复记录。"
        result["question"] = None
    else:
        await _check_object(db, user, checkpoint["lead_id"])
        dossier = await dossier_service.get(db, checkpoint["lead_id"])
        if dossier is None or dossier.revision != checkpoint["dossier_revision"]:
            raise ConflictError("项目事实已变化，已保存跟进保持不变，请重新读取后继续")
        task = dict(result["task"])
        question = dialogue["question"]

        def normalize(value: str | None):
            return "".join((value or "").split())

        resent = pending_index is not None or (
            bool(question) and any(normalize(question) == normalize(p) for p in prior_pending)
        )
        if resent:
            # 同一缺口由主节点识别并引用；Host只维护引用状态，不解释问句语义。
            task["pending_questions"] = (
                [prior_pending[pending_index]]
                if pending_index is not None
                else [p for p in prior_pending if normalize(p) == normalize(question)][:1]
            )
            task["state"] = "awaiting_user"
            result.update(
                task=task,
                question=None,
                overall_status="succeeded",
                reply=dialogue["acknowledgement"],
            )
            result["interaction"] = {
                "decision": "close",
                "question_topic": "none",
                "reason": "已问缺口未重发",
                "decision_basis": dialogue.get("decision_basis"),
            }
        else:
            task["pending_questions"] = [question] if question else []
            if work["extracted"].get("_contract_amount_conflict"):
                task["pending_questions"].extend(
                    pending
                    for pending in prior_pending
                    if normalize(pending) != normalize(question)
                )
            task["state"] = "awaiting_user" if question else "ready"
            if question:
                task["asked_topics"] = list(
                    dict.fromkeys([*(task.get("asked_topics") or []), dialogue["question_topic"]])
                )
            result.update(
                task=task,
                question=question,
                overall_status="succeeded",
                reply=dialogue["acknowledgement"] + ("\n" + question if question else ""),
            )
            result["interaction"] = {
                "decision": "ask" if question else "close",
                "question_topic": dialogue["question_topic"],
                "decision_basis": dialogue.get("decision_basis"),
            }
        result["operation_receipts"] = [
            *[r for r in result["operation_receipts"] if r["operation"] != "dialogue"],
            {"operation": "dialogue", "status": "succeeded"},
        ]
        turn.checkpoint = None
    released = await db.execute(
        update(AssistantConversation)
        .where(
            AssistantConversation.id == conversation_id,
            AssistantConversation.active_request == request_key,
        )
        .values(active_request=None, processing_since=None, task=result["task"])
    )
    if released.rowcount != 1:
        raise ConflictError("会话已变化，未覆盖收尾回复")
    turn.result = result
    turn.status = "routed"
    await db.commit()
    return result
