"""模型输出明确替身，只验证已声明提取事实的合并与领域执行。"""

import uuid

import pytest
from sqlalchemy import select

from app.ai import assistant_router, lead_intake
from app.core.errors import ValidationError
from app.models.activity import FollowUp
from app.models.lead import Contact, Lead
from tests.conftest import auth_headers


def updates(message, **fields):
    return {
        "updates": [{"key": k, "value": v, "quote": message} for k, v in fields.items()],
        "question": None,
    }


@pytest.fixture
async def setup(client, monkeypatch):
    headers = await auth_headers(client, "sales")
    cid = (await client.post("/api/v1/ai/conversations", headers=headers, json={})).json()["id"]

    async def fake_route(context):
        return {
            "skill_id": "lead-intake-validation",
            "skill_revision": "test",
            "skill_key": "K1",
            "object_key": next((o["key"] for o in context["objects"] if o["current"]), None),
            "transition": "continue" if context["task"].get("skill") else "new",
            "reason": "确定性替身",
            "question": None,
        }

    monkeypatch.setattr(assistant_router, "route_message", fake_route)
    return headers, cid


async def send(client, setup, key, text):
    headers, cid = setup
    return await client.post(
        f"/api/v1/ai/conversations/{cid}/messages",
        headers=headers,
        json={"request_key": key, "content": text},
    )


@pytest.mark.parametrize("verified", [True, False])
async def test_appointment_requires_truth_confirmation_then_finishes_validation(
    client, monkeypatch, setup, db_session, verified
):
    name = "预约待核实学校" + uuid.uuid4().hex[:8]
    calls = []

    async def extract(message, known, questions):
        calls.append((known, questions))
        if len(calls) == 1:
            return updates(
                message, name=name, source="channel_partner", contact_name="王校长",
                contact_position="校长", contacted=True, intent="report_meeting",
            )
        return updates(message, info_verified=verified)

    monkeypatch.setattr(lead_intake, "extract_lead", extract)
    first = await send(client, setup, "appointment", "已与王校长通话，对方同意下午正式汇报")
    assert first.status_code == 200, first.text
    task = first.json()["task"]
    assert task["state"] == "awaiting_user", "有预约但未核实信息时，必须继续追问"
    assert len(task["pending_questions"]) == 1
    assert "属实" in task["pending_questions"][0]
    lid = task["lead_id"]
    answer = "上述客户和联系人信息属实" if verified else "核实后客户信息不属实"
    second = await send(client, setup, "confirmation", answer)
    assert second.status_code == 200, second.text
    assert second.json()["task"]["lead_id"] == lid
    assert second.json()["task"]["validation_state"] == ("valid" if verified else "invalid")
    assert calls[1][0]["intent"] == "report_meeting"
    assert calls[1][1] == task["pending_questions"]
    lead = await db_session.get(Lead, lid)
    assert lead.status == ("valid" if verified else "invalid")
    followups = (
        (await db_session.execute(select(FollowUp).where(FollowUp.lead_id == lid))).scalars().all()
    )
    assert len(followups) == 1
    repeat = await send(client, setup, "confirmation", answer)
    assert repeat.json() == second.json()


async def test_missing_source_merges_then_creates_without_phone(
    client, monkeypatch, setup, db_session
):
    name = "合成学校" + uuid.uuid4().hex[:8]
    seen = []

    async def extract(message, known, questions):
        seen.append(known)
        return (
            updates(message, name=name, contact_name="李校长", contact_position="校长")
            if len(seen) == 1
            else updates(message, source="channel_partner")
        )

    monkeypatch.setattr(lead_intake, "extract_lead", extract)
    first = await send(client, setup, "1", name + "李校长")
    assert first.status_code == 200, first.text
    assert first.json()["task"]["state"] == "awaiting_user"
    assert first.json()["business_executed"] is False
    second = await send(client, setup, "2", "渠道商介绍")
    assert second.status_code == 200, second.text
    lid = second.json()["task"]["lead_id"]
    lead = await db_session.get(Lead, lid)
    assert lead.name == name and lead.status == "assigned" and lead.owner_id
    contact = (await db_session.execute(select(Contact).where(Contact.lead_id == lid))).scalar_one()
    assert contact.name == "李校长" and contact.phone == ""
    assert seen[1]["name"] == name
    repeat = await send(client, setup, "2", "渠道商介绍")
    assert repeat.json() == second.json()


