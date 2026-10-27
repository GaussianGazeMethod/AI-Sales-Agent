"""P1-d 丢单登记与分析（页面 2.5 / 场景 S2-6）。

规则（已确认）：判丢单时**必须从 R1–R6 多选原因**，这是"丢单原因分布"的唯一数据来源。
丢单后状态置为 `lost`（与闭环一的"无效商机"区分开），并从漏斗中剔除。
"""

from __future__ import annotations

from datetime import date, datetime, time

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import dicts
from app.core.errors import ConflictError, ValidationError
from app.models.enums import LeadStatus, RiskType
from app.models.lead import Lead
from app.models.user import User
from app.schemas import LoseLeadRequest
from app.services import audit_service


async def register_loss(
    db: AsyncSession, *, operator: User, lead: Lead, payload: LoseLeadRequest
) -> Lead:
    """登记丢单：必须多选 R1–R6 原因。"""
    if lead.status == LeadStatus.SIGNED:
        raise ConflictError("已签约客户不能标记丢单（请走闭环三的终止流程）")
    if lead.status == LeadStatus.LOST:
        raise ConflictError("该商机已登记为丢单")

    reasons = [r for r in (payload.lost_reason_types or []) if r in {x.value for x in RiskType}]
    if not reasons:
        raise ValidationError("判丢单时必须从 R1–R6 中选择至少一个原因")

    lead.status = LeadStatus.LOST
    lead.lost_at = datetime.now()
    lead.lost_reason_types = reasons
    lead.lost_remark = payload.remark
    # 丢单后不再参与自动回收与卡单预警
    lead.recycle_deadline = None
    lead.first_follow_deadline = None

    await audit_service.write_log(
        db,
        operator=operator,
        action="lose",
        target_type="lead",
        target_id=lead.id,
        target_name=lead.name,
        detail=(
            "登记丢单（环节 "
            f"{dicts.label_of(dicts.STAGE_LABELS, lead.current_stage) or '闭环一'}）："
            + "、".join(dicts.label_of(dicts.RISK_TYPE_LABELS, r) for r in reasons)
            + (f"；备注：{payload.remark}" if payload.remark else "")
        ),
    )
    await audit_service.notify_many(
        db,
        user_ids=await audit_service.manager_ids(db),
        title=f"商机丢单：{lead.name}",
        content=f"{lead.owner_name or '未指派'} 登记丢单，原因："
        + "、".join(dicts.label_of(dicts.RISK_TYPE_LABELS, r) for r in reasons),
        type_="lead_lost",
        biz_type="lead",
        biz_id=lead.id,
    )
    return lead


async def revive_lost(
    db: AsyncSession, *, operator: User, lead: Lead, remark: str | None = None
) -> Lead:
    """误判丢单可恢复（留日志）。"""
    if lead.status != LeadStatus.LOST:
        raise ConflictError("只有已丢单的商机可以恢复")
    lead.status = LeadStatus.VALID if lead.current_stage else LeadStatus.FOLLOWING
    lead.lost_at = None
    lead.lost_reason_types = []
    lead.lost_remark = None
    await audit_service.write_log(
        db,
        operator=operator,
        action="loss_revert",
        target_type="lead",
        target_id=lead.id,
        target_name=lead.name,
        detail=f"撤销丢单；备注：{remark or '无'}",
    )
    return lead


async def loss_analysis(
    db: AsyncSession,
    *,
    start: date | None = None,
    end: date | None = None,
    owner_id: str | None = None,
) -> dict:
    """丢单分布：按原因 / 按环节 / 按人 + 明细清单。"""
    start_dt = datetime.combine(start, time.min) if start else None
    end_dt = datetime.combine(end, time.max) if end else None

    stmt = select(Lead).where(Lead.is_deleted.is_(False), Lead.status == LeadStatus.LOST)
    if start_dt:
        stmt = stmt.where(Lead.lost_at >= start_dt)
    if end_dt:
        stmt = stmt.where(Lead.lost_at <= end_dt)
    if owner_id:
        stmt = stmt.where(Lead.owner_id == owner_id)
    leads = (await db.execute(stmt.order_by(Lead.lost_at.desc()))).scalars().all()

    total = len(leads)
    by_reason: dict[str, int] = {}
    by_stage: dict[str, int] = {}
    by_owner: dict[str, dict] = {}
    lost_amount = 0

    for lead in leads:
        lost_amount += lead.budget_amount_fen or 0
        for reason in lead.lost_reason_types or []:
            by_reason[reason] = by_reason.get(reason, 0) + 1
        stage = lead.current_stage or "closed_loop_one"
        by_stage[stage] = by_stage.get(stage, 0) + 1
        owner_key = lead.owner_id or "unassigned"
        item = by_owner.setdefault(
            owner_key,
            {"user_id": lead.owner_id, "user_name": lead.owner_name or "未指派", "count": 0},
        )
        item["count"] += 1

    reason_rows = [
        {
            "risk_type": risk.value,
            "label": dicts.label_of(dicts.RISK_TYPE_LABELS, risk.value),
            "count": by_reason.get(risk.value, 0),
            "rate": round(by_reason.get(risk.value, 0) / total, 4) if total else 0.0,
        }
        for risk in RiskType
    ]
    reason_rows.sort(key=lambda x: x["count"], reverse=True)

    stage_rows = [
        {
            "stage": stage,
            "label": dicts.label_of(dicts.STAGE_LABELS, stage)
            if stage != "closed_loop_one"
            else "闭环一（线索阶段）",
            "count": count,
        }
        for stage, count in sorted(by_stage.items(), key=lambda x: x[1], reverse=True)
    ]

    owner_rows = sorted(by_owner.values(), key=lambda x: x["count"], reverse=True)

    return {
        "start": start.isoformat() if start else None,
        "end": end.isoformat() if end else None,
        "total_lost": total,
        "lost_amount_fen": lost_amount,
        "by_reason": reason_rows,
        "by_stage": stage_rows,
        "by_owner": owner_rows,
        "top_reason": reason_rows[0] if reason_rows and reason_rows[0]["count"] else None,
    }


def loss_lead_out(lead: Lead) -> dict:
    from app.serializers import lead_out  # 局部导入避免循环依赖

    data = lead_out(lead)
    data["lost_at"] = lead.lost_at.isoformat() if lead.lost_at else None
    data["lost_remark"] = lead.lost_remark
    data["lost_reason_labels"] = [
        dicts.label_of(dicts.RISK_TYPE_LABELS, r) for r in (lead.lost_reason_types or [])
    ]
    return data
