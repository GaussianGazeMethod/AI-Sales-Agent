"""闭环三 · 交付核心服务（P2-a）：合同、交付物清单、交付交接单、客户档案聚合。

 全系统**唯一一次真正的交接**就在这里：销售 → 运营交付部（spec/04 §5 三段式）。
- 编排 O2（签约完成时触发）：建合同 → 生成交付物清单（标准《项目服务清单》+ 非标待澄清）
  → 建交付交接单（待双方确认）→ 初始化 10 阶段进度 → 通知运营交付专员
"""

from __future__ import annotations

import io
import logging
from datetime import date, datetime
from uuid import uuid4

from openpyxl import Workbook
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import dicts
from app.core.errors import ConflictError, NotFoundError, PermissionDeniedError, ValidationError
from app.models.delivery import (
    Contract,
    DeliveryItem,
    HandoverSheet,
    RenewalAssessment,
    StandardServiceItem,
)
from app.models.enums import (
    ComplaintStatus,
    ContractStatus,
    IssueStatus,
    UserRole,
)
from app.models.lead import Lead
from app.models.operation import Complaint, OperationMetric, ProductIssue
from app.models.user import User
from app.schemas import (
    ContractCreate,
    ContractUpdate,
    DeliveryItemCreate,
    DeliveryItemUpdate,
    HandoverConfirm,
    HandoverCreate,
)
from app.services import audit_service

logger = logging.getLogger(__name__)

#: 客户级归属：一个客户一名运营交付专员（不区分合同数）
DELIVERY_ROLES = (UserRole.DELIVERY_STAFF, UserRole.DELIVERY_MANAGER)


# ══════════════ 合同 ══════════════


async def next_contract_no(db: AsyncSession, today: date | None = None) -> str:
    """生成合同编号：HT20261001-001（同日流水）。"""
    today = today or date.today()
    prefix = f"HT{today.strftime('%Y%m%d')}"
    count = int(
        (
            await db.execute(
                select(func.count())
                .select_from(Contract)
                .where(Contract.contract_no.like(f"{prefix}%"))
            )
        ).scalar()
        or 0
    )
    return f"{prefix}-{count + 1:03d}"


async def create_contract(db: AsyncSession, *, operator: User, payload: ContractCreate) -> Contract:
    lead = (
        await db.execute(select(Lead).where(Lead.id == payload.lead_id, Lead.is_deleted.is_(False)))
    ).scalar_one_or_none()
    if lead is None:
        raise NotFoundError("客户不存在")

    contract = Contract(
        lead_id=lead.id,
        customer_name=lead.name,
        contract_no=payload.contract_no or await next_contract_no(db),
        amount_fen=payload.amount_fen,
        sign_date=payload.sign_date or date.today(),
        service_start=payload.service_start,
        service_end=payload.service_end,
        acceptance_standard=payload.acceptance_standard,
        has_hardware=payload.has_hardware,
        attachment=payload.attachment,
        sales_owner_id=lead.owner_id,
        sales_owner_name=lead.owner_name,
        remark=payload.remark,
        current_phase=1,
        phase_entered_at=datetime.now(),
    )
    db.add(contract)
    await db.flush()

    await generate_standard_items(db, contract_id=contract.id)
    await audit_service.write_log(
        db,
        operator=operator,
        action="create",
        target_type="contract",
        target_id=contract.id,
        target_name=f"{contract.customer_name} {contract.contract_no}",
        detail=f"补录合同，金额 {contract.amount_fen / 100:.2f} 元",
    )
    return contract