async def test_no_reply_pending_then_verified_intent_converts_one_contact_record(
    client,
    monkeypatch,
    setup,
    db_session,
):
    name = "验证学校" + uuid.uuid4().hex[:8]
    i = 0

    async def extract(message, known, questions):
        nonlocal i
        i += 1
        if i == 1:
            return updates(
                message,
                name=name,
                source="channel_partner",
                contact_name="李校长",
                contact_position="校长",
            )
        if i == 2:
            return updates(message, contacted=True)
        if i == 3:
            return updates(message, info_verified=True)
        return updates(message, intent="report_meeting")

    monkeypatch.setattr(lead_intake, "extract_lead", extract)
    created = await send(client, setup, "1", name)
    assert created.status_code == 200, created.text
    lid = created.json()["task"]["lead_id"]
    pending = await send(client, setup, "2", "联系了没回复")
    assert pending.status_code == 200, pending.text
    assert pending.json()["task"]["validation_state"] == "pending"
    await send(client, setup, "3", "信息已经核实正确")
    valid = await send(client, setup, "4", "同意下周听汇报")
    assert valid.status_code == 200, valid.text
    assert valid.json()["task"]["validation_state"] == "valid"
    lead = await db_session.get(Lead, lid)
    assert lead.status == "valid" and lead.current_stage == "s1_valid_opportunity"
    rows = (
        (await db_session.execute(select(FollowUp).where(FollowUp.lead_id == lid))).scalars().all()
    )
    assert len(rows) == 1
    assert "没回复" in rows[0].content and "核实" in rows[0].content
    assert rows[0].is_valid == "valid"


async def test_extract_requires_actual_message_quote(monkeypatch):
    from types import SimpleNamespace

    async def invalid(**kwargs):
        return updates("不存在的引文", name="某学校")

    monkeypatch.setattr(
        lead_intake,
        "skill_catalog",
        lambda: {
            "lead-intake-validation": SimpleNamespace(revision_ref="test"),
        },
    )
    monkeypatch.setattr(lead_intake, "run_structured", invalid)
    with pytest.raises(ValidationError, match="原文依据"):
        await lead_intake.extract_lead("王总介绍", {}, [])


async def test_historical_contact_keeps_its_date_and_separate_record(
    client, monkeypatch, setup, db_session
):
    calls = 0

    async def extract(message, known, questions):
        nonlocal calls
        calls += 1
        if calls == 1:
            return updates(
                message,
                name="历史线索" + uuid.uuid4().hex[:8],
                source="channel_partner",
                contact_name="联系人甲",
                contact_position="主任",
            )
        return {
            **updates(message, contacted=True),
            "mode": "history",
            "new_record": True,
            "occurred_at": "2026-10-13T00:00:00" if calls == 2 else "2026-10-09T00:00:00",
        }

    monkeypatch.setattr(lead_intake, "extract_lead", extract)
    created = await send(client, setup, "create", "新线索")
    lid = created.json()["task"]["lead_id"]
    for key, text in (("today", "10月13日联系未回复"), ("history", "补录10月9日联系未回复")):
        response = await send(client, setup, key, text)
        assert response.status_code == 200, response.text
    rows = (
        (await db_session.execute(select(FollowUp).where(FollowUp.lead_id == lid))).scalars().all()
    )
    assert {r.followed_at.date().isoformat() for r in rows} == {"2026-10-13", "2026-10-09"}
    assert len(rows) == 2


async def test_four_fields_can_be_completed_across_turns_with_source_trace(
    client, monkeypatch, setup, db_session
):
    from app.models.assistant import AssistantConversation, AssistantTurn

    name = "分轮线索" + uuid.uuid4().hex[:8]
    extracts = [
        updates("先录入分轮线索", name=name),
        updates("来源是合作渠道", source="channel_partner"),
        updates("联系人张老师，职位是教务主任", contact_name="张老师", contact_position="教务主任"),
    ]

    async def extract(_message, _known, _questions):
        return extracts.pop(0)

    monkeypatch.setattr(lead_intake, "extract_lead", extract)
    first = await send(client, setup, "split-1", "先录入分轮线索")
    second = await send(client, setup, "split-2", "来源是合作渠道")
    assert first.json()["task"]["task_id"] == second.json()["task"]["task_id"]
    assert first.json()["task"]["state"] == second.json()["task"]["state"] == "awaiting_user"
    assert first.json()["business_executed"] is False
    assert second.json()["business_executed"] is False

    third = await send(client, setup, "split-3", "联系人张老师，职位是教务主任")
    body = third.json()
    assert body["task"]["state"] in {"ready", "awaiting_user"}
    lid = body["task"]["lead_id"]
    lead = await db_session.get(Lead, lid)
    assert lead.name == name and lead.source == "channel_partner"
    assert lead.owner_id is not None
    contact = (
        await db_session.execute(select(Contact).where(Contact.lead_id == lid))
    ).scalar_one()
    assert (contact.name, contact.position, contact.phone) == ("张老师", "教务主任", "")

    conversation = await db_session.get(AssistantConversation, setup[1])
    evidence = conversation.task["evidence"]
    assert {key: item["quote"] for key, item in evidence.items()} == {
        "name": "先录入分轮线索",
        "source": "来源是合作渠道",
        "contact_name": "联系人张老师，职位是教务主任",
        "contact_position": "联系人张老师，职位是教务主任",
    }
    turns = (
        await db_session.execute(
            select(AssistantTurn).where(AssistantTurn.conversation_id == setup[1])
        )
    ).scalars().all()
    by_request = {turn.request_key: turn for turn in turns}
    assert evidence["name"]["turn_id"] == by_request["split-1"].id
    assert evidence["source"]["turn_id"] == by_request["split-2"].id
    assert evidence["contact_name"]["turn_id"] == by_request["split-3"].id


