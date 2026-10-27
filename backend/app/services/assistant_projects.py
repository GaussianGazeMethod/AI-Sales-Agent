"""项目跟进Skill的结构化记录、证据与阶段流转执行。"""

import logging
from datetime import datetime

from sqlalchemy import select

from app.ai.assistant_facts import parse_time
from app.core.dicts import STAGE_LABELS, STAGE_ORDER
from app.core.errors import ConflictError, ValidationError
from app.core.sales_rules import (
    PROPOSAL_QUOTE_RULE,
    combine_proposal_quote,
    combo_rule_revision,
    fact_key,
    is_proposal_quote_standard,
    states_from_facts,
)
from app.models.activity import FollowUp
from app.models.enums import LeadStatus
from app.models.lead import Lead
from app.services import dossier_service, lead_service, stage_service

logger = logging.getLogger(__name__)


async def prepare(db, user, lead_id, message):
    context = await dossier_service.snapshot(db, user, lead_id, compact=True)
    standards = await stage_service.list_standards(db)
    context["standards"] = [
        {
            "key": f"S{i}",
            "id": s.id,
            "stage": s.stage,
            "seq": s.seq,
            "content": s.content,
            "hard_gate": s.is_hard_gate,
            "evidence_hint": s.evidence_hint,
        }
        for i, s in enumerate(standards, 1)
    ]
    lead = await lead_service.get_lead(db, lead_id)
    context["customer_identity"] = {
        "name_present": bool((lead.name or "").strip()),
        "address_present": any(
            (value or "").strip()
            for value in (lead.province, lead.city, lead.district)
        ),
        "organization_type_present": bool((lead.org_type or "").strip()),
    }
    siblings = (
        (
            await db.execute(
                select(Lead).where(
                    Lead.customer_id == lead.customer_id,
                    Lead.owner_id == user.id,
                    Lead.id != lead.id,
                    Lead.is_deleted.is_(False),
                )
            )
        )
        .scalars()
        .all()
        if lead.customer_id
        else []
    )
    context["related_projects"] = [{"name": s.project_name} for s in siblings]
    # 已达成标准跨阶段只查一次：阶段推进后前阶段的达成项同样不重复下发。
    completed_ids = await stage_service.list_done_standard_ids(db, lead)
    context["completed_standard_keys"] = [
        s["key"] for s in context["standards"] if s["id"] in completed_ids
    ]
    context["evidence"][0]["content"] = message
    # 宿主把档案里各固定条件的现行时序水位交给判定层（不进 Prompt，
    # stage_input 只按白名单投影），使校验、累计、实际生效写入与最终 AND
    # 用同一组时序合法的状态：事后补录不能解除更新的事实。
    archived = await dossier_service.get(db, lead.id)
    context["condition_prior_times"] = dossier_service.condition_prior_times(archived)
    # 把"历史累计条件状态 + 各自来源日期"单独交付给阶段节点，
    # 与 facts 平铺的旧状态区分开（历史不是约束，本轮更晚原文可更新）。
    context["prior_condition_states"] = dossier_service.condition_prior_summary(archived)
    return context


