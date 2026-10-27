"""线索skill结果的领域写入；仅在会话CAS成功后的事务中执行。"""

from datetime import datetime

from sqlalchemy import select

from app.ai.assistant_facts import parse_time
from app.config import get_settings
from app.core.errors import ConflictError, ValidationError
from app.models.activity import FollowUp
from app.models.enums import LeadStatus
from app.models.lead import Contact, Lead
from app.schemas import LeadCreate
from app.services import audit_service, calendar_service, lead_service

FOUR = ("name", "source", "contact_name", "contact_position")


async def snapshot(db, user, lead_id):
    if not lead_id:
        return {}
    lead = await lead_service.get_lead(db, lead_id)
    lead_service.assert_can_edit(user, lead)
    contacts = (
        (
            await db.execute(
                select(Contact)
                .where(Contact.lead_id == lead_id)
                .order_by(Contact.is_primary.desc(), Contact.created_at)
            )
        )
        .scalars()
        .all()
    )
    return {
        "name": lead.name,
        "source": lead.source,
        "contact_name": contacts[0].name if contacts else None,
        "contact_position": contacts[0].position if contacts else None,
    }


async def apply(db, *, user, task, extracted, message, turn_id):
    facts = dict(task.get("facts") or {})
    evidence = dict(task.get("evidence") or {})
    updates = {u["key"]: u["value"] for u in extracted["updates"]}
    facts.update(updates)
    for update in extracted["updates"]:
        evidence[update["key"]] = {"turn_id": turn_id, "quote": update["quote"]}
    task = {**task, "facts": facts, "evidence": evidence}
    missing = [k for k in FOUR if not facts.get(k)]
    if missing:
        labels = {
            "name": "客户名称",
            "source": "来源",
            "contact_name": "联系人",
            "contact_position": "职位",
        }
        question = (
            extracted.get("question") or "请补充" + "、".join(labels[k] for k in missing[:2]) + "。"
        )
        return {**task, "state": "awaiting_user", "pending_questions": [question]}, False, question

    lid = task.get("lead_id")
    created = False
    if not lid:
        # 同名待核对，不静默合并或创建重复业务对象；不泄露他人线索。
        existing = (
            await db.execute(
                select(Lead.id)
                .where(
                    Lead.is_deleted.is_(False),
                    Lead.name == facts["name"],
                )
                .limit(1)
            )
        ).first()
        if existing:
            question = "可能已有同名客户，请从有权限的线索列表选择要补充的记录。"
            return (
                {**task, "state": "awaiting_user", "pending_questions": [question]},
                False,
                question,
            )
        lead, _ = await lead_service.create_lead(
            db,
            user=user,
            payload=LeadCreate(
                name=facts["name"],
                source=facts["source"],
                contact_name=facts["contact_name"],
                contact_position=facts["contact_position"],
            ),
        )
        lid, created = lead.id, True
    else:
        lead = await lead_service.get_lead(db, lid)
        lead_service.assert_can_edit(user, lead)
        if lead.current_stage or lead.status in {
            LeadStatus.VALID,
            LeadStatus.SIGNED,
            LeadStatus.LOST,
        }:
            raise ConflictError("此记录已进入商机，请使用项目进度跟进")
        # 对已有记录的四项更正暂不静默执行，避免客户层归属未同步。
        if any(
            k in updates and updates[k] != (task.get("original_fields") or {}).get(k) for k in FOUR
        ):
            raise ValidationError("基础资料更正请先在资料页面确认，暂未接入跨客户迁移")

    validation_changed = bool({"info_verified", "intent", "rejected", "contacted"} & updates.keys())
    state = "pending"
    if facts.get("info_verified") is False or facts.get("rejected") is True:
        state = "invalid"
    elif facts.get("info_verified") is True and facts.get("intent") in {
        "report_meeting",
        "wechat_material",
    }:
        state = "valid"
    if validation_changed:
        now = parse_time(extracted.get("occurred_at")) or datetime.now()
        follow = (
            await db.get(FollowUp, task["contact_followup_id"])
            if task.get("contact_followup_id") and not extracted.get("new_record")
            else None
        )
        if follow is None:
            follow = FollowUp(
                lead_id=lid,
                user_id=user.id,
                user_name=user.name,
                method="other",
                followed_at=now,
                content=message,
                is_valid=state,
                next_action=facts.get("intent") or "other",
                info_verified=facts.get("info_verified") is True,
                source="ai",
            )
            db.add(follow)
            await db.flush()
        else:
            if follow.lead_id != lid or follow.user_id != user.id:
                raise ValidationError("联系记录归属不匹配")
            follow.content += "\n" + message
            follow.is_valid = state
            follow.next_action = facts.get("intent") or "other"
            follow.info_verified = facts.get("info_verified") is True
        task["contact_followup_id"] = follow.id
        lead.last_follow_at = max(lead.last_follow_at or now, now)
        if lead.first_follow_at is None or now < lead.first_follow_at:
            lead.first_follow_at = now
        if state == "valid":
            await lead_service.mark_as_opportunity(db, lead=lead, user=user)
        elif state == "invalid":
            await lead_service.mark_invalid(db, lead=lead, user=user)
        else:
            lead.status = LeadStatus.FOLLOWING
        if lead.owner_id and state != "valid":
            lead.recycle_deadline = await calendar_service.deadline_after_working_days(
                db,
                now,
                get_settings().LEAD_RECYCLE_WORKING_DAYS,
            )
        await audit_service.write_log(
            db,
            operator=user,
            action="assistant_validate",
            target_type="lead",
            target_id=lid,
            target_name=lead.name,
            detail=f"AI联系事实验证：{state}；来源消息{turn_id}",
        )
    task.update(lead_id=lid, validation_state=state, original_fields={k: facts[k] for k in FOUR})
    if state == "valid":
        task.update(state="completed", pending_questions=[])
        reply = "客户信息核实正确且已有推进意愿，已转为有效商机。后续项目进展可继续告诉我。"
    else:
        question = extracted.get("question")
        if state == "pending" and facts.get("info_verified") is None and facts.get("intent") in {
            "report_meeting", "wechat_material",
        }:
            # 已有推进意愿但缺核实结论时，必须把验证交回销售确认，不能静默结束。
            question = "请确认：上述客户名称、联系人及职位等信息是否已核实属实？"
        elif not created and not validation_changed:
            question = question or "这次联系客户的结果是什么？"
        task.update(
            state="awaiting_user" if question else "ready",
            pending_questions=[question] if question else [],
        )
        reply = (
            "线索已记录。"
            if created
            else "联系结果已记录。"
            if validation_changed
            else "本次未新增联系事实。"
        ) + (
            "客户明确拒绝或信息错误，当前为无效线索，可后续重新验证。"
            if state == "invalid"
            else "当前待验证，不因未回复判无效。"
        )
        if question:
            reply += question
    return task, created or validation_changed, reply