async def test_customer_switch_does_not_carry_four_intake_fields(
    client, monkeypatch, setup, db_session
):
    name_a = "客户甲" + uuid.uuid4().hex[:8]
    name_b = "客户乙" + uuid.uuid4().hex[:8]

    async def fake_route(context):
        content = context["message"]
        switch_to_b = content.startswith("切换")
        has_task = bool(context["task"].get("skill"))
        transition = "new" if switch_to_b or not has_task else "continue"
        return {
            "skill_id": "lead-intake-validation",
            "skill_revision": "test",
            "skill_key": "K1",
            "object_key": None,
            "transition": transition,
            "reason": "隔离测试",
            "question": None,
        }

    answers = {
        "录入客户甲": updates(
            "录入客户甲",
            name=name_a,
            source="company_pool",
            contact_name="甲联系人",
            contact_position="校长",
        ),
        "切换录入客户乙": updates(
            "切换录入客户乙", name=name_b, source="channel_partner"
        ),
        "补客户乙联系人": updates(
            "补客户乙联系人", contact_name="乙联系人", contact_position="主任"
        ),
    }

    async def extract(message, _known, _questions):
        return answers[message]

    monkeypatch.setattr(assistant_router, "route_message", fake_route)
    monkeypatch.setattr(lead_intake, "extract_lead", extract)
    a = await send(client, setup, "switch-a", "录入客户甲")
    b1 = await send(client, setup, "switch-b1", "切换录入客户乙")
    b2 = await send(client, setup, "switch-b2", "补客户乙联系人")
    assert a.json()["task"]["lead_id"] != b2.json()["task"]["lead_id"]
    lead_b = await db_session.get(Lead, b2.json()["task"]["lead_id"])
    assert (lead_b.name, lead_b.source) == (name_b, "channel_partner")
    contact_b = (
        await db_session.execute(select(Contact).where(Contact.lead_id == lead_b.id))
    ).scalar_one()
    assert (contact_b.name, contact_b.position) == ("乙联系人", "主任")
    assert "甲联系人" not in {contact_b.name, contact_b.position}
    assert b1.json()["task"]["facts"].get("name") == name_b


async def test_explicit_refusal_is_invalid_without_inventing_positive_intent(
    client, monkeypatch, setup, db_session
):
    name = "明确拒绝" + uuid.uuid4().hex[:8]
    count = 0

    async def extract(message, _known, _questions):
        nonlocal count
        count += 1
        if count == 1:
            return updates(
                message,
                name=name,
                source="company_pool",
                contact_name="李主任",
                contact_position="主任",
            )
        return updates(message, rejected=True)

    monkeypatch.setattr(lead_intake, "extract_lead", extract)
    created = await send(client, setup, "refusal-1", "登记拒绝测试客户")
    lid = created.json()["task"]["lead_id"]
    refusal = await send(client, setup, "refusal-2", "客户明确说暂不接受任何交流")
    assert refusal.json()["task"]["validation_state"] == "invalid"
    lead = await db_session.get(Lead, lid)
    assert lead.status == "invalid"
    rows = (
        await db_session.execute(select(FollowUp).where(FollowUp.lead_id == lid))
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].is_valid == "invalid"
    assert "同意汇报" not in rows[0].content
    assert "微信" not in rows[0].content


