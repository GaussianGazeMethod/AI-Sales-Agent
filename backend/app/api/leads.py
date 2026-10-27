"""闭环一接口：线索 CRUD、查重撞单、分配、跟进、公海、导入、看板。"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Query, Request, Response, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import (
    get_current_user,
    require_admin,
    require_lead_assign,
    require_lead_view,
    require_lead_write,
)
from app.core.errors import BizError
from app.database import get_db
from app.models.activity import Reminder
from app.models.lead import Lead
from app.models.user import User
from app.schemas import (
    AssignRequest,
    CheckDuplicateRequest,
    ContactCreate,
    ContactUpdate,
    DedupResolveRequest,
    FollowUpCreate,
    LeadCreate,
    LeadUpdate,
    RecycleRequest,
)
from app.serializers import (
    assignment_out,
    contact_out,
    followup_out,
    import_batch_out,
    lead_out,
    leads_with_metadata,
    log_out,
    reminder_out,
    stage_completion_out,
    stage_standard_out,
)
from app.services import (
    assign_service,
    audit_service,
    dashboard_service,
    followup_service,
    import_service,
    lead_service,
    sla_service,
    stage_service,
)

router = APIRouter(tags=["闭环一 · 线索"])


# ══════════════ 列表 / 详情 ══════════════


@router.get("/leads", summary="线索列表（多子视图 + 多维筛选）")
async def list_leads(
    view: str = Query("all", description="all/unassigned/mine/pool/duplicate/invalid"),
    lifecycle: str | None = Query(None, pattern="^(lead|opportunity)$"),
    active_only: bool = False,
    keyword: str | None = None,
    status: str | None = None,
    source: str | None = None,
    org_type: str | None = None,
    province: str | None = None,
    city: str | None = None,
    owner_id: str | None = None,
    include_deleted: bool = False,
    sort: str = "recent",
    page: int = 1,
    page_size: int = 20,
    user: User = Depends(require_lead_view),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    rows, total = await lead_service.list_leads(
        db,
        user=user,
        view=view,
        lifecycle=lifecycle,
        active_only=active_only,
        keyword=keyword,
        status=status,
        source=source,
        org_type=org_type,
        province=province,
        city=city,
        owner_id=owner_id,
        include_deleted=include_deleted and user.role == "admin",
        sort=sort,
        offset=(page - 1) * page_size,
        limit=page_size,
    )
    from app.services import dossier_service

    metadata = await dossier_service.list_metadata(db, rows)
    return {
        "items": [{**lead_out(r), **metadata.get(r.id, {})} for r in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/leads/load-view", summary="负载视图（各人手上条数）")
async def load_view(
    user: User = Depends(require_lead_assign), db: AsyncSession = Depends(get_db, scope="function")
) -> dict:
    items = await assign_service.load_view(db)
    return {"items": items, "total": len(items)}


@router.get("/leads/duplicates", summary="疑似重复裁决台列表")
async def list_duplicates(
    page: int = 1,
    page_size: int = 20,
    user: User = Depends(require_lead_assign),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    rows, total = await lead_service.list_duplicates(
        db, user=user, offset=(page - 1) * page_size, limit=page_size
    )
    return {
        "items": await leads_with_metadata(db, rows),
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.post("/leads/check-duplicate", summary="实时查重（录入时防抖调用）")
async def check_duplicate(
    payload: CheckDuplicateRequest,
    user: User = Depends(require_lead_write),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    hits = await lead_service.check_duplicate(
        db,
        name=payload.name,
        province=payload.province,
        city=payload.city,
        district=payload.district,
    )
    return lead_service.duplicate_warning(hits)


@router.get("/leads/export", summary="导出线索（限权 + 留审计）")
async def export_leads(
    request: Request,
    view: str = "all",
    user: User = Depends(require_lead_assign),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> Response:
    import io

    from openpyxl import Workbook

    rows, _ = await lead_service.list_leads(db, user=user, view=view, offset=0, limit=1000)
    wb = Workbook()
    ws = wb.active
    ws.title = "线索"
    headers = [
        "客户名称",
        "来源",
        "省份",
        "城市",
        "区县",
        "类型",
        "状态",
        "归属人",
        "预算金额(元)",
        "创建时间",
    ]
    ws.append(headers)
    for lead in rows:
        ws.append(
            [
                lead.name,
                lead.source,
                lead.province,
                lead.city,
                lead.district,
                lead.org_type,
                lead.status,
                lead.owner_name or "未指派",
                round((lead.budget_amount_fen or 0) / 100, 2),
                lead.created_at.strftime("%Y-%m-%d %H:%M") if lead.created_at else "",
            ]
        )
    await audit_service.write_log(
        db,
        operator=user,
        action="export",
        target_type="lead",
        detail=f"导出线索 {len(rows)} 条（视图 {view}）",
        ip=request.client.host if request.client else None,
    )
    stream = io.BytesIO()
    wb.save(stream)
    return Response(
        content=stream.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="leads.xlsx"'},
    )


@router.get("/leads/{lead_id}", summary="线索详情（含联系人/跟进/分配/待办/日志/环节进度）")
async def get_lead_detail(
    lead_id: str,
    user: User = Depends(require_lead_view),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    from app.models.activity import Assignment
    from app.models.system import OperationLog
    from app.serializers import amount_change_out, risk_out, stage_transition_out

    lead = await lead_service.get_lead(db, lead_id)
    await lead_service.assert_can_view_lead(db, user, lead)
    contacts = await lead_service.list_contacts(db, lead_id)
    followups = await followup_service.list_followups(db, lead_id)
    assignments = (
        (
            await db.execute(
                select(Assignment)
                .where(Assignment.lead_id == lead_id)
                .order_by(Assignment.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    reminders = (
        (
            await db.execute(
                select(Reminder)
                .where(Reminder.lead_id == lead_id)
                .order_by(Reminder.remind_at.desc())
                .limit(50)
            )
        )
        .scalars()
        .all()
    )
    logs = (
        (
            await db.execute(
                select(OperationLog)
                .where(OperationLog.target_id == lead_id)
                .order_by(OperationLog.created_at.desc())
                .limit(50)
            )
        )
        .scalars()
        .all()
    )
    progress = await stage_service.stage_progress(db, lead) if lead.current_stage else None
    from app.services import dossier_service
    metadata = (await dossier_service.list_metadata(db, [lead])).get(lead.id, {})

    return {
        "lead": {**lead_out(lead), **metadata},
        "contacts": [
            contact_out(c, reveal_phone=_can_reveal_contact(user, lead)) for c in contacts
        ],
        "followups": [followup_out(f) for f in followups],
        "assignments": [assignment_out(a) for a in assignments],
        "reminders": [reminder_out(r) for r in reminders],
        "logs": [log_out(x) for x in logs],
        "sla": await sla_service.lead_sla_snapshot(db, lead),
        "stage_progress": _progress_out(progress) if progress else None,
        "amount_changes": [
            amount_change_out(a) for a in await stage_service.list_amount_changes(db, lead_id)
        ],
        "risks": [risk_out(r) for r in await stage_service.list_risks(db, lead_id)],
        "transitions": [
            stage_transition_out(t) for t in await stage_service.list_transitions(db, lead_id)
        ],
    }


def _progress_out(progress: dict | None) -> dict | None:
    if not progress:
        return None
    return {
        "stage": progress["stage"],
        "stage_label": progress["stage_label"],
        "standards": [
            {
                "standard": stage_standard_out(item["standard"]),
                "completion": stage_completion_out(item["completion"]),
            }
            for item in progress["standards"]
        ],
        "hard_gate_passed": progress["hard_gate_passed"],
        "missing_hard_gates": progress["missing_hard_gates"],
        "stay_days": progress["stay_days"],
        "base_days": progress["base_days"],
        "overdue": progress["overdue"],
        "done_count": progress["done_count"],
        "total_count": progress["total_count"],
    }


# ══════════════ 录入 / 编辑 / 删除 / 复活 / 领取 ══════════════


@router.post("/leads", summary="录入线索（含撞单提示）")
async def create_lead(
    payload: LeadCreate,
    user: User = Depends(require_lead_write),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    lead, warning = await lead_service.create_lead(db, user=user, payload=payload)
    return {"lead": lead_out(lead), "duplicate_warning": warning}


@router.patch("/leads/{lead_id}", summary="编辑线索")
async def update_lead(
    lead_id: str,
    payload: LeadUpdate,
    user: User = Depends(require_lead_write),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    lead = await lead_service.get_lead(db, lead_id)
    await lead_service.update_lead(db, user=user, lead=lead, payload=payload)
    return lead_out(lead)


@router.delete("/leads/{lead_id}", summary="软删除线索（仅管理员）")
async def delete_lead(
    lead_id: str,
    request: Request,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    lead = await lead_service.get_lead(db, lead_id)
    await lead_service.soft_delete(db, user=user, lead=lead)
    return {"ok": True}


@router.post("/leads/{lead_id}/revive", summary="无效商机复活")
async def revive_lead(
    lead_id: str,
    payload: dict | None = None,
    user: User = Depends(require_lead_write),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    lead = await lead_service.get_lead(db, lead_id)
    await lead_service.revive_lead(db, user=user, lead=lead, remark=(payload or {}).get("remark"))
    return {"lead": lead_out(lead)}


@router.post("/leads/{lead_id}/claim", summary="公海重新领取")
async def claim_lead(
    lead_id: str,
    user: User = Depends(require_lead_write),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    lead = await lead_service.get_lead(db, lead_id)
    await assign_service.claim(db, user=user, lead=lead)
    return {"lead": lead_out(lead)}


# ══════════════ 分配 / 回收 / 裁决 ══════════════


@router.post("/leads/assign", summary="批量指派 / 改派")
async def assign_leads(
    payload: AssignRequest,
    user: User = Depends(require_lead_assign),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    affected = await assign_service.assign_many(db, user=user, payload=payload)
    return {"ok": True, "affected": affected}


@router.post("/leads/recycle", summary="批量回收至公海")
async def recycle_leads(
    payload: RecycleRequest,
    user: User = Depends(require_lead_assign),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    affected = await assign_service.recycle_many(db, user=user, payload=payload)
    return {"ok": True, "affected": affected}


@router.post("/leads/duplicates/resolve", summary="疑似重复裁决（保留 / 合并）")
async def resolve_duplicate(
    payload: DedupResolveRequest,
    user: User = Depends(require_lead_assign),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    await lead_service.resolve_duplicate(
        db,
        user=user,
        keep_id=payload.keep_id,
        merge_id=payload.merge_id,
        action=payload.action,
        remark=payload.remark,
    )
    return {"ok": True}


# ══════════════ 联系人 ══════════════


@router.get("/leads/{lead_id}/assignments", summary="分配记录")
async def list_assignments(
    lead_id: str,
    user: User = Depends(require_lead_view),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    lead = await lead_service.get_lead(db, lead_id)
    await lead_service.assert_can_view_lead(db, user, lead)
    from app.models.activity import Assignment

    rows = (
        (
            await db.execute(
                select(Assignment)
                .where(Assignment.lead_id == lead_id)
                .order_by(Assignment.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return {"items": [assignment_out(a) for a in rows], "total": len(rows)}


@router.get("/leads/{lead_id}/logs", summary="该线索的操作日志")
async def list_lead_logs(
    lead_id: str,
    user: User = Depends(require_lead_view),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    lead = await lead_service.get_lead(db, lead_id)
    await lead_service.assert_can_view_lead(db, user, lead)
    from app.models.system import OperationLog

    rows = (
        (
            await db.execute(
                select(OperationLog)
                .where(OperationLog.target_id == lead_id)
                .order_by(OperationLog.created_at.desc())
                .limit(100)
            )
        )
        .scalars()
        .all()
    )
    return {"items": [log_out(x) for x in rows], "total": len(rows)}


@router.get("/leads/{lead_id}/contacts", summary="联系人列表")
async def list_contacts(
    lead_id: str,
    user: User = Depends(require_lead_view),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    lead = await lead_service.get_lead(db, lead_id)
    await lead_service.assert_can_view_lead(db, user, lead)
    reveal = _can_reveal_contact(user, lead)
    rows = await lead_service.list_contacts(db, lead_id)
    return {"items": [contact_out(c, reveal_phone=reveal) for c in rows], "total": len(rows)}


def _can_reveal_contact(user: User, lead: Lead) -> bool:
    """手机号明文查看权限：管理人员/管理员，或该线索的归属人与创建人。"""
    from app.core.deps import is_manager

    if is_manager(user):
        return True
    return lead.owner_id == user.id or lead.creator_id == user.id


@router.post("/leads/{lead_id}/contacts", summary="新增联系人")
async def add_contact(
    lead_id: str,
    payload: ContactCreate,
    user: User = Depends(require_lead_write),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    lead = await lead_service.get_lead(db, lead_id)
    contact = await lead_service.add_contact(db, user=user, lead=lead, payload=payload)
    return contact_out(contact)


@router.patch("/contacts/{contact_id}", summary="编辑联系人")
async def update_contact(
    contact_id: str,
    payload: ContactUpdate,
    user: User = Depends(require_lead_write),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    contact = await lead_service.get_contact(db, contact_id)
    await lead_service.update_contact(db, user=user, contact=contact, payload=payload)
    return contact_out(contact)


@router.delete("/contacts/{contact_id}", summary="删除联系人")
async def delete_contact(
    contact_id: str,
    user: User = Depends(require_lead_write),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    contact = await lead_service.get_contact(db, contact_id)
    await lead_service.delete_contact(db, user=user, contact=contact)
    return {"ok": True}


# ══════════════ 跟进 ══════════════


@router.get("/leads/{lead_id}/followups", summary="跟进记录列表")
async def list_followups(
    lead_id: str,
    user: User = Depends(require_lead_view),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    lead = await lead_service.get_lead(db, lead_id)
    await lead_service.assert_can_view_lead(db, user, lead)
    rows = await followup_service.list_followups(db, lead_id)
    return {"items": [followup_out(f) for f in rows], "total": len(rows)}


@router.post("/leads/{lead_id}/followups", summary="新增跟进（自动判定有效/无效）")
async def create_followup(
    lead_id: str,
    payload: FollowUpCreate,
    user: User = Depends(require_lead_write),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    lead = await lead_service.get_lead(db, lead_id)
    lead_service.assert_can_edit(user, lead)
    follow, validity = await followup_service.create_followup(
        db, user=user, lead=lead, payload=payload
    )
    return {"followup": followup_out(follow), "lead": lead_out(lead), "validity": validity}


# ══════════════ Excel 导入 ══════════════


@router.get("/imports/template", summary="下载导入模板")
async def download_template(user: User = Depends(get_current_user)) -> Response:
    content = import_service.build_template()
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="lead_import_template.xlsx"'},
    )


@router.post("/imports/leads", summary="Excel 批量导入线索")
async def import_leads(
    file: UploadFile,
    user: User = Depends(require_lead_assign),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    content = await file.read()
    try:
        result = await import_service.import_leads(
            db, user=user, filename=file.filename or "upload.xlsx", content=content
        )
    except BizError:
        raise
    return {
        "batch": import_batch_out(result["batch"]),
        "total": result["total"],
        "success": result["success"],
        "failed": result["failed"],
        "errors": result["errors"],
    }


@router.get("/imports/batches", summary="导入批次历史")
async def list_batches(
    page: int = 1,
    page_size: int = 20,
    user: User = Depends(require_lead_assign),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    rows, total = await import_service.list_batches(
        db, offset=(page - 1) * page_size, limit=page_size
    )
    return {
        "items": [import_batch_out(b) for b in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


# ══════════════ 看板 ══════════════


@router.get("/dashboard/workbench", summary="首页工作台（按角色动态）")
async def workbench(
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db, scope="function")
) -> dict:
    from app.serializers import risk_out as _risk_out

    data = await dashboard_service.workbench(db, user=user)
    data["todo_items"] = [reminder_out(r) for r in data["todo_items"]]
    data["stuck_items"] = await leads_with_metadata(db, data["stuck_items"])
    data["risk_items"] = [_risk_out(x) for x in data["risk_items"]]
    if data.get("team_overview"):
        data["team_overview"]["stuck_items"] = await leads_with_metadata(
            db, data["team_overview"]["stuck_items"]
        )
        data["team_overview"]["overdue_first_follow"] = [
            lead_out(x) for x in data["team_overview"]["overdue_first_follow"]
        ]
    return data


@router.get("/dashboard/lead-board", summary="线索看板（来源/漏斗/人均产出/时效/趋势）")
async def lead_board(
    start: date | None = None,
    end: date | None = None,
    user: User = Depends(require_lead_view),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    today = date.today()
    end = end or today
    start = start or date(today.year, today.month, 1)
    if start > end:
        start, end = end, start
    return await dashboard_service.lead_board(db, user=user, start=start, end=end)


@router.get("/opportunities", summary="商机列表（闭环二）")
async def list_opportunities(
    keyword: str | None = None,
    page: int = 1,
    page_size: int = 20,
    user: User = Depends(require_lead_view),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    rows, total = await dashboard_service.opportunity_list(
        db, user=user, keyword=keyword, offset=(page - 1) * page_size, limit=page_size
    )
    return {
        "items": await leads_with_metadata(db, rows),
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/dashboard/funnel", summary="业绩漏斗与加权预测")
async def funnel(
    owner_id: str | None = None,
    user: User = Depends(require_lead_view),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    return await dashboard_service.funnel_and_forecast(db, user=user, owner_id=owner_id)


@router.get("/dashboard/risk-center", summary="风险与卡单中心")
async def risk_center(
    user: User = Depends(require_lead_view), db: AsyncSession = Depends(get_db, scope="function")
) -> dict:
    data = await dashboard_service.risk_center(db, user=user)
    data["stuck"] = await leads_with_metadata(db, data["stuck"])
    data["long_running"] = await leads_with_metadata(db, data["long_running"])
    if data.get("alerts"):
        names = await dashboard_service.alert_lead_names(db, data["alerts"])
        for item in data["alerts"]:
            item["lead_name"] = names.get(item.get("lead_id"))
    return data


@router.get("/dashboard/staff-options", summary="市场人员下拉")
async def staff_options(
    user: User = Depends(require_lead_view), db: AsyncSession = Depends(get_db, scope="function")
) -> dict:
    items = await dashboard_service.market_staff_options(db)
    return {"items": items, "total": len(items)}