async def contract_from_signed_lead(
    db: AsyncSession, *, lead: Lead, operator: User | None = None
) -> Contract:
    """【编排 O2】环节七签约完成 → 建合同 + 交付物清单 + 交接单（待确认）+ 10 阶段初始化。

    这是"销售 → 运营交付部"的唯一交接入口，幂等：同一客户已有执行中合同则直接返回。
    """
    from app.services import delivery_stage_service

    existing = (
        await db.execute(
            select(Contract).where(
                Contract.lead_id == lead.id, Contract.status == ContractStatus.EXECUTING
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    contract = Contract(
        lead_id=lead.id,
        customer_name=lead.name,
        contract_no=await next_contract_no(db),
        amount_fen=lead.contract_amount_fen or 0,
        sign_date=lead.sign_date or date.today(),
        service_start=lead.sign_date or date.today(),
        service_end=None,
        sales_owner_id=lead.owner_id,
        sales_owner_name=lead.owner_name,
        status=ContractStatus.EXECUTING,
        current_phase=1,
        phase_entered_at=datetime.now(),
    )
    db.add(contract)
    await db.flush()

    # 交付物清单：先落标准《项目服务清单》；合同特约的非标项由内部交接会补充
    await generate_standard_items(db, contract_id=contract.id)
    # 10 阶段进度初始化（按阶段材料配置建空记录）
    await delivery_stage_service.ensure_all_stages(db, contract)
    # 交付交接单（待双方确认）
    db.add(
        HandoverSheet(
            contract_id=contract.id,
            lead_id=lead.id,
            customer_name=lead.name,
            handover_user_id=lead.owner_id,
            handover_user_name=lead.owner_name,
            handover_at=datetime.now(),
            standard_note="按标准《项目服务清单》与客户在启动会逐条核对（三段式第一步）",
            nonstandard_note="合同中的非标内容待内部交接会澄清（三段式第二步）",
        )
    )
    await audit_service.write_log(
        db,
        operator=operator or User(id="system", username="system", name="系统", role=UserRole.ADMIN),
        action="handover_create",
        target_type="contract",
        target_id=contract.id,
        target_name=contract.customer_name,
        detail="【编排 O2】签约完成 → 自动建合同/交付物清单/交付交接单/10 阶段进度",
    )
    return contract


async def get_contract(db: AsyncSession, contract_id: str) -> Contract:
    contract = (
        await db.execute(select(Contract).where(Contract.id == contract_id))
    ).scalar_one_or_none()
    if contract is None:
        raise NotFoundError("合同不存在")
    return contract


async def get_contract_by_lead(db: AsyncSession, lead_id: str) -> Contract | None:
    return (
        (
            await db.execute(
                select(Contract)
                .where(Contract.lead_id == lead_id)
                .order_by(Contract.created_at.desc())
            )
        )
        .scalars()
        .first()
    )


async def update_contract(
    db: AsyncSession, *, operator: User, contract: Contract, payload: ContractUpdate
) -> Contract:
    data = payload.model_dump(exclude_unset=True)
    owner_id = data.pop("delivery_owner_id", None)
    if owner_id is not None and owner_id != contract.delivery_owner_id:
        await assign_delivery_owner(
            db, operator=operator, contract=contract, owner_id=owner_id, remark="调整运营交付专员"
        )
    for key, value in data.items():
        if value is not None:
            setattr(contract, key, value)
    await audit_service.write_log(
        db,
        operator=operator,
        action="update",
        target_type="contract",
        target_id=contract.id,
        target_name=contract.contract_no,
        detail="修改合同信息：" + "、".join(data.keys()),
    )
    return contract


async def assign_delivery_owner(
    db: AsyncSession,
    *,
    operator: User,
    contract: Contract,
    owner_id: str,
    remark: str | None = None,
) -> Contract:
    """由运营部管理人员**确认运营交付专员**（客户级归属）。"""
    owner = (await db.execute(select(User).where(User.id == owner_id))).scalar_one_or_none()
    if owner is None:
        raise NotFoundError("运营交付专员不存在")
    if owner.role not in DELIVERY_ROLES:
        raise ValidationError("只能指派运营交付部的人员")

    contract.delivery_owner_id = owner.id
    contract.delivery_owner_name = owner.name
    # 同一客户的其他合同也归同一人（客户级归属）
    siblings = (
        (
            await db.execute(
                select(Contract).where(
                    Contract.lead_id == contract.lead_id, Contract.id != contract.id
                )
            )
        )
        .scalars()
        .all()
    )
    for item in siblings:
        item.delivery_owner_id = owner.id
        item.delivery_owner_name = owner.name

    handover = await get_handover(db, contract.id, required=False)
    if handover is not None:
        handover.delivery_owner_id = owner.id
        handover.delivery_owner_name = owner.name
        handover.receiver_id = owner.id
        handover.receiver_name = owner.name

    await audit_service.notify(
        db,
        user_id=owner.id,
        title=f"你被指派为「{contract.customer_name}」的运营交付专员",
        content=f"合同 {contract.contract_no}｜{remark or ''}",
        type_="delivery_assigned",
        event="delivery_assigned",
        context={"lead_name": contract.customer_name},
        biz_type="contract",
        biz_id=contract.id,
    )
    await audit_service.write_log(
        db,
        operator=operator,
        action="assign",
        target_type="contract",
        target_id=contract.id,
        target_name=contract.customer_name,
        detail=f"确认运营交付专员：{owner.name}；{remark or ''}",
    )
    return contract


async def list_contracts(
    db: AsyncSession,
    *,
    user: User,
    keyword: str | None = None,
    status: str | None = None,
    delivery_owner_id: str | None = None,
    expiring_days: int | None = None,
    lead_id: str | None = None,
    offset: int = 0,
    limit: int = 20,
) -> tuple[list[Contract], int]:
    stmt = select(Contract)
    # 数据范围：运营专员只看自己负责的客户；管理层/管理员看全部
    if user.role == UserRole.DELIVERY_STAFF:
        stmt = stmt.where(Contract.delivery_owner_id == user.id)
    if keyword:
        kw = f"%{keyword.strip()}%"
        stmt = stmt.where(Contract.customer_name.like(kw) | Contract.contract_no.like(kw))
    if status:
        stmt = stmt.where(Contract.status == status)
    if delivery_owner_id:
        stmt = stmt.where(Contract.delivery_owner_id == delivery_owner_id)
    if lead_id:
        stmt = stmt.where(Contract.lead_id == lead_id)
    if expiring_days is not None:
        today = date.today()
        stmt = stmt.where(
            Contract.service_end.is_not(None),
            Contract.service_end <= date.fromordinal(today.toordinal() + expiring_days),
        )
    total = int((await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar() or 0)
    rows = (
        (
            await db.execute(
                stmt.order_by(Contract.service_end.is_(None), Contract.service_end.asc())
                .offset(offset)
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return list(rows), total


async def contract_out(db: AsyncSession, contract: Contract) -> dict:
    """合同输出：附带交付进度、交接状态、未闭环问题/投诉数、最新活跃率等派生字段。"""
    from app.models.delivery import DeliveryPhaseStandard, DeliveryStage
    from app.services import delivery_stage_service

    material_total = int(
        (
            await db.execute(
                select(func.count())
                .select_from(DeliveryPhaseStandard)
                .where(DeliveryPhaseStandard.is_active.is_(True))
            )
        ).scalar()
        or 0
    )
    material_done = int(
        (
            await db.execute(
                select(func.count())
                .select_from(DeliveryStage)
                .where(DeliveryStage.contract_id == contract.id, DeliveryStage.is_done.is_(True))
            )
        ).scalar()
        or 0
    )

    open_issues = int(
        (
            await db.execute(
                select(func.count())
                .select_from(ProductIssue)
                .where(
                    ProductIssue.contract_id == contract.id,
                    ProductIssue.status.in_([IssueStatus.PENDING, IssueStatus.PROCESSING]),
                )
            )
        ).scalar()
        or 0
    )
    open_complaints = int(
        (
            await db.execute(
                select(func.count())
                .select_from(Complaint)
                .where(
                    Complaint.contract_id == contract.id,
                    Complaint.status.in_([ComplaintStatus.PENDING, ComplaintStatus.PROCESSING]),
                )
            )
        ).scalar()
        or 0
    )
    latest_metric = (
        (
            await db.execute(
                select(OperationMetric)
                .where(OperationMetric.contract_id == contract.id)
                .order_by(OperationMetric.period.desc())
            )
        )
        .scalars()
        .first()
    )

    handover = await get_handover(db, contract.id, required=False)
    renewal = (
        (
            await db.execute(
                select(RenewalAssessment)
                .where(RenewalAssessment.contract_id == contract.id)
                .order_by(RenewalAssessment.created_at.desc())
            )
        )
        .scalars()
        .first()
    )

    stage_info = await delivery_stage_service.contract_stage_info(db, contract)
    days_to_expire = (contract.service_end - date.today()).days if contract.service_end else None

    return {
        "id": contract.id,
        "contract_no": contract.contract_no,
        "lead_id": contract.lead_id,
        "customer_name": contract.customer_name,
        "delivery_owner_id": contract.delivery_owner_id,
        "delivery_owner_name": contract.delivery_owner_name,
        "sales_owner_name": contract.sales_owner_name,
        "amount_fen": contract.amount_fen,
        "sign_date": contract.sign_date.isoformat() if contract.sign_date else None,
        "service_start": contract.service_start.isoformat() if contract.service_start else None,
        "service_end": contract.service_end.isoformat() if contract.service_end else None,
        "days_to_expire": days_to_expire,
        "acceptance_standard": contract.acceptance_standard,
        "has_hardware": contract.has_hardware,
        "status": contract.status,
        "status_label": dicts.label_of(dicts.CONTRACT_STATUS_LABELS, contract.status),
        "current_phase": contract.current_phase,
        "current_phase_label": dicts.DELIVERY_PHASE_LABELS.get(contract.current_phase, ""),
        "phase_entered_at": contract.phase_entered_at.isoformat()
        if contract.phase_entered_at
        else None,
        # 当前阶段停留（列表页直接用，避免前端逐行调用单阶段接口）
        "stay_days": stage_info["stay_days"],
        "base_days": stage_info["base_days"],
        "overdue": stage_info["overdue"],
        "has_baseline": stage_info["base_days"] > 0,
        "stage_done_count": stage_info["done_phases"],
        "stage_total_count": stage_info["total_phases"],
        "delivery_progress": round(stage_info["done_phases"] / stage_info["total_phases"], 4)
        if stage_info["total_phases"]
        else 0.0,
        "material_done_count": material_done,
        "material_total_count": material_total,
        "handover_status": (
            "none" if handover is None else ("confirmed" if handover.is_confirmed else "pending")
        ),
        "handover_status_label": (
            "未建交接单"
            if handover is None
            else ("双方已确认" if handover.is_confirmed else "待双方确认")
        ),
        "renew_status": renewal.status if renewal else "not_started",
        "renew_status_label": (
            dicts.label_of(dicts.RENEWAL_STATUS_LABELS, renewal.status) if renewal else "未启动"
        ),
        "latest_activity_rate": latest_metric.activity_rate if latest_metric else None,
        "open_issue_count": open_issues,
        "open_complaint_count": open_complaints,
        "remark": contract.remark,
        "created_at": contract.created_at.isoformat() if contract.created_at else None,
    }


# ══════════════ 交付物清单 ══════════════


async def list_delivery_items(db: AsyncSession, contract_id: str) -> list[DeliveryItem]:
    return list(
        (
            await db.execute(
                select(DeliveryItem)
                .where(DeliveryItem.contract_id == contract_id)
                .order_by(
                    DeliveryItem.is_standard.desc(), DeliveryItem.sort_order, DeliveryItem.name
                )
            )
        )
        .scalars()
        .all()
    )


async def add_delivery_item(
    db: AsyncSession, *, operator: User, contract: Contract, payload: DeliveryItemCreate
) -> DeliveryItem:
    item = DeliveryItem(
        contract_id=contract.id,
        name=payload.name,
        quantity=payload.quantity,
        unit=payload.unit,
        due_date=payload.due_date,
        acceptance_standard=payload.acceptance_standard,
        is_standard=payload.is_standard,
        remark=payload.remark,
        sort_order=payload.sort_order,
    )
    db.add(item)
    await db.flush()
    await audit_service.write_log(
        db,
        operator=operator,
        action="create",
        target_type="delivery_item",
        target_id=item.id,
        target_name=item.name,
        detail=f"{'标准项' if item.is_standard else '非标项'}｜客户 {contract.customer_name}",
    )
    return item


async def update_delivery_item(
    db: AsyncSession, *, operator: User, item: DeliveryItem, payload: DeliveryItemUpdate
) -> DeliveryItem:
    for key, value in payload.model_dump(exclude_unset=True).items():
        if value is not None:
            setattr(item, key, value)
    return item


async def delete_delivery_item(db: AsyncSession, *, operator: User, item: DeliveryItem) -> None:
    await audit_service.write_log(
        db,
        operator=operator,
        action="delete",
        target_type="delivery_item",
        target_id=item.id,
        target_name=item.name,
        detail="删除交付物",
    )
    await db.delete(item)


async def get_delivery_item(db: AsyncSession, item_id: str) -> DeliveryItem:
    item = (
        await db.execute(select(DeliveryItem).where(DeliveryItem.id == item_id))
    ).scalar_one_or_none()
    if item is None:
        raise NotFoundError("交付物不存在")
    return item


async def generate_standard_items(db: AsyncSession, *, contract_id: str) -> int:
    """按标准《项目服务清单》生成标准交付物（幂等：同名同合同不重复）。"""
    from app.models.delivery import StandardServiceItem

    templates = (
        (
            await db.execute(
                select(StandardServiceItem)
                .where(StandardServiceItem.is_active.is_(True))
                .order_by(StandardServiceItem.sort_order)
            )
        )
        .scalars()
        .all()
    )
    existing = {
        row.name
        for row in (
            await db.execute(select(DeliveryItem).where(DeliveryItem.contract_id == contract_id))
        )
        .scalars()
        .all()
    }
    created = 0
    for idx, tpl in enumerate(templates):
        if tpl.name in existing:
            continue
        db.add(
            DeliveryItem(
                contract_id=contract_id,
                name=tpl.name,
                quantity=tpl.quantity,
                unit=tpl.unit,
                acceptance_standard=tpl.acceptance_standard,
                is_standard=True,
                sort_order=idx,
            )
        )
        created += 1
    if created:
        await db.flush()
    return created


async def list_standard_service_items(db: AsyncSession) -> list[StandardServiceItem]:
    return list(
        (
            await db.execute(
                select(StandardServiceItem)
                .where(StandardServiceItem.is_active.is_(True))
                .order_by(StandardServiceItem.sort_order)
            )
        )
        .scalars()
        .all()
    )


# ══════════════ 交付交接单 ══════════════


async def get_handover(
    db: AsyncSession, contract_id: str, *, required: bool = True
) -> HandoverSheet | None:
    handover = (
        await db.execute(select(HandoverSheet).where(HandoverSheet.contract_id == contract_id))
    ).scalar_one_or_none()
    if handover is None and required:
        raise NotFoundError("该合同还没有交付交接单")
    return handover


async def upsert_handover(
    db: AsyncSession, *, operator: User, contract: Contract, payload: HandoverCreate
) -> HandoverSheet:
    handover = await get_handover(db, contract.id, required=False)
    if handover is None:
        handover = HandoverSheet(
            contract_id=contract.id, lead_id=contract.lead_id, customer_name=contract.customer_name
        )
        db.add(handover)
        await db.flush()

    if handover.is_confirmed:
        raise ConflictError("交接单已双方确认，如需修改请先撤销确认")

    async def resolve(user_id: str | None) -> tuple[str | None, str | None]:
        if not user_id:
            return None, None
        user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
        if user is None:
            raise NotFoundError("指定的用户不存在")
        return user.id, user.name

    if payload.handover_user_id is not None:
        handover.handover_user_id, handover.handover_user_name = await resolve(
            payload.handover_user_id
        )
    if payload.receiver_id is not None:
        handover.receiver_id, handover.receiver_name = await resolve(payload.receiver_id)
    if payload.delivery_owner_id is not None:
        await assign_delivery_owner(
            db,
            operator=operator,
            contract=contract,
            owner_id=payload.delivery_owner_id,
            remark="交接单指定",
        )
    if payload.handover_at is not None:
        handover.handover_at = payload.handover_at
    if payload.standard_note is not None:
        handover.standard_note = payload.standard_note
    if payload.nonstandard_note is not None:
        handover.nonstandard_note = payload.nonstandard_note
    if payload.remark is not None:
        handover.remark = payload.remark
    if handover.handover_at is None:
        handover.handover_at = datetime.now()

    await audit_service.write_log(
        db,
        operator=operator,
        action="update",
        target_type="handover",
        target_id=handover.id,
        target_name=handover.customer_name,
        detail="填写/更新交付交接单",
    )
    return handover


async def confirm_handover(
    db: AsyncSession, *, operator: User, handover: HandoverSheet, payload: HandoverConfirm
) -> HandoverSheet:
    """双方确认（三段式第三步的前置：双方签字确认）。"""
    if payload.role == "sales":
        if operator.role not in {UserRole.MARKET_STAFF, UserRole.MARKET_MANAGER, UserRole.ADMIN}:
            raise PermissionDeniedError("销售侧确认需市场部人员操作")
        handover.sales_confirmed = True
        handover.sales_confirmed_at = datetime.now()
    elif payload.role == "delivery":
        if operator.role not in {*DELIVERY_ROLES, UserRole.ADMIN}:
            raise PermissionDeniedError("交付侧确认需运营交付部人员操作")
        handover.delivery_confirmed = True
        handover.delivery_confirmed_at = datetime.now()
    else:
        raise ValidationError("role 只能是 sales 或 delivery")

    if payload.remark:
        handover.remark = payload.remark

    if handover.is_confirmed:
        await audit_service.notify(
            db,
            user_id=handover.delivery_owner_id or "",
            title=f"交付交接单已双方确认：{handover.customer_name}",
            content="可以筹备三方项目启动会，与客户逐条核对《项目服务清单》",
            type_="handover_confirmed",
            event="handover_confirmed",
            context={"lead_name": handover.customer_name, "role": payload.role},
            biz_type="contract",
            biz_id=handover.contract_id,
        )

    await audit_service.write_log(
        db,
        operator=operator,
        action="handover_confirm",
        target_type="handover",
        target_id=handover.id,
        target_name=handover.customer_name,
        detail=f"{'销售侧' if payload.role == 'sales' else '交付侧'}确认交接单；"
        f"双方确认完成={handover.is_confirmed}",
    )
    return handover


def handover_out(
    handover: HandoverSheet | None, items: list[DeliveryItem] | None = None
) -> dict | None:
    if handover is None:
        return None
    return {
        "id": handover.id,
        "contract_id": handover.contract_id,
        "customer_name": handover.customer_name,
        "handover_user_id": handover.handover_user_id,
        "handover_user_name": handover.handover_user_name,
        "receiver_id": handover.receiver_id,
        "receiver_name": handover.receiver_name,
        "delivery_owner_id": handover.delivery_owner_id,
        "delivery_owner_name": handover.delivery_owner_name,
        "handover_at": handover.handover_at.isoformat() if handover.handover_at else None,
        "standard_note": handover.standard_note,
        "nonstandard_note": handover.nonstandard_note,
        "sales_confirmed": handover.sales_confirmed,
        "sales_confirmed_at": handover.sales_confirmed_at.isoformat()
        if handover.sales_confirmed_at
        else None,
        "delivery_confirmed": handover.delivery_confirmed,
        "delivery_confirmed_at": handover.delivery_confirmed_at.isoformat()
        if handover.delivery_confirmed_at
        else None,
        "is_confirmed": handover.is_confirmed,
        "remark": handover.remark,
        "items": [delivery_item_out(x) for x in (items or [])],
    }


def delivery_item_out(item: DeliveryItem) -> dict:
    return {
        "id": item.id,
        "contract_id": item.contract_id,
        "name": item.name,
        "quantity": item.quantity,
        "unit": item.unit,
        "due_date": item.due_date.isoformat() if item.due_date else None,
        "acceptance_standard": item.acceptance_standard,
        "is_standard": item.is_standard,
        "is_standard_label": "标准项" if item.is_standard else "非标项",
        "sort_order": item.sort_order,
        "remark": item.remark,
        "created_at": item.created_at.isoformat() if item.created_at else None,
    }


# ══════════════ 客户档案聚合（3.2）══════════════


async def delivery_overview(db: AsyncSession, contract: Contract) -> dict:
    """客户档案总览：合同 + 交接单 + 交付物 + 阶段 + 运营 + 问题 + 投诉 + 验收 + 续费。"""
    from app.services import (
        acceptance_service,
        delivery_stage_service,
        issue_service,
        operation_service,
    )

    items = await list_delivery_items(db, contract.id)
    handover = await get_handover(db, contract.id, required=False)
    progress = await delivery_stage_service.contract_progress(db, contract)
    stages_summary = await delivery_stage_service.stages_summary(db, contract)
    records = await operation_service.list_records(db, contract_id=contract.id, limit=20)
    metrics = await operation_service.list_metrics(db, contract_id=contract.id, limit=12)
    issues = await issue_service.list_issues(db, contract_id=contract.id, limit=50)
    complaints = await issue_service.list_complaints(db, contract_id=contract.id, limit=50)
    acceptances = await acceptance_service.list_acceptances(db, contract_id=contract.id)
    renewals = await acceptance_service.list_renewals(db, contract_id=contract.id)

    return {
        "contract": await contract_out(db, contract),
        "handover": handover_out(handover, items),
        "items": [delivery_item_out(x) for x in items],
        "progress": progress,
        "stages_summary": stages_summary,
        "operation_records": [operation_service.record_out(r) for r in records],
        "contact_rhythm": await operation_service.contact_rhythm(db, contract),
        "metrics": [operation_service.metric_out(m) for m in metrics],
        "issues": [issue_service.issue_out(i) for i in issues],
        "complaints": [issue_service.complaint_out(c) for c in complaints],
        "acceptances": [acceptance_service.acceptance_out(a) for a in acceptances],
        "renewals": [acceptance_service.renewal_out(r) for r in renewals],
    }


async def delivery_workbench(db: AsyncSession, *, user: User) -> dict:
    """运营交付工作台（0.2 工作台 / 3.5 列表）：按阶段分组的我的客户 + 待处理事项。"""
    from app.services import delivery_stage_service, issue_service

    contracts, _ = await list_contracts(
        db,
        user=user,
        status=ContractStatus.EXECUTING,
        offset=0,
        limit=200,
    )
    grouped: dict[int, list[dict]] = {}
    for contract in contracts:
        info = await delivery_stage_service.contract_stage_info(db, contract)
        grouped.setdefault(contract.current_phase, []).append(
            {
                "contract_id": contract.id,
                "customer_name": contract.customer_name,
                "phase": contract.current_phase,
                "phase_label": dicts.DELIVERY_PHASE_LABELS.get(contract.current_phase, ""),
                "stay_days": info["stay_days"],
                "base_days": info["base_days"],
                "overdue": info["overdue"],
                "material_done": info["material_done"],
                "material_total": info["material_total"],
                "days_to_expire": (
                    (contract.service_end - date.today()).days if contract.service_end else None
                ),
            }
        )

    pending_issues = await issue_service.list_issues(
        db, status=IssueStatus.PENDING, owner_scope=None, limit=20
    )
    open_complaints = await issue_service.list_complaints(
        db, status=ComplaintStatus.PENDING, limit=20
    )

    return {
        "total_customers": len(contracts),
        "by_phase": [
            {
                "phase": phase,
                "phase_label": dicts.DELIVERY_PHASE_LABELS.get(phase, ""),
                "count": len(items),
                "items": items,
            }
            for phase, items in sorted(grouped.items())
        ],
        "pending_issues": [issue_service.issue_out(i) for i in pending_issues],
        "open_complaints": [issue_service.complaint_out(c) for c in open_complaints],
    }


# ══════════════ 交付物清单批量导入（历史数据迁移）══════════════
#
# 表头口径：对标飞书「运营交付部」在线表格的列。
# 支持两种用法：
#   ① 全局导入（POST /delivery-items/import）——必须给「客户名称」或「合同编号」；
#   ② 合同内导入（POST /contracts/{id}/delivery-items/import）——两列可省。
# 列名支持常见别名，导出历史表格不必改名。

DELIVERY_ITEM_HEADERS = [
    "客户名称",
    "合同编号",
    "交付物名称",
    "数量",
    "单位",
    "验收标准",
    "是否标准服务",
    "计划完成时间",
    "排序",
    "备注",
]

DELIVERY_ITEM_SAMPLE_ROW = [
    "示例：XX市第三中学",
    "",
    "平台部署与开户",
    "1",
    "项",
    "按《项目服务清单》逐条验收，功能测试通过",
    "是",
    "2026-10-15",
    1,
    "示例行，导入前请删除",
]

DELIVERY_ITEM_ALIASES: dict[str, list[str]] = {
    "customer_name": ["客户名称", "客户", "学校名称", "客户名字"],
    "contract_no": ["合同编号", "合同号", "合同编码"],
    "name": ["交付物名称", "交付物", "服务项", "服务内容", "名称", "项目名称"],
    "quantity": ["数量", "数目"],
    "unit": ["单位"],
    "acceptance_standard": ["验收标准", "验收口径", "验收要求"],
    "is_standard": ["是否标准服务", "是否标准", "标准服务", "标准/非标", "标准项"],
    "due_date": ["计划完成时间", "计划完成", "完成时间", "计划完成日期", "截止时间"],
    "sort_order": ["排序", "序号"],
    "remark": ["备注", "说明"],
}


def build_delivery_item_template() -> bytes:
    """交付物清单导入模板（含填写说明）。"""
    wb = Workbook()
    ws = wb.active
    ws.title = "交付物清单导入"
    ws.append(DELIVERY_ITEM_HEADERS)
    ws.append(DELIVERY_ITEM_SAMPLE_ROW)
    for idx, header in enumerate(DELIVERY_ITEM_HEADERS, start=1):
        ws.column_dimensions[ws.cell(row=1, column=idx).column_letter].width = max(
            12, len(header) * 2 + 4
        )

    tips = wb.create_sheet("填写说明")
    tips.append(["列名", "是否必填", "说明"])
    tips.append(["客户名称", "全局导入必填", "与系统中的客户名称一致；合同内导入可省"])
    tips.append(["合同编号", "选填", "填了按编号精确匹配（优先级高于客户名称）"])
    tips.append(["交付物名称", "必填", "如「平台部署与开户」「线下培训」"])
    tips.append(["数量 / 单位", "选填", "文本；如 1 / 项、2 / 场"])
    tips.append(["验收标准", "选填", "客户验收时逐条对照的口径"])
    tips.append(["是否标准服务", "选填", "是 → 标准《项目服务清单》项；否/空 → 合同非标项"])
    tips.append(["计划完成时间", "选填", "YYYY-MM-DD（也认 2026/10/15、2026年10月15日）"])
    tips.append(["排序", "选填", "数字，越小越靠前"])
    tips.append(["备注", "选填", ""])
    tips.append(["", "", ""])
    tips.append(["提示", "", "同一合同下同名交付物会跳过（不重复建），并在结果里记为「跳过」"])
    tips.column_dimensions["A"].width = 22
    tips.column_dimensions["B"].width = 14
    tips.column_dimensions["C"].width = 80

    stream = io.BytesIO()
    wb.save(stream)
    return stream.getvalue()


async def import_delivery_items(
    db: AsyncSession,
    *,
    operator: User,
    filename: str,
    content: bytes,
    contract: Contract | None = None,
) -> dict:
    """导入交付物清单（Excel）：逐行校验 → 入库 → 失败明细。

    确定性匹配：客户按「合同编号」精确匹配，其次按「客户名称」精确匹配；
    同名多条时要求改用合同编号（避免挂错客户）。
    """
    from app.services import import_service

    header, rows = import_service.read_first_sheet(content)
    cell, index = import_service.cell_reader(header, DELIVERY_ITEM_ALIASES, ())
    if "name" not in index:
        raise ValidationError("表头缺少必需列：交付物名称（可写「交付物 / 服务项 / 名称」）")
    if contract is None and "customer_name" not in index and "contract_no" not in index:
        raise ValidationError("表头缺少必需列：客户名称 或 合同编号")

    batch_no = f"DI{datetime.now().strftime('%Y%m%d%H%M%S')}{uuid4().hex[:4].upper()}"
    total = success = skipped = 0
    errors: list[dict] = []

    for row_no, row in enumerate(rows, start=2):
        if row is None or all(import_service._cell_text(c) == "" for c in row):
            continue
        total += 1
        cell, _ = import_service.cell_reader(header, DELIVERY_ITEM_ALIASES, row)

        customer_name = cell("customer_name")
        if customer_name.startswith("示例："):
            total -= 1
            continue
        name = cell("name")
        if not name:
            errors.append({"row": row_no, "message": "交付物名称为空"})
            continue

        lead, row_contract, error = await import_service.resolve_customer(
            db,
            contract=contract,
            contract_no=cell("contract_no"),
            customer_name=customer_name,
        )
        if error:
            errors.append({"row": row_no, "message": error})
            continue
        if row_contract is None:
            who = lead.name if lead else customer_name
            errors.append({"row": row_no, "message": f"客户「{who}」还没有合同，请先签约"})
            continue

        # 幂等：同一合同下同名交付物跳过（重复导入历史表格不产生重复行）
        exists = (
            await db.execute(
                select(DeliveryItem).where(
                    DeliveryItem.contract_id == row_contract.id, DeliveryItem.name == name
                )
            )
        ).scalar_one_or_none()
        if exists is not None:
            skipped += 1
            continue

        due_raw = cell("due_date")
        due_date = import_service.parse_date_cell(due_raw)
        if due_raw and due_date is None:
            errors.append(
                {"row": row_no, "message": f"计划完成时间无法识别：{due_raw}（应为 YYYY-MM-DD）"}
            )
            continue

        sort_raw = cell("sort_order")
        sort_order = import_service.parse_int_cell(sort_raw) or 0

        db.add(
            DeliveryItem(
                contract_id=row_contract.id,
                name=name,
                quantity=cell("quantity") or None,
                unit=cell("unit") or None,
                due_date=due_date,
                acceptance_standard=cell("acceptance_standard") or None,
                is_standard=import_service._truthy(cell("is_standard")),
                sort_order=sort_order,
                remark=cell("remark") or None,
            )
        )
        success += 1

    await db.flush()
    await audit_service.write_log(
        db,
        operator=operator,
        action="import",
        target_type="delivery_item",
        target_name=filename,
        detail=(
            f"交付物清单导入：共 {total} 条，成功 {success}，跳过 {skipped}，"
            f"失败 {total - success - skipped}（批次 {batch_no}）"
        ),
    )
    return {
        "batch_no": batch_no,
        "total": total,
        "success": success,
        "skipped": skipped,
        "failed": total - success - skipped,
        "errors": errors[:200],
    }