async def test_contacted_or_verified_without_a_qualifying_intent_stays_pending(
    client, monkeypatch, setup, db_session
):
    name = "仅联系不转商机" + uuid.uuid4().hex[:8]
    count = 0

    async def extract(message, _known, _questions):
        nonlocal count
        count += 1
        if count == 1:
            return updates(
                message,
                name=name,
                source="company_pool",
                contact_name="陈老师",
                contact_position="教务主任",
            )
        if count == 2:
            return updates(message, contacted=True, info_verified=True)
        return updates(message, intent="wechat_material")

    monkeypatch.setattr(lead_intake, "extract_lead", extract)
    created = await send(client, setup, "wechat-1", "登记微信验证客户")
    lid = created.json()["task"]["lead_id"]
    contacted = await send(client, setup, "wechat-2", "已经加上微信，还没交流也没看资料")
    assert contacted.json()["task"]["validation_state"] == "pending"
    lead = await db_session.get(Lead, lid)
    assert lead.status == "following" and lead.current_stage is None
    converted = await send(client, setup, "wechat-3", "微信沟通过，也看过资料，愿意继续了解")
    assert converted.json()["task"]["validation_state"] == "valid"
    await db_session.refresh(lead)
    assert lead.current_stage == "s1_valid_opportunity"


async def test_lead_intake_failure_never_returns_success_receipt(
    client, monkeypatch, setup, db_session
):
    from app.core.errors import ConflictError
    from app.services import lead_service

    name = "失败不报成功" + uuid.uuid4().hex[:8]

    async def extract(message, _known, _questions):
        return updates(
            message,
            name=name,
            source="company_pool",
            contact_name="王老师",
            contact_position="校长",
        )

    original_create = lead_service.create_lead

    async def fail_create(*_args, **_kwargs):
        raise ConflictError("隔离测试模拟写入冲突")

    monkeypatch.setattr(lead_intake, "extract_lead", extract)
    monkeypatch.setattr(lead_service, "create_lead", fail_create)
    response = await send(client, setup, "fail-create", "录入一次待失败线索")
    assert response.status_code != 200
    assert response.json().get("business_executed") is not True
    assert response.json().get("overall_status") != "succeeded"
    leads = (
        await db_session.execute(select(Lead).where(Lead.name == name))
    ).scalars().all()
    assert leads == []
    monkeypatch.setattr(lead_service, "create_lead", original_create)
    retry = await send(client, setup, "fail-create", "录入一次待失败线索")
    assert retry.status_code == 200, retry.text
    retry_lid = retry.json()["task"]["lead_id"]
    repeats = (
        await db_session.execute(select(Lead).where(Lead.name == name))
    ).scalars().all()
    assert [lead.id for lead in repeats] == [retry_lid]
    assert (await send(client, setup, "fail-create", "录入一次待失败线索")).json() == retry.json()


async def test_seller_cannot_create_conversation_for_another_sellers_lead(
    client, monkeypatch, setup, db_session
):
    from app.core.security import hash_password
    from app.models.user import User

    name = "权限隔离" + uuid.uuid4().hex[:8]

    async def extract(message, _known, _questions):
        return updates(
            message,
            name=name,
            source="company_pool",
            contact_name="周老师",
            contact_position="校长",
        )

    monkeypatch.setattr(lead_intake, "extract_lead", extract)
    created = await send(client, setup, "acl-create", "登记本人线索")
    lid = created.json()["task"]["lead_id"]
    other = User(
        username="order009010_other_seller",
        name="隔离销售",
        password_hash=hash_password("123456"),
        role="market_staff",
        data_scope="self",
        department="测试部",
    )
    db_session.add(other)
    await db_session.commit()
    other_headers = await auth_headers(client, other.username)
    denied = await client.post(
        "/api/v1/ai/conversations", headers=other_headers, json={"lead_id": lid}
    )
    assert denied.status_code == 403
    assert "权限" in denied.text or "本人" in denied.text


def test_full_skill_receipt_cannot_pass_as_empty_extraction():
    from pydantic import ValidationError as SchemaError

    with pytest.raises(SchemaError):
        lead_intake.LeadIntakeOutput.model_validate({"status": "completed"})


def test_lead_intent_output_is_limited_to_confirmed_enum_values():
    from pydantic import ValidationError as SchemaError

    assert lead_intake.LeadIntentFact.model_validate(
        {"key": "intent", "value": "unknown", "quote": "仅加上微信"}
    ).value == "unknown"
    with pytest.raises(SchemaError):
        lead_intake.LeadIntentFact.model_validate(
            {
                "key": "intent",
                "value": "unknown：仅加上微信，尚未交流",
                "quote": "仅加上微信",
            }
        )
