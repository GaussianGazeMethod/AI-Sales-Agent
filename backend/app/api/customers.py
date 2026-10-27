"""客户层接口：客户列表 / 客户详情（聚合多个项目商机）/ 客户级跟进。

业务规则：「客户-项目两层模型」，理由是"这样可以看一个客户整体的价值"：

* `GET /customers`            客户列表：项目数、**过程中价值**、已签约额、待处理风险数
* `GET /customers/{id}`       客户详情：名下项目（各自环节+金额）、跨项目跟进时间线、风险、合同
* `POST /customers/{id}/followups`  客户级跟进（渠道活动/跨项目复盘/决策链澄清，不进环节）
* `PATCH /customers/{id}`     维护客户信息（等级/备注/负责人）
* `GET /customers/of-lead/{lead_id}`  该项目属于哪个客户 + 同客户其他项目（商机页用）
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import dicts
from app.core.deps import can_view_all_leads, require_admin, require_lead_view, require_lead_write
from app.core.errors import NotFoundError, PermissionDeniedError
from app.database import get_db
from app.models.activity import FollowUp
from app.models.customer import Customer
from app.models.delivery import Contract
from app.models.enums import RiskStatus
from app.models.lead import Contact, Lead
from app.models.stage import RiskAlert
from app.models.user import User
from app.schemas import CustomerFollowUpCreate, CustomerUpdate
from app.services import customer_service, lead_service

router = APIRouter(tags=["客户"])


@router.get("/customers", summary="客户列表（含过程中价值/已签约额/项目数）")
async def list_customers(
    keyword: str | None = Query(None, description="按客户名模糊搜索"),
    only_mine: bool = Query(False, description="只看我负责的客户"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_lead_view),
) -> dict:
    stmt = select(Customer)
    if keyword:
        stmt = stmt.where(Customer.name.like(f"%{keyword.strip()}%"))
    if only_mine:
        stmt = stmt.where(Customer.owner_id == user.id)
    rows = (await db.execute(stmt.order_by(Customer.name))).scalars().all()

    metrics = await customer_service.metrics_for(db, list(rows), user)
    items = [
        customer_service.customer_out(c, metrics.get(c.id))
        for c in rows
        if can_view_all_leads(user) or metrics.get(c.id, {}).get("project_count", 0)
    ]

    # 列表页的合计：一眼看出"客户盘子有多大"（过程中价值 vs 已签约）
    total_in_progress = sum(i.get("in_progress_value_fen", 0) for i in items)
    total_signed = sum(i.get("signed_value_fen", 0) for i in items)
    start = (page - 1) * page_size
    return {
        "items": items[start : start + page_size],
        "total": len(items),
        "page": page,
        "page_size": page_size,
        "summary": {
            "customer_count": len(items),
            "project_count": sum(i.get("project_count", 0) for i in items),
            "in_progress_value_fen": total_in_progress,
            "signed_value_fen": total_signed,
            # 口径说明随接口返回，前端直接展示，避免"数字对不上"的误会
            "value_note": "过程中价值=未签约项目金额合计；已签约按合同额计；已丢单项目金额已剔除",
        },
        "value_note": "过程中价值=未签约项目金额合计；已签约按合同额计；已丢单项目金额已剔除",
    }


async def _load_customer(db: AsyncSession, customer_id: str, user: User) -> Customer:
    customer = (
        await db.execute(select(Customer).where(Customer.id == customer_id))
    ).scalar_one_or_none()
    if customer is None:
        raise NotFoundError("客户不存在")
    if not can_view_all_leads(user) and not await customer_service.projects_of(
        db, customer_id, user
    ):
        raise PermissionDeniedError("只能查看本人负责项目所属的客户")
    return customer


@router.get("/customers/{customer_id}", summary="客户详情（项目清单/跨项目跟进/风险/合同）")
async def customer_detail(
    customer_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_lead_view),
) -> dict:
    customer = await _load_customer(db, customer_id, user)
    metrics = (await customer_service.metrics_for(db, [customer], user)).get(customer.id, {})
    projects = await customer_service.projects_of(db, customer.id, user)

    contacts = (
        (
            await db.execute(
                select(Contact).where(Contact.lead_id.in_([p.id for p in projects] or [""]))
            )
        )
        .scalars()
        .all()
    )
    contracts = (
        (await db.execute(select(Contract).where(Contract.lead_id.in_([p.id for p in projects]))))
        .scalars()
        .all()
    )
    pending_risks = (
        (
            await db.execute(
                select(RiskAlert)
                .where(
                    RiskAlert.status == RiskStatus.PENDING,
                    RiskAlert.lead_id.in_([p.id for p in projects] or [""]),
                )
                .order_by(RiskAlert.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    customer_followups = await customer_service.followups_of(db, customer.id)
    if not can_view_all_leads(user):
        customer_followups = [f for f in customer_followups if f.user_id == user.id]
    project_followup_counts = dict(
        (
            await db.execute(
                select(FollowUp.lead_id, func.count())
                .where(FollowUp.lead_id.in_([p.id for p in projects] or [""]))
                .group_by(FollowUp.lead_id)
            )
        ).all()
    )

    return {
        "customer": customer_service.customer_out(customer, metrics),
        "metrics": metrics,
        "projects": [customer_service.lead_project_out(p) for p in projects],
        "contacts": [
            {
                "id": c.id,
                "lead_id": c.lead_id,
                "name": c.name,
                "position": c.position,
                "phone": c.phone,
                "wechat": c.wechat,
                "is_primary": c.is_primary,
                "remark": c.remark,
            }
            for c in contacts
        ],
        "followups": [
            {
                "id": f.id,
                "scope": "customer",
                "scope_label": "客户级（不进环节）",
                "user_name": f.user_name,
                "method": f.method,
                "method_label": dicts.label_of(dicts.FOLLOW_METHOD_LABELS, f.method),
                "content": f.content,
                "followed_at": f.followed_at.isoformat() if f.followed_at else None,
                "next_follow_at": f.next_follow_at.isoformat() if f.next_follow_at else None,
            }
            for f in customer_followups
        ],
        "followup_counts": {
            "customer_level": len(customer_followups),
            "by_project": {p.id: int(project_followup_counts.get(p.id, 0)) for p in projects},
        },
        "risks": [
            {
                "id": r.id,
                "lead_id": r.lead_id,
                "kind": r.kind,
                "kind_label": dicts.label_of(dicts.ALERT_KIND_LABELS, r.kind),
                "risk_type": r.risk_type,
                "risk_type_label": dicts.label_of(dicts.RISK_TYPE_LABELS, r.risk_type),
                "status": r.status,
                "trigger_rule": r.trigger_rule,
                "description": r.description,
                "handle_note": r.handle_note,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in pending_risks
        ],
        "contracts": [
            {
                "id": c.id,
                "contract_no": c.contract_no,
                "amount_fen": int(c.amount_fen or 0),
                "status": c.status,
            }
            for c in contracts
        ],
    }


@router.get("/customers/of-lead/{lead_id}", summary="该商机属于哪个客户 + 同客户其他项目")
async def customer_of_lead(
    lead_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_lead_view),
) -> dict:
    lead = (await db.execute(select(Lead).where(Lead.id == lead_id))).scalar_one_or_none()
    if lead is None:
        raise NotFoundError("线索不存在")

    lead_service.assert_can_view(user, lead)
    customer = None
    if lead.customer_id:
        customer = (
            await db.execute(select(Customer).where(Customer.id == lead.customer_id))
        ).scalar_one_or_none()
    if customer is None:
        # 兜底：历史数据未回填 customer_id 时按名称匹配
        customer = await customer_service.find_customer(
            db, lead.name, province=lead.province, city=lead.city, district=lead.district
        )

    if customer is None:
        return {"customer": None, "siblings": [], "metrics": {}}

    metrics = (await customer_service.metrics_for(db, [customer], user)).get(customer.id, {})
    siblings = [
        customer_service.lead_project_out(p)
        for p in await customer_service.projects_of(db, customer.id, user)
        if p.id != lead.id
    ]
    return {
        "customer": customer_service.customer_out(customer, metrics),
        "metrics": metrics,
        "siblings": siblings,
    }


@router.post("/customers/{customer_id}/followups", summary="客户级跟进（不进环节、不计时长）")
async def create_customer_followup(
    customer_id: str,
    payload: CustomerFollowUpCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_lead_write),
) -> dict:
    customer = await _load_customer(db, customer_id, user)
    follow = await customer_service.create_customer_followup(
        db, user=user, customer=customer, payload=payload
    )
    await db.commit()
    return {
        "ok": True,
        "followup": {
            "id": follow.id,
            "customer_id": customer.id,
            "content": follow.content,
            "followed_at": follow.followed_at.isoformat() if follow.followed_at else None,
            "note": "客户级记录：不推进任何项目环节，也不计环节时长",
        },
    }


@router.patch("/customers/{customer_id}", summary="维护客户信息（等级/备注/负责人）")
async def patch_customer(
    customer_id: str,
    payload: CustomerUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
) -> dict:
    customer = await _load_customer(db, customer_id, user)
    await customer_service.update_customer(db, customer=customer, payload=payload, operator=user)
    await db.commit()
    metrics = (await customer_service.metrics_for(db, [customer], user)).get(customer.id, {})
    return {"ok": True, "customer": customer_service.customer_out(customer, metrics)}
