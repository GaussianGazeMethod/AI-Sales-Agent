"""P1-c 接口：预警中心、预警规则配置（6.7）、转化率分析、丢单分析。

页面归属：2.4 风险与卡单中心 ｜ 6.7 预警规则配置 ｜ 2.5 丢单登记与分析。
"""

from __future__ import annotations

from datetime import date, datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, require_lead_view
from app.core.errors import PermissionDeniedError
from app.database import get_db
from app.models.enums import LeadStatus, RiskStatus, UserRole
from app.models.lead import Lead
from app.models.user import User
from app.schemas import RiskHandleRequest, RuleUpdateRequest
from app.serializers import risk_out
from app.services import loss_service, risk_service

router = APIRouter(tags=["P1-c · 预警"])


def _can_manage(user: User) -> bool:
    return user.role in {UserRole.ADMIN, UserRole.MARKET_MANAGER}


# ══════════════ 预警清单 ══════════════


@router.get("/risks", summary="预警清单（四类系统预警 + R1–R6 业务风险）")
async def list_risks(
    status: str | None = Query(
        RiskStatus.PENDING, description="pending/processing/resolved；传 all 表示不限状态"
    ),
    kind: str | None = None,
    lead_id: str | None = None,
    mine: bool = False,
    page: int = 1,
    page_size: int = 20,
    user: User = Depends(require_lead_view),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    owner_id = user.id if (mine or user.role == UserRole.MARKET_STAFF) else None
    rows, total = await risk_service.list_alerts(
        db,
        status=None if status in (None, "", "all") else status,
        kind=kind,
        lead_id=lead_id,
        owner_id=owner_id,
        offset=(page - 1) * page_size,
        limit=page_size,
    )
    names = await risk_service.lead_names_for(db, [r.lead_id for r in rows])
    items = []
    for row in rows:
        item = risk_out(row)
        item["lead_name"] = names.get(row.lead_id)
        items.append(item)
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.post("/risks/scan", summary="手工触发预警扫描（卡单/总时长/节奏窗口 + 转化率）")
async def scan_risks(
    user: User = Depends(require_lead_view), db: AsyncSession = Depends(get_db, scope="function")
) -> dict:
    result = await risk_service.scan(db)
    analysis = await risk_service.conversion_analysis(db)
    notified = await risk_service.notify_conversion_alerts(db, analysis)
    return {"ok": True, **result, "conversion_notified": notified, "breached": analysis["breached"]}


@router.patch("/risks/{risk_id}", summary="处理预警（待处理/处理中/已解除）")
async def handle_risk(
    risk_id: str,
    payload: RiskHandleRequest,
    user: User = Depends(require_lead_view),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    alert = await risk_service.get_alert(db, risk_id)
    alert.status = payload.status
    alert.handle_note = payload.handle_note
    alert.handler_id = user.id
    alert.handler_name = user.name
    alert.handled_at = datetime.now()
    return risk_out(alert)


@router.get("/risks/counts", summary="预警状态计数（工作台/风险中心徽标）")
async def risk_counts(
    user: User = Depends(require_lead_view), db: AsyncSession = Depends(get_db, scope="function")
) -> dict:
    return await risk_service.count_by_status(db)


# ══════════════ 预警规则配置（6.7）══════════════


@router.get("/risk-rules", summary="预警规则与阈值列表")
async def list_rules(
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db, scope="function")
) -> dict:
    rows = await risk_service.list_rules(db)
    return {"items": [risk_service.rule_out(r) for r in rows], "total": len(rows)}


@router.patch("/risk-rules", summary="修改预警规则阈值（管理员 / 市场部管理）")
async def update_rules(
    payload: RuleUpdateRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    if not _can_manage(user):
        raise PermissionDeniedError("只有管理员或市场部管理人员可以修改预警规则")
    rows = await risk_service.update_rules(
        db, operator=user, items=[item.model_dump() for item in payload.items]
    )
    return {"ok": True, "items": [risk_service.rule_out(r) for r in rows]}


# ══════════════ 转化率分析 ══════════════


@router.get("/dashboard/conversion", summary="阶段转化率 vs 基准（个人/团队）")
async def conversion(
    start: date | None = None,
    end: date | None = None,
    owner_id: str | None = None,
    user: User = Depends(require_lead_view),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    # 专员只能看自己的
    scope = owner_id
    if not _can_manage(user):
        scope = user.id
    return await risk_service.conversion_analysis(db, start=start, end=end, owner_id=scope)


# ══════════════ 丢单分析（2.5）══════════════


@router.get("/dashboard/lost-analysis", summary="丢单原因 / 环节 / 人员分布")
async def lost_analysis(
    start: date | None = None,
    end: date | None = None,
    owner_id: str | None = None,
    include_leads: bool = True,
    user: User = Depends(require_lead_view),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    scope = owner_id
    if not _can_manage(user):
        scope = user.id
    data = await loss_service.loss_analysis(db, start=start, end=end, owner_id=scope)

    if include_leads:
        stmt = select(Lead).where(Lead.is_deleted.is_(False), Lead.status == LeadStatus.LOST)
        if scope:
            stmt = stmt.where(Lead.owner_id == scope)
        rows = (await db.execute(stmt.order_by(Lead.lost_at.desc()).limit(100))).scalars().all()
        data["items"] = [loss_service.loss_lead_out(x) for x in rows]
    return data


# ══════════════ 风险与卡单中心（2.4，含持久化预警）══════════════
