"""市场目标服务：按月按人设目标 + 达成计算（S1-7）。"""

from __future__ import annotations

from calendar import monthrange
from datetime import date, datetime, time

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.models.activity import MarketTarget
from app.models.enums import LeadStatus
from app.models.lead import Lead
from app.models.user import User
from app.schemas import TargetSet


def period_bounds(period: str) -> tuple[datetime, datetime, date, date]:
    """把 '2026-09' 转成 [起, 止) 的 datetime 与 date。"""
    year, month = (int(x) for x in period.split("-"))
    start_date = date(year, month, 1)
    last_day = monthrange(year, month)[1]
    end_date = date(year, month, last_day)
    return (
        datetime.combine(start_date, time.min),
        datetime.combine(end_date, time.max),
        start_date,
        end_date,
    )


def current_period() -> str:
    return datetime.now().strftime("%Y-%m")


async def actual_value(db: AsyncSession, *, user_id: str, metric: str, period: str) -> float:
    """计算某人在某月的目标实际值。"""
    start_dt, end_dt, start_d, end_d = period_bounds(period)

    if metric == "new_lead_count":
        stmt = (
            select(func.count())
            .select_from(Lead)
            .where(
                Lead.is_deleted.is_(False),
                Lead.created_at.between(start_dt, end_dt),
                (Lead.owner_id == user_id) | (Lead.creator_id == user_id),
            )
        )
        return float((await db.execute(stmt)).scalar() or 0)

    if metric == "valid_lead_count":
        stmt = (
            select(func.count())
            .select_from(Lead)
            .where(
                Lead.is_deleted.is_(False),
                Lead.owner_id == user_id,
                Lead.valid_at.is_not(None),
                Lead.valid_at.between(start_dt, end_dt),
            )
        )
        return float((await db.execute(stmt)).scalar() or 0)

    if metric == "valid_rate":
        new_count = await actual_value(db, user_id=user_id, metric="new_lead_count", period=period)
        valid_count = await actual_value(
            db, user_id=user_id, metric="valid_lead_count", period=period
        )
        return round(valid_count / new_count, 4) if new_count else 0.0

    if metric == "sign_amount":
        stmt = select(func.coalesce(func.sum(Lead.contract_amount_fen), 0)).where(
            Lead.is_deleted.is_(False),
            Lead.owner_id == user_id,
            Lead.sign_date.is_not(None),
            Lead.sign_date.between(start_d, end_d),
        )
        return float((await db.execute(stmt)).scalar() or 0)

    if metric == "paid_amount":
        stmt = select(func.coalesce(func.sum(Lead.paid_amount_fen), 0)).where(
            Lead.is_deleted.is_(False),
            Lead.owner_id == user_id,
            Lead.sign_date.is_not(None),
            Lead.sign_date.between(start_d, end_d),
        )
        return float((await db.execute(stmt)).scalar() or 0)

    return 0.0


async def set_target(db: AsyncSession, *, operator: User, payload: TargetSet) -> MarketTarget:
    """设定目标（同一人同月同指标覆盖式更新）。"""
    target_user = (
        await db.execute(select(User).where(User.id == payload.user_id))
    ).scalar_one_or_none()
    if target_user is None:
        raise NotFoundError("目标对象用户不存在")

    _, _, start_d, end_d = period_bounds(payload.period)
    existing = (
        await db.execute(
            select(MarketTarget).where(
                MarketTarget.period == payload.period,
                MarketTarget.user_id == payload.user_id,
                MarketTarget.metric == payload.metric,
            )
        )
    ).scalar_one_or_none()

    if existing:
        existing.target_value = payload.target_value
        existing.setter_id = operator.id
        existing.setter_name = operator.name
        existing.period_end = end_d
        return existing

    row = MarketTarget(
        period_type=payload.period_type,
        period=payload.period,
        user_id=payload.user_id,
        user_name=target_user.name,
        metric=payload.metric,
        target_value=payload.target_value,
        setter_id=operator.id,
        setter_name=operator.name,
        period_end=end_d,
    )
    db.add(row)
    await db.flush()
    return row


async def list_targets(
    db: AsyncSession, *, period: str | None = None, user_id: str | None = None
) -> list[tuple[MarketTarget, float]]:
    stmt = select(MarketTarget)
    if period:
        stmt = stmt.where(MarketTarget.period == period)
    if user_id:
        stmt = stmt.where(MarketTarget.user_id == user_id)
    rows = (
        (await db.execute(stmt.order_by(MarketTarget.period.desc(), MarketTarget.user_name)))
        .scalars()
        .all()
    )

    result: list[tuple[MarketTarget, float]] = []
    for row in rows:
        actual = await actual_value(db, user_id=row.user_id, metric=row.metric, period=row.period)
        result.append((row, actual))
    return result


async def achievement(
    db: AsyncSession, *, period: str, scope_user_id: str | None = None
) -> list[dict]:
    """目标达成看板：按人聚合各指标。"""
    targets = await list_targets(db, period=period, user_id=scope_user_id)
    agg: dict[str, dict] = {}
    for target, actual in targets:
        item = agg.setdefault(
            target.user_id,
            {"user_id": target.user_id, "user_name": target.user_name, "metrics": []},
        )
        rate = round(actual / target.target_value, 4) if target.target_value else 0.0
        item["metrics"].append(
            {
                "metric": target.metric,
                "target_value": target.target_value,
                "actual_value": actual,
                "achievement_rate": rate,
            }
        )
    return list(agg.values())


async def my_achievement(db: AsyncSession, *, user: User, period: str | None = None) -> list[dict]:
    period = period or current_period()
    rows = await list_targets(db, period=period, user_id=user.id)
    result = []
    for target, actual in rows:
        rate = round(actual / target.target_value, 4) if target.target_value else 0.0
        result.append(
            {
                "metric": target.metric,
                "target_value": target.target_value,
                "actual_value": actual,
                "achievement_rate": rate,
            }
        )
    return result


async def valid_lead_count_in_period(db: AsyncSession, *, user_id: str | None, period: str) -> int:
    start_dt, end_dt, _, _ = period_bounds(period)
    stmt = (
        select(func.count())
        .select_from(Lead)
        .where(
            Lead.is_deleted.is_(False),
            Lead.status.in_([LeadStatus.VALID, LeadStatus.SIGNED]),
            Lead.valid_at.is_not(None),
            Lead.valid_at.between(start_dt, end_dt),
        )
    )
    if user_id:
        stmt = stmt.where(Lead.owner_id == user_id)
    return int((await db.execute(stmt)).scalar() or 0)
