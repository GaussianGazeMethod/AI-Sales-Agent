"""助手项目档案、行动及报告读取接口。"""

from datetime import datetime
from io import BytesIO
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import require_lead_view, require_lead_write
from app.core.errors import NotFoundError, PermissionDeniedError, ValidationError
from app.database import get_db
from app.models.activity import Reminder
from app.models.ai import AiDiagnosis
from app.models.assistant import AssistantConversation
from app.models.dossier import AssistantAction, ProjectFactEvent
from app.models.user import User
from app.services import dossier_service, lead_service

router = APIRouter(prefix="/ai/assistant", tags=["AI 助手工作区"])


@router.get("/conversations")
async def conversations(
    user: User = Depends(require_lead_write), db: AsyncSession = Depends(get_db, scope="function")
):
    rows = (
        (
            await db.execute(
                select(AssistantConversation)
                .where(AssistantConversation.owner_id == user.id)
                .order_by(AssistantConversation.updated_at.desc())
                .limit(30)
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "id": r.id,
            "lead_id": r.lead_id,
            "updated_at": r.updated_at.isoformat(),
            "skill": (r.task or {}).get("skill_id"),
        }
        for r in rows
    ]


@router.get("/projects/{lead_id}/dossier")
async def dossier(
    lead_id: str,
    details: bool = False,
    user: User = Depends(require_lead_view),
    db: AsyncSession = Depends(get_db, scope="function"),
):
    context = await dossier_service.snapshot(db, user, lead_id)
    stored = await dossier_service.get(db, lead_id)
    result = {
        "lead_id": lead_id,
        "customer": context["customer"],
        "project": context["project"],
        "stage": context["stage"],
        "revision": context["revision"],
        "facts": context["facts"],
        "record": context["record"],
        "evidence": stored.evidence if stored else {},
        "actions": context["actions"],
        "stage_history": context["stage_history"],
    }
    if details:
        events = (
            (
                await db.execute(
                    select(ProjectFactEvent)
                    .where(ProjectFactEvent.lead_id == lead_id)
                    .order_by(
                        ProjectFactEvent.occurred_at.desc(), ProjectFactEvent.created_at.desc()
                    )
                    .limit(100)
                )
            )
            .scalars()
            .all()
        )
        result["history"] = [
            {
                "id": e.id,
                "occurred_at": e.occurred_at.isoformat(),
                "recorded_at": e.created_at.isoformat(),
                "content": e.content,
                "changes": e.changes,
                "record": e.record,
            }
            for e in events
        ]
        result["followups"] = [e for e in context["evidence"] if e.get("followup_id")]
    return result


@router.get("/actions")
async def actions(
    lead_id: str | None = None,
    user: User = Depends(require_lead_view), db: AsyncSession = Depends(get_db, scope="function")
):
    if lead_id:
        lead = await lead_service.get_lead(db, lead_id)
        await lead_service.assert_can_view_lead(db, user, lead)
    condition = (
        AssistantAction.lead_id == lead_id
        if lead_id
        else AssistantAction.owner_id == user.id
    )
    rows = (
        (
            await db.execute(
                select(AssistantAction)
                .where(condition)
                .order_by(AssistantAction.created_at.desc())
                .limit(200)
            )
        )
        .scalars()
        .all()
    )
    result = []
    for a in rows:
        lead = await lead_service.get_lead(db, a.lead_id)
        await lead_service.assert_can_view_lead(db, user, lead)
        result.append(
            {
                **dossier_service.action_dict(a),
                "customer": lead.name,
                "is_opportunity": bool(lead.current_stage),
            }
        )
    return result


class ActionFeedback(BaseModel):
    effect: str | None = Field(default=None, max_length=1000)


@router.post("/actions/{action_id}/complete")
async def complete(
    action_id: str,
    payload: ActionFeedback,
    user: User = Depends(require_lead_write),
    db: AsyncSession = Depends(get_db, scope="function"),
):
    a = await db.get(AssistantAction, action_id)
    if not a:
        raise NotFoundError("行动不存在")
    if a.owner_id != user.id:
        raise PermissionDeniedError()
    lead = await lead_service.get_lead(db, a.lead_id)
    lead_service.assert_can_edit(user, lead)
    a.status, a.completed_at = "done", a.completed_at or datetime.now()
    if payload.effect is not None:
        a.effect = payload.effect
    a.history = [
        *(a.history or []),
        {
            "operation": "complete",
            "by": user.id,
            "effect": payload.effect,
            "at": datetime.now().isoformat(),
        },
    ]
    reminders = (
        (
            await db.execute(
                select(Reminder).where(
                    Reminder.dedup_key.like(f"assistant_action:{a.id}:%"),
                    Reminder.status == "pending",
                )
            )
        )
        .scalars()
        .all()
    )
    for reminder in reminders:
        reminder.status, reminder.done_at, reminder.done_by = "done", datetime.now(), user.id
    return dossier_service.action_dict(a)


async def _report(db, user, report_id):
    report = await db.get(AiDiagnosis, report_id)
    if not report:
        raise NotFoundError("报告不存在")
    lead = await lead_service.get_lead(db, report.lead_id)
    await lead_service.assert_can_view_lead(db, user, lead)
    return report, lead


class Adopt(BaseModel):
    action_index: int = Field(ge=0)


@router.post("/reports/{report_id}/adopt")
async def adopt(
    report_id: str,
    payload: Adopt,
    user: User = Depends(require_lead_write),
    db: AsyncSession = Depends(get_db, scope="function"),
):
    report, lead = await _report(db, user, report_id)
    lead_service.assert_can_edit(user, lead)
    suggestions = (report.structured or {}).get("suggested_actions", [])
    if payload.action_index >= len(suggestions):
        raise ValidationError("建议不存在")
    origin = f"report:{report.id}:{payload.action_index}:{user.id}"
    action = (
        await db.execute(select(AssistantAction).where(AssistantAction.origin == origin))
    ).scalar_one_or_none()
    if not action:
        from app.ai.assistant_facts import parse_time

        suggestion = suggestions[payload.action_index]
        action = AssistantAction(
            lead_id=lead.id,
            owner_id=user.id,
            origin=origin,
            content=suggestion["content"],
            due_at=parse_time(suggestion.get("due_at")),
            customer_confirmed=False,
        )
        db.add(action)
        await db.flush()
    report.adopted = True
    return {
        **dossier_service.action_dict(action),
        "source_report_id": report.id,
        "source_report_version": (report.structured or {}).get("report_version")
        or (report.structured or {}).get("analysis_version"),
        "source_suggestion_index": payload.action_index,
    }


def word_bytes(title, sections):
    """A4常规页边距，正文11磅；标准OOXML可由Word/WPS读取。"""
    ns = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    ET.register_namespace("w", ns)

    def node(parent, tag, **attrs):
        return ET.SubElement(
            parent, "{" + ns + "}" + tag, {"{" + ns + "}" + k: str(v) for k, v in attrs.items()}
        )

    document = ET.Element("{" + ns + "}document")
    body = node(document, "body")

    def paragraph(text, title=False, heading=False):
        p = node(body, "p")
        props = node(p, "pPr")
        node(props, "spacing", after=100)
        if title:
            node(props, "pStyle", val="Title")
        run = node(p, "r")
        props = node(run, "rPr")
        node(props, "rFonts", ascii="Arial", eastAsia="Heiti SC")
        node(props, "color", val="000000")
        node(props, "sz", val=32 if title else 22)
        if heading or title:
            node(props, "b")
        node(run, "t").text = text

    paragraph(title, title=True)
    for section in sections:
        paragraph(section["title"], heading=True)
        paragraph(section["content"])
    sect = node(body, "sectPr")
    node(sect, "pgSz", w=11906, h=16838)
    node(sect, "pgMar", top=1440, right=1440, bottom=1440, left=1440)
    styles = ET.Element("{" + ns + "}styles")
    defaults = node(node(node(styles, "docDefaults"), "rPrDefault"), "rPr")
    node(defaults, "rFonts", ascii="Arial", eastAsia="Heiti SC")
    node(defaults, "sz", val=22)
    style = node(styles, "style", type="paragraph", styleId="Title")
    node(style, "name", val="Title")
    title_props = node(style, "rPr")
    node(title_props, "b")
    node(title_props, "color", val="000000")
    node(title_props, "sz", val=32)
    types_ns = "http://schemas.openxmlformats.org/package/2006/content-types"
    types = ET.Element("Types", xmlns=types_ns)
    ET.SubElement(
        types,
        "Default",
        Extension="rels",
        ContentType="application/vnd.openxmlformats-package.relationships+xml",
    )
    ET.SubElement(types, "Default", Extension="xml", ContentType="application/xml")
    mime_prefix = "application/vnd.openxmlformats-officedocument.wordprocessingml."
    for path, mime in [("document.xml", "document.main+xml"), ("styles.xml", "styles+xml")]:
        ET.SubElement(types, "Override", PartName="/word/" + path, ContentType=mime_prefix + mime)
    rel_ns = "http://schemas.openxmlformats.org/package/2006/relationships"
    rel_prefix = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
    rels = ET.Element("Relationships", xmlns=rel_ns)
    ET.SubElement(
        rels,
        "Relationship",
        Id="rId1",
        Type=rel_prefix + "officeDocument",
        Target="word/document.xml",
    )
    doc_rels = ET.Element("Relationships", xmlns=rel_ns)
    ET.SubElement(
        doc_rels, "Relationship", Id="rId1", Type=rel_prefix + "styles", Target="styles.xml"
    )
    buffer = BytesIO()
    with ZipFile(buffer, "w") as z:
        for path, root in [
            ("[Content_Types].xml", types),
            ("_rels/.rels", rels),
            ("word/_rels/document.xml.rels", doc_rels),
            ("word/document.xml", document),
            ("word/styles.xml", styles),
        ]:
            z.writestr(path, ET.tostring(root, encoding="utf-8", xml_declaration=True))
    return buffer.getvalue()


@router.get("/reports/{report_id}/word")
async def word(
    report_id: str,
    user: User = Depends(require_lead_write),
    db: AsyncSession = Depends(get_db, scope="function"),
):
    report, lead = await _report(db, user, report_id)
    sections = (report.structured or {}).get("sections")
    if not sections:
        raise ValidationError("此历史报告尚无结构化正文")
    return Response(
        word_bytes(
            lead.name + ("需求报告" if report.trigger == "requirements" else "项目诊断"), sections
        ),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": 'attachment; filename="project-report.docx"'},
    )