async def apply(db, *, user, task, extracted, context, message, turn_id):
    # 项目进展中的泛称“主任”等不覆盖线索四项及原验证结论。
    lead_fields = {
        "name",
        "source",
        "contact_name",
        "contact_position",
        "info_verified",
        "rejected",
        "contacted",
        "intent",
    }
    extracted = {
        **extracted,
        "updates": [u for u in extracted["updates"] if u["key"] not in lead_fields],
    }
    lead = await lead_service.get_lead(db, task["lead_id"])
    lead_service.assert_can_edit(user, lead)
    if lead.current_stage != context["stage"]:
        raise ConflictError("项目阶段已被更新，请重试本次记录")
    if not lead.current_stage or lead.status in {LeadStatus.SIGNED, LeadStatus.LOST}:
        raise ConflictError("项目跟进仅适用于进行中的商机；签约后请进入交付")
    occurred = parse_time(extracted.get("occurred_at")) or datetime.now()
    record = dict(task.get("record") or {})
    if extracted.get("new_record"):
        record = {}
    asked_topics = [] if extracted.get("new_record") else list(task.get("asked_topics") or [])
    topic = (extracted.get("interaction") or {}).get("question_topic")
    if (
        not extracted.get("_stage_unavailable")
        and extracted.get("question")
        and topic
        and topic != "none"
        and topic not in asked_topics
    ):
        asked_topics.append(topic)
    record.update({k: v for k, v in extracted["record"].items() if v is not None})
    saved = extracted.get("_saved_followup")
    if saved:
        follow = await db.get(FollowUp, saved["followup_id"])
        dossier = await dossier_service.get(db, lead.id)
        if (
            follow is None
            or follow.lead_id != lead.id
            or follow.user_id != user.id
            or dossier is None
            or dossier.revision != saved["dossier_revision"]
        ):
            raise ConflictError("已保存跟进或档案版本已变化，需核对后恢复")
        actions = saved["actions"]
    else:
        follow = (
            await db.get(FollowUp, task.get("followup_id") or context.get("followup_id") or "")
            if not extracted.get("new_record")
            else None
        )
        if follow is None:
            follow = FollowUp(
                lead_id=lead.id,
                user_id=user.id,
                user_name=user.name,
                content=message,
                followed_at=occurred,
                stage=lead.current_stage,
                source="ai",
                method="other",
            )
            db.add(follow)
            await db.flush()
        else:
            if follow.lead_id != lead.id or follow.user_id != user.id:
                raise ValidationError("跟进记录归属不匹配")
            follow.content += "\n" + message
        lead.last_follow_at = max(lead.last_follow_at or occurred, occurred)
        lead.recycle_deadline = None
        task.update(
            record=record,
            followup_id=follow.id,
            record_occurred_at=follow.followed_at.isoformat(),
            asked_topics=asked_topics,
        )
        # 固定双条件的已核验状态作为事实更新进入项目档案：沿用 persist 的
        # 未提及不更新、乱序不倒退、同值不重链与 revision CAS 语义；
        # unknown 不落库（不能以默认 unknown 清空已知项），规则修订随来源锚定。
        condition_states = []
        for group in extracted.get("_condition_states") or []:
            for entry in group.get("conditions") or []:
                key = fact_key(entry["condition_id"])
                if not key or entry["state"] == "unknown" or not entry.get("quote"):
                    continue
                update = {
                    "key": key,
                    "value": entry["state"],
                    "quote": entry["quote"],
                    "update_mode": "replace",
                    "rule_revision": combo_rule_revision(),
                }
                # ：引用更早消息的条件是"再引证"，不是本轮新确认：
                # 带上它实际来源的时刻与身份，写入层据此不推水位、不写本轮确认；
                # 该条件首次入档时也必须落在旧消息的真实来源上。
                # 来源身份无法核对（既无跟进ID也无档案记录的 turn_id）时不写库：
                # 宁可少记一条事实，也不把来源伪记成本轮。
                if (
                    entry.get("source_occurred_at")
                    and entry.get("source_is_current_turn") is False
                ):
                    fid = entry.get("source_followup_id") or ""
                    sid = entry.get("source_turn_id") or (
                        f"prior-followup:{fid}" if fid else ""
                    )
                    if not sid:
                        logger.warning(
                            "条件来源无法核对身份，本轮不写入档案 condition=%s",
                            entry["condition_id"],
                        )
                        continue
                    update["source_occurred_at"] = entry["source_occurred_at"]
                    update["source_turn_id"] = sid
                    update["new_confirmation"] = False
                condition_states.append(update)
        if condition_states:
            extracted["updates"] = list(extracted.get("updates") or []) + condition_states
        dossier = await dossier_service.persist(
            db,
            user=user,
            lead=lead,
            task=task,
            extracted=extracted,
            message=message,
            turn_id=turn_id,
            revision=context["revision"],
            record=record,
            followup_id=follow.id,
        )
        actions = await dossier_service.apply_actions(
            db, user=user, lead=lead, extracted=extracted, context=context, turn_id=turn_id
        )
    latest = dossier.facts
    # 金额只接受本轮带原文的事实，旧历史不会覆盖档案中的新金额。
    amount_keys = (
        ("project_amount_fen",)
        if extracted.get("_finance_review") is not None
        else (
            "budget_amount_fen",
            "proposal_amount_fen",
            "award_amount_fen",
            "contract_amount_fen",
        )
    )
    for key in () if saved else amount_keys:
        changed = next((u for u in extracted["updates"] if u["key"] == key), None)
        if changed and latest.get(key) == changed["value"]:
            await stage_service.change_amount(
                db,
                user=user,
                lead=lead,
                amount_fen=changed["value"],
                reason=changed["quote"],
                source="ai",
            )
    standards = {s["key"]: s for s in context["standards"]}
    evidence = {e["key"]: e["content"] for e in context["evidence"]}
    matches = []
    for match in extracted["standards"]:
        standard = standards.get(match["standard_key"])
        source = evidence.get(match["evidence_key"])
        if not standard or not source or match["quote"] not in source:
            raise ValidationError("阶段判定引用了未提供的标准或不存在的原文")
        matches.append((standard, match))
    before = lead.current_stage
    applied: set[str] = set()
    for _ in range(0 if extracted.get("_stage_unavailable") else 7):
        # 已达成判定跨阶段取，前阶段已达成分不被重复下发或覆盖。
        completed_ids = await stage_service.list_done_standard_ids(db, lead)
        for standard, match in matches:
            if standard["id"] in applied:
                # 同一标准本轮只写一次；后续循环只用于阶段级联推进。
                continue
            stage_index = STAGE_ORDER.index(standard["stage"])
            if stage_index > STAGE_ORDER.index(lead.current_stage):
                # 后续阶段的标准不因本轮反馈被提前勾选。
                continue
            if standard["id"] in completed_ids:
                # 首次达成的证据固定；后续变化记入档案，不覆盖原达成依据。
                continue
            if is_proposal_quote_standard(standard["stage"], standard.get("seq")):
                # 勾选只看 persist **实际生效**之后的档案状态。被时序守卫
                # 撤掉的旧补录、被 R01 降级为「未核验」的空引用声称，都不允许在这一层
                # 重新变成达成；校验、累计、写入与最终 AND 从此共用同一组状态。
                if not combine_proposal_quote(states_from_facts(latest)):
                    continue
            if "需求分析报告" in standard["content"] and not (
                extracted.get("_requirements_report") or context.get("requirements_report")
            ):
                # 提到报告不等于已有产物；既有报告与本轮新报告均须真实存在。
                continue
            # 语义达成由阶段节点按原文判定，不再要求提取节点重复提供同义标志。
            # 阶段已推进时仍允许补记前阶段未完成项（Spec20 §3.2）：只写该项达成与来源，
            # 阶段与门槛属性都不变。
            # 达成证据槽仍为单条逐字原文；模型给出的各分项引用并列追加进备注，
            # 使「一条标准并列多个交付物」的达成可以逐项枚举与追溯，不改写也不拼接原文。
            # 达成备注逐项枚举固定条件（含来自项目档案累计的条件），保持可追踪结构；
            # evidence 槽仍是单条逐字原文，不拼接不同条件的原话。
            citations = []
            for cond_def in PROPOSAL_QUOTE_RULE["conditions"]:
                cid = cond_def["id"]
                entry = next(
                    (
                        c
                        for c in (match.get("conditions") or [])
                        if c["condition_id"] == cid
                    ),
                    None,
                )
                state = (entry or {}).get("state") or "unknown"
                bit = f"{cid}={state}"
                if entry and entry.get("quote"):
                    bit += f"：{entry['quote']}"
                elif entry and entry.get("state_source") == "项目档案累计":
                    bit += "（项目档案累计来源，确立时已逐字核验）"
                elif state == "unknown":
                    bit += "（本轮未提及，保持未提及，不清空已知项）"
                citations.append(bit)
            remark = "；".join(
                [match["reason"], *citations, f"组合=AND 规则修订={combo_rule_revision()}"]
            ) if citations else match["reason"]
            await stage_service.toggle_standard(
                db,
                user=user,
                lead=lead,
                standard_id=standard["id"],
                is_done=True,
                evidence=match["quote"],
                remark=remark,
                source="ai",
                allow_prior_stage=stage_index < STAGE_ORDER.index(lead.current_stage),
            )
            applied.add(standard["id"])
        progress = await stage_service.stage_progress(db, lead)
        if not progress["total_count"] or not progress["hard_gate_passed"]:
            break
        if lead.current_stage == "s7_contract_signed":
            if latest.get("contract_signed") is True:
                amount = latest.get("contract_amount_fen")
                if amount is None:
                    break
                await stage_service.sign_contract(
                    db, user=user, lead=lead, contract_amount_fen=amount, sign_date=occurred.date()
                )
            break
        await stage_service.advance_stage(
            db, user=user, lead=lead, basis=f"AI比对生效标准；来源跟进{follow.id}"
        )
    await dossier_service.refresh_risks(db, lead=lead, dossier=dossier)
    progress = await stage_service.stage_progress(db, lead)
    gaps = progress["missing_hard_gates"]
    question = extracted.get("question")
    task.update(
        state="awaiting_user" if question else "ready",
        pending_questions=[question] if question else [],
    )
    return (
        task,
        {
            "record": {
                "recorded_at": datetime.now().isoformat(),
                "occurred_at": follow.followed_at.isoformat(),
                "customer": lead.name,
                **record,
            },
            "followup_id": follow.id,
            "actions": actions,
            "stage": {
                "before": before,
                "after": lead.current_stage,
                "status": lead.status,
                "gaps": gaps,
                "evaluations": [
                    {
                        "standard": standards[item["standard_key"]]["content"],
                        "achieved": item["achieved"],
                        "quote": item.get("quote"),
                        "reason": item["reason"],
                        # ：回执里逐项写清"引用的是哪条消息、什么时候、
                        # 是不是本轮"，让旧引用在记录上就能与本轮确认区分开。
                        "conditions": [
                            {
                                "condition_id": e.get("condition_id"),
                                "state": e.get("state"),
                                "quote": e.get("quote") or "",
                                "source_key": e.get("source_key") or "",
                                "state_source": e.get("state_source") or "",
                                "source_occurred_at": e.get("source_occurred_at") or "",
                                "source_is_current_turn": bool(
                                    e.get("source_is_current_turn", True)
                                ),
                            }
                            for e in (item.get("conditions") or [])
                        ],
                    }
                    for item in extracted.get("_judgements", [])
                    if item["standard_key"] in standards
                ],
            },
            "dossier_revision": dossier.revision,
        },
        followup_reply(
            record=record,
            occurred_at=follow.followed_at,
            customer=lead.name,
            before=before,
            after=lead.current_stage,
            question=question,
            acknowledgement=extracted.get("acknowledgement"),
            evaluations=extracted.get("_judgements", []),
        ),
    )


def followup_reply(
    *,
    record,
    occurred_at,
    customer,
    before,
    after,
    question,
    acknowledgement=None,
    evaluations=None,
):
    """Render the Skill's conversation decision using actual persisted results.

    Semantic interpretation and whether to ask belong to the Skill. This
    renderer never turns a future stage gate into a question or invents facts.
    """
    if question:
        return (acknowledgement or "已记录。") + "\n" + question
    rows = [
        "已整理并保存本次跟进：",
        "跟进日期：" + occurred_at.strftime("%Y-%m-%d"),
        "客户项目：" + customer,
        "行动目标：" + (record.get("action_goal") or "未说明"),
        "客户反馈：" + (record.get("customer_feedback") or "未说明"),
        "达成情况：" + (record.get("achievement") or "未说明"),
        "下一步行动：" + (record.get("next_action") or "待确认"),
    ]
    label = STAGE_LABELS.get(after, after)
    if before != after:
        rows.append("阶段：进入“" + label + "”。")
        reasons = [e["reason"] for e in (evaluations or []) if e.get("achieved")]
        if reasons:
            rows.append("依据：" + reasons[-1])
    else:
        rows.append("阶段：保持“" + label + "”。")
    return "\n".join(rows)
