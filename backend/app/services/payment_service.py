"""P1-d 回款服务（页面 2.6 / 场景 S2-9）：回款登记与统计。

主表 `paid_amount_fen` 由本表汇总而来，避免两处口径不一致；
回款金额是业绩目标的一部分（spec/01 §4.1 考核口径）。
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import dicts
from app.core.errors import NotFoundError, ValidationError
from app.models.lead import Lead
from app.models.report import PaymentRecord
from app.models.user import User
from app.schemas import PaymentCreate, PaymentUpdate
from app.services import audit_service


async def recalc_paid(db: AsyncSession, lead_id: str) -> int:
    """按回款记录重算主表已回款金额（分），返回新值。"""
    total = int(
        (
            await db.execute(
                select(func.coalesce(func.sum(PaymentRecord.amount_fen), 0)).where(
                    PaymentRecord.lead_id == lead_id
                )
            )
        ).scalar()
        or 0
    )
    lead = (await db.execute(select(Lead).where(Lead.id == lead_id))).scalar_one_or_none()
    if lead is not None:
        lead.paid_amount_fen = total
    return total


async def create_payment(
    db: AsyncSession, *, operator: User, payload: PaymentCreate
) -> PaymentRecord:
    lead = (
        await db.execute(select(Lead).where(Lead.id == payload.lead_id, Lead.is_deleted.is_(False)))
    ).scalar_one_or_none()
    if lead is None:
        raise NotFoundError("商机/客户不存在")
    if payload.amount_fen <= 0:
        raise ValidationError("回款金额必须大于 0")

    record = PaymentRecord(
        lead_id=lead.id,
        lead_name=lead.name,
        amount_fen=payload.amount_fen,
        paid_date=payload.paid_date or date.today(),
        method=payload.method,
        period_no=payload.period_no,
        remark=payload.remark,
        operator_id=operator.id,
        operator_name=operator.name,
    )
    db.add(record)
    await db.flush()
    await recalc_paid(db, lead.id)

    await audit_service.write_log(
        db,
        operator=operator,
        action="payment",
        target_type="lead",
        target_id=lead.id,
        target_name=lead.name,
        detail=f"登记回款 {payload.amount_fen / 100:.2f} 元（{payload.period_no or '未注明期次'}）",
    )
    return record


async def update_payment(
    db: AsyncSession, *, operator: User, record: PaymentRecord, payload: PaymentUpdate
) -> PaymentRecord:
    data = payload.model_dump(exclude_unset=True)
    for key, value in data.items():
        if value is not None:
            setattr(record, key, value)
    await db.flush()
    await recalc_paid(db, record.lead_id)
    await audit_service.write_log(
        db,
        operator=operator,
        action="payment_update",
        target_type="lead",
        target_id=record.lead_id,
        target_name=record.lead_name,
        detail="修改回款记录",
    )
    return record


async def delete_payment(db: AsyncSession, *, operator: User, record: PaymentRecord) -> None:
    lead_id, lead_name = record.lead_id, record.lead_name
    await db.delete(record)
    await db.flush()
    await recalc_paid(db, lead_id)
    await audit_service.write_log(
        db,
        operator=operator,
        action="payment_delete",
        target_type="lead",
        target_id=lead_id,
        target_name=lead_name,
        detail="删除回款记录",
    )


async def get_payment(db: AsyncSession, record_id: str) -> PaymentRecord:
    record = (
        await db.execute(select(PaymentRecord).where(PaymentRecord.id == record_id))
    ).scalar_one_or_none()
    if record is None:
        raise NotFoundError("回款记录不存在")
    return record


async def list_payments(
    db: AsyncSession,
    *,
    lead_id: str | None = None,
    owner_id: str | None = None,
    start: date | None = None,
    end: date | None = None,
    offset: int = 0,
    limit: int = 20,
) -> tuple[list[PaymentRecord], int]:
    stmt = select(PaymentRecord)
    if lead_id:
        stmt = stmt.where(PaymentRecord.lead_id == lead_id)
    if start:
        stmt = stmt.where(PaymentRecord.paid_date >= start)
    if end:
        stmt = stmt.where(PaymentRecord.paid_date <= end)
    if owner_id:
        stmt = stmt.where(
            PaymentRecord.lead_id.in_(select(Lead.id).where(Lead.owner_id == owner_id))
        )
    total = int((await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar() or 0)
    rows = (
        (
            await db.execute(
                stmt.order_by(PaymentRecord.paid_date.desc()).offset(offset).limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return list(rows), total


async def summary(
    db: AsyncSession,
    *,
    owner_id: str | None = None,
    start: date | None = None,
    end: date | None = None,
) -> dict:
    """回款总览：合同额 / 已回款 / 未回款 / 未回款清单 / 按客户明细。"""
    lead_stmt = select(Lead).where(
        Lead.is_deleted.is_(False),
        Lead.contract_amount_fen > 0,
    )
    if owner_id:
        lead_stmt = lead_stmt.where(Lead.owner_id == owner_id)
    leads = (await db.execute(lead_stmt.order_by(Lead.sign_date.desc()))).scalars().all()

    contracted = sum(lead.contract_amount_fen or 0 for lead in leads)
    paid = sum(lead.paid_amount_fen or 0 for lead in leads)

    unpaid_list = [
        {
            "lead_id": lead.id,
            "lead_name": lead.name,
            "owner_name": lead.owner_name,
            "contract_amount_fen": lead.contract_amount_fen or 0,
            "paid_amount_fen": lead.paid_amount_fen or 0,
            "unpaid_amount_fen": lead.unpaid_amount_fen,
            "sign_date": lead.sign_date.isoformat() if lead.sign_date else None,
            "status": lead.status,
            "status_label": dicts.label_of(dicts.LEAD_STATUS_LABELS, lead.status),
        }
        for lead in leads
        if lead.unpaid_amount_fen > 0
    ]

    paid_stmt = select(func.coalesce(func.sum(PaymentRecord.amount_fen), 0), func.count())
    if start:
        paid_stmt = paid_stmt.where(PaymentRecord.paid_date >= start)
    if end:
        paid_stmt = paid_stmt.where(PaymentRecord.paid_date <= end)
    if owner_id:
        paid_stmt = paid_stmt.where(
            PaymentRecord.lead_id.in_(select(Lead.id).where(Lead.owner_id == owner_id))
        )
    period_paid, period_count = (await db.execute(paid_stmt)).one()

    return {
        "contracted_fen": contracted,
        "paid_fen": paid,
        "unpaid_fen": max(contracted - paid, 0),
        "collection_rate": round(paid / contracted, 4) if contracted else 0.0,
        "period_paid_fen": int(period_paid or 0),
        "period_count": int(period_count or 0),
        "unpaid_list": unpaid_list,
        "signed_count": len(leads),
    }


def payment_out(record: PaymentRecord) -> dict:
    return {
        "id": record.id,
        "lead_id": record.lead_id,
        "lead_name": record.lead_name,
        "amount_fen": record.amount_fen,
        "paid_date": record.paid_date.isoformat() if record.paid_date else None,
        "method": record.method,
        "method_label": dicts.label_of(dicts.PAYMENT_METHODS, record.method),
        "period_no": record.period_no,
        "period_label": dicts.label_of(dicts.PAYMENT_PERIODS, record.period_no),
        "remark": record.remark,
        "operator_name": record.operator_name,
        "created_at": record.created_at.isoformat() if record.created_at else None,
    }
