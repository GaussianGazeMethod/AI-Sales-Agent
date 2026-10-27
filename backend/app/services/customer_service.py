"""客户层服务：同一客户的多个项目、客户整体价值及客户级跟进。

整体价值汇总进行中与已签约项目；客户级跟进不参与项目阶段计时。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import dicts
from app.core.deps import can_view_all_leads
from app.core.errors import ConflictError, NotFoundError
from app.models.activity import FollowUp
from app.models.customer import Customer
from app.models.enums import LeadStatus, RiskStatus
from app.models.lead import Lead
from app.models.stage import RiskAlert
from app.models.user import User
from app.schemas import CustomerFollowUpCreate, CustomerUpdate

#: 计入"客户整体价值"的状态：进行中（未指派/已指派/跟进中/有效/无效待判）+ 已签约
VALUE_STATUSES = (
    LeadStatus.UNASSIGNED,
    LeadStatus.ASSIGNED,
    LeadStatus.FOLLOWING,
    LeadStatus.VALID,
    LeadStatus.INVALID,
)
#: 已丢单：金额剔除，但要能看到
LOST_STATUS = LeadStatus.LOST
SIGNED_STATUS = LeadStatus.SIGNED


def norm_name(name: str) -> str:
    """客户名归一：去空白与全角空格；客户匹配还必须核对省市区。"""
    return (name or "").replace("\u3000", " ").strip()


async def find_customer(
    db: AsyncSession, name: str, *, province: str = "", city: str = "", district: str = ""
) -> Customer | None:
    key = norm_name(name)
    if not key:
        return None
    rows = (await db.execute(select(Customer))).scalars().all()
    for row in rows:
        if norm_name(row.name) == key and (row.province, row.city, row.district) == (
            province or "",
            city or "",
            district or "",
        ):
            return row
    return None


async def ensure_customer(
    db: AsyncSession,
    *,
    name: str,
    province: str = "",
    city: str = "",
    district: str = "",
    org_type: str | None = None,
    owner: User | None = None,
    created_from: str = "manual",
) -> tuple[Customer, bool]:
    """按名称和省市区取客户，没有就建。返回 (客户, 是否新建)。

    命中已有客户时会**补全空字段**（区域/机构类型/负责人），不覆盖已有值。
    """
    key = norm_name(name)
    if not key:
        raise ConflictError("客户名称不能为空")

    customer = await find_customer(db, key, province=province, city=city, district=district)
    if customer is not None:
        if not customer.province and province:
            customer.province = province
        if not customer.city and city:
            customer.city = city
        if not customer.district and district:
            customer.district = district
        if org_type and not customer.org_type:
            customer.org_type = org_type
        if owner is not None and not customer.owner_id:
            customer.owner_id = owner.id
            customer.owner_name = owner.name
        return customer, False

    customer = Customer(
        name=key,
        province=province or "",
        city=city or "",
        district=district or "",
        org_type=org_type or "public_school",
        owner_id=owner.id if owner else None,
        owner_name=owner.name if owner else None,
        created_from=created_from,
    )
    db.add(customer)
    await db.flush()
    return customer, True


def _amount_of(lead: Lead) -> int:
    """该项目的金额：已签约用合同额（回落到预算额），其余用预算额。"""
    if lead.status == SIGNED_STATUS:
        return int(lead.contract_amount_fen or lead.budget_amount_fen or 0)
    return int(lead.budget_amount_fen or 0)


async def _visible_leads(
    db: AsyncSession, customer_ids: list[str], user: User | None = None
) -> list[Lead]:
    if not customer_ids:
        return []
    rows = (
        (
            await db.execute(
                select(Lead).where(Lead.customer_id.in_(customer_ids), Lead.is_deleted.is_(False))
            )
        )
        .scalars()
        .all()
    )
    return [
        lead
        for lead in rows
        if user is None or can_view_all_leads(user) or lead.owner_id == user.id
    ]


async def metrics_for(
    db: AsyncSession, customers: list[Customer], user: User | None = None
) -> dict[str, dict]:
    """批量算客户指标（列表页用，避免 N+1）。"""
    ids = [c.id for c in customers]
    leads = await _visible_leads(db, ids, user)

    risk_rows = (
        await db.execute(
            select(RiskAlert.lead_id, func.count())
            .where(
                RiskAlert.status == RiskStatus.PENDING,
                RiskAlert.lead_id.in_(ids + [lead.id for lead in leads]),
            )
            .group_by(RiskAlert.lead_id)
        )
    ).all()
    risk_by_lead = {lead_id: n for lead_id, n in risk_rows}

    out: dict[str, dict] = {}
    for customer in customers:
        mine = [lead for lead in leads if lead.customer_id == customer.id]
        by_stage: dict[str, dict] = {}
        in_progress_value = signed_value = lost_value = 0
        counts = {"in_progress": 0, "signed": 0, "lost": 0, "other": 0}
        for lead in mine:
            amount = _amount_of(lead)
            if lead.status == LOST_STATUS:
                counts["lost"] += 1
                lost_value += int(lead.budget_amount_fen or 0)
            elif lead.status == SIGNED_STATUS:
                counts["signed"] += 1
                signed_value += amount
            elif lead.status in VALUE_STATUSES:
                counts["in_progress"] += 1
                in_progress_value += amount
            else:
                counts["other"] += 1

            # 阶段维度：无论是否签约都展示，便于"看每个阶段的金额"
            stage_key = lead.current_stage or "not_in_stage"
            bucket = by_stage.setdefault(
                stage_key,
                {
                    "stage": stage_key,
                    "stage_label": dicts.label_of(dicts.STAGE_LABELS, lead.current_stage)
                    or "未进入环节",
                    "amount_fen": 0,
                    "project_count": 0,
                    "signed": True,
                },
            )
            bucket["amount_fen"] += amount
            bucket["project_count"] += 1
            bucket["signed"] = bucket["signed"] and lead.status == SIGNED_STATUS

        out[customer.id] = {
            "project_count": len(mine),
            "in_progress_count": counts["in_progress"],
            "signed_count": counts["signed"],
            "lost_count": counts["lost"],
            # 「过程中价值」：未签约项目的金额合计
            "in_progress_value_fen": in_progress_value,
            "signed_value_fen": signed_value,
            "lost_value_fen": lost_value,
            "pending_risk_count": sum(risk_by_lead.get(lead.id, 0) for lead in mine),
            "stages": sorted(
                by_stage.values(),
                key=lambda b: (
                    list(dicts.STAGE_LABELS).index(b["stage"])
                    if b["stage"] in dicts.STAGE_LABELS
                    else 99
                ),
            ),
        }
    return out


async def projects_of(db: AsyncSession, customer_id: str, user: User | None = None) -> list[Lead]:
    """客户名下项目（可见），按"进行中优先 + 金额倒序"。"""
    rows = await _visible_leads(db, [customer_id], user)
    order = {
        LeadStatus.UNASSIGNED: 0,
        LeadStatus.ASSIGNED: 1,
        LeadStatus.FOLLOWING: 2,
        LeadStatus.VALID: 3,
        LeadStatus.INVALID: 4,
        LeadStatus.SIGNED: 5,
        LeadStatus.LOST: 6,
    }
    return sorted(rows, key=lambda lead: (order.get(lead.status, 9), -_amount_of(lead)))


async def followups_of(db: AsyncSession, customer_id: str) -> list[FollowUp]:
    """客户级跟进（跨项目的那些记录）。"""
    rows = (
        (
            await db.execute(
                select(FollowUp)
                .where(FollowUp.customer_id == customer_id, FollowUp.lead_id.is_(None))
                .order_by(FollowUp.followed_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


async def create_customer_followup(
    db: AsyncSession, *, user: User, customer: Customer, payload: CustomerFollowUpCreate
) -> FollowUp:
    """客户级跟进（渠道活动/跨项目复盘/决策链澄清）。

     刻意**不**做有效性判定、**不**改任何项目状态、**不**计环节时长 —— 它不属于任何单一项目。
    """
    follow = FollowUp(
        lead_id=None,
        customer_id=customer.id,
        user_id=user.id,
        user_name=user.name,
        method=payload.method,
        content=payload.content,
        followed_at=payload.followed_at or datetime.now(),
        next_follow_at=payload.next_follow_at,
    )
    db.add(follow)
    await db.flush()
    return follow


async def update_customer(
    db: AsyncSession, *, customer: Customer, payload: CustomerUpdate, operator: User
) -> Customer:
    data = payload.model_dump(exclude_unset=True)
    if "level" in data:
        customer.level = data["level"]
    if "remark" in data:
        customer.remark = data["remark"]
    if "owner_id" in data and data["owner_id"]:
        owner = (
            await db.execute(select(User).where(User.id == data["owner_id"]))
        ).scalar_one_or_none()
        if owner is None:
            raise NotFoundError("指定的客户负责人不存在")
        customer.owner_id = owner.id
        customer.owner_name = owner.name
    await db.flush()
    return customer


def customer_out(customer: Customer, metrics: dict | None = None) -> dict:
    """客户序列化（含指标）。"""
    data = {
        "id": customer.id,
        "name": customer.name,
        "province": customer.province,
        "city": customer.city,
        "district": customer.district,
        "region": f"{customer.province}{customer.city}{customer.district}",
        "org_type": customer.org_type,
        "org_type_label": dicts.label_of(dicts.ORG_TYPE_LABELS, customer.org_type),
        "owner_id": customer.owner_id,
        "owner_name": customer.owner_name,
        "level": customer.level,
        "remark": customer.remark,
        "created_at": customer.created_at.isoformat() if customer.created_at else None,
    }
    if metrics:
        data.update(metrics)
    return data


def lead_project_out(lead: Lead) -> dict:
    """客户详情里的项目卡片。"""
    return {
        "id": lead.id,
        "name": lead.name,
        "project_name": lead.project_name or "",
        "status": lead.status,
        "status_label": dicts.label_of(dicts.LEAD_STATUS_LABELS, lead.status),
        "current_stage": lead.current_stage,
        "current_stage_label": dicts.label_of(dicts.STAGE_LABELS, lead.current_stage),
        "amount_fen": _amount_of(lead),
        "budget_amount_fen": int(lead.budget_amount_fen or 0),
        "contract_amount_fen": int(lead.contract_amount_fen or 0),
        # 「过程中价值」= 未签约；已签约则计合同额
        "is_process_value": lead.status != SIGNED_STATUS,
        "value_label": "已签约"
        if lead.status == SIGNED_STATUS
        else ("已丢单" if lead.status == LOST_STATUS else "过程中价值"),
        "owner_name": lead.owner_name,
        "city": lead.city,
        "district": lead.district,
        "source_label": dicts.label_of(dicts.LEAD_SOURCE_LABELS, lead.source),
    }
