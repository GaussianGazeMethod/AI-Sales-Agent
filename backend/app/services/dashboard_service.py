"""看板服务：首页工作台（0.2，按角色动态）+ 线索看板（4.2）。"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import dicts
from app.core.deps import can_view_all_leads, is_manager
from app.models.activity import Reminder
from app.models.ai import AiDraft
from app.models.enums import LeadStatus, RiskStatus, UserRole
from app.models.lead import Lead
from app.models.stage import RiskAlert
from app.models.user import User
from app.services import assign_service, lead_service, target_service


def _stage_funnel(rows: list[tuple[str, int, int]]) -> list[dict]:
    """把 (环节, 条数, 金额) 聚合成完整漏斗（缺失环节补 0）。"""
    by_stage = {r[0]: (r[1], r[2]) for r in rows}
    result = []
    for stage in dicts.STAGE_ORDER:
        count, amount = by_stage.get(stage, (0, 0))
        result.append(
            {
                "stage": stage,
                "label": dicts.label_of(dicts.STAGE_LABELS, stage),
                "count": count,
                "amount_fen": amount,
                "weighted_amount_fen": int(round(amount * dicts.win_probability_of(stage))),
            }
        )
    return result


async def _funnel_rows(db: AsyncSession, owner_id: str | None) -> list[tuple[str, int, int]]:
    stmt = (
        select(
            Lead.current_stage,
            func.count(),
            func.coalesce(func.sum(Lead.budget_amount_fen), 0),
        )
        .where(
            Lead.is_deleted.is_(False),
            Lead.current_stage.is_not(None),
            # 丢单/无效/已签约不参与在途漏斗
            Lead.status.in_([LeadStatus.VALID, LeadStatus.FOLLOWING]),
        )
        .group_by(Lead.current_stage)
    )
    if owner_id:
        stmt = stmt.where(Lead.owner_id == owner_id)
    return [tuple(r) for r in (await db.execute(stmt)).all()]


async def _stuck_items(db: AsyncSession, owner_id: str | None, limit: int = 20) -> list[Lead]:
    """卡单线索：当前环节停留已超基准天数（已签约的不算卡单）。"""
    stmt = select(Lead).where(
        Lead.is_deleted.is_(False),
        Lead.current_stage.is_not(None),
        Lead.stage_entered_at.is_not(None),
        Lead.status.in_([LeadStatus.VALID, LeadStatus.FOLLOWING]),
    )
    if owner_id:
        stmt = stmt.where(Lead.owner_id == owner_id)
    rows = (await db.execute(stmt.order_by(Lead.stage_entered_at.asc()).limit(200))).scalars().all()

    now = datetime.now()
    stuck: list[Lead] = []
    for lead in rows:
        stay = (now - lead.stage_entered_at).days
        if dicts.duration_of(lead.current_stage) and stay > dicts.duration_of(lead.current_stage):
            stuck.append(lead)
        if len(stuck) >= limit:
            break
    return stuck


async def workbench(db: AsyncSession, *, user: User) -> dict:
    """首页工作台：按角色返回区块数据。"""
    now = datetime.now()
    period = target_service.current_period()

    todo_items = (
        (
            await db.execute(
                select(Reminder)
                .where(Reminder.receiver_id == user.id, Reminder.status == "pending")
                .order_by(Reminder.remind_at.asc())
                .limit(10)
            )
        )
        .scalars()
        .all()
    )
    todo_count = int(
        (
            await db.execute(
                select(func.count())
                .select_from(Reminder)
                .where(Reminder.receiver_id == user.id, Reminder.status == "pending")
            )
        ).scalar()
        or 0
    )

    pending_drafts = int(
        (
            await db.execute(
                select(func.count())
                .select_from(AiDraft)
                .where(AiDraft.created_by == user.id, AiDraft.status == "pending")
            )
        ).scalar()
        or 0
    )

    # 个人视角
    mine_funnel = _stage_funnel(await _funnel_rows(db, user.id))
    unassigned = await assign_service.unassigned_count(db)
    risk_rows = (
        (
            await db.execute(
                select(RiskAlert)
                .where(RiskAlert.status != RiskStatus.RESOLVED)
                .order_by(RiskAlert.created_at.desc())
                .limit(10)
            )
        )
        .scalars()
        .all()
    )

    payload: dict = {
        "role": user.role,
        "role_label": dicts.label_of(dicts.ROLE_LABELS, user.role),
        "todo_count": todo_count,
        "todo_items": todo_items,
        "funnel": mine_funnel,
        "stuck_items": await _stuck_items(db, user.id),
        "pending_drafts": pending_drafts,
        "my_achievement": await target_service.my_achievement(db, user=user, period=period),
        "unassigned_count": unassigned,
        "risk_items": risk_rows,
        "period": period,
        "rhythm_window": dicts.current_rhythm_window(),
        "team_overview": None,
    }

    # 管理人员 / 管理层：团队概览
    if is_manager(user):
        team_rows = await _funnel_rows(db, None)
        team_funnel = _stage_funnel(team_rows)
        month_start = datetime.combine(date.today().replace(day=1), time.min)
        new_valid = int(
            (
                await db.execute(
                    select(func.count())
                    .select_from(Lead)
                    .where(
                        Lead.is_deleted.is_(False),
                        Lead.valid_at.is_not(None),
                        Lead.valid_at >= month_start,
                    )
                )
            ).scalar()
            or 0
        )
        total_valid = int(
            (
                await db.execute(
                    select(func.count())
                    .select_from(Lead)
                    .where(
                        Lead.is_deleted.is_(False),
                        Lead.status.in_([LeadStatus.VALID, LeadStatus.SIGNED]),
                    )
                )
            ).scalar()
            or 0
        )
        signed_amount = int(
            (
                await db.execute(
                    select(func.coalesce(func.sum(Lead.contract_amount_fen), 0)).where(
                        Lead.is_deleted.is_(False),
                        Lead.sign_date >= date.today().replace(day=1),
                    )
                )
            ).scalar()
            or 0
        )
        paid_amount = int(
            (
                await db.execute(
                    select(func.coalesce(func.sum(Lead.paid_amount_fen), 0)).where(
                        Lead.is_deleted.is_(False), Lead.sign_date.is_not(None)
                    )
                )
            ).scalar()
            or 0
        )
        overdue_leads = (
            (
                await db.execute(
                    select(Lead)
                    .where(
                        Lead.is_deleted.is_(False),
                        Lead.current_stage.is_(None),
                        Lead.owner_id.is_not(None),
                        Lead.first_follow_at.is_(None),
                        Lead.first_follow_deadline.is_not(None),
                        Lead.first_follow_deadline < now,
                    )
                    .order_by(Lead.first_follow_deadline.asc())
                    .limit(10)
                )
            )
            .scalars()
            .all()
        )

        payload["team_overview"] = {
            "new_valid_count": new_valid,
            "total_valid_count": total_valid,
            "weighted_amount_fen": sum(x["weighted_amount_fen"] for x in team_funnel),
            "signed_amount_fen": signed_amount,
            "paid_amount_fen": paid_amount,
            "stage_distribution": team_funnel,
            "stuck_items": await _stuck_items(db, None, limit=20),
            "overdue_first_follow": overdue_leads,
            "team_targets": await target_service.achievement(db, period=period),
        }

    return payload


async def lead_board(db: AsyncSession, *, user: User, start: date, end: date) -> dict:
    """线索看板：来源分布与有效率、状态漏斗、人均产出、跟进时效、趋势。"""
    start_dt = datetime.combine(start, time.min)
    end_dt = datetime.combine(end, time.max)

    # 趋势：按天新增与有效
    trend_rows = (
        await db.execute(
            select(func.date(Lead.created_at), func.count())
            .where(Lead.is_deleted.is_(False), Lead.created_at.between(start_dt, end_dt))
            .group_by(func.date(Lead.created_at))
        )
    ).all()
    valid_rows = (
        await db.execute(
            select(func.date(Lead.valid_at), func.count())
            .where(
                Lead.is_deleted.is_(False),
                Lead.valid_at.is_not(None),
                Lead.valid_at.between(start_dt, end_dt),
            )
            .group_by(func.date(Lead.valid_at))
        )
    ).all()
    valid_map = {str(r[0]): r[1] for r in valid_rows}
    trend = [
        {
            "date": str(r[0]),
            "new_count": r[1],
            "valid_count": valid_map.get(str(r[0]), 0),
        }
        for r in sorted(trend_rows, key=lambda x: str(x[0]))
    ]

    per_person = await lead_service.per_person_output(db, start_dt, end_dt)
    if not can_view_all_leads(user):
        per_person = [p for p in per_person if p["user_id"] == user.id]

    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "source_distribution": await lead_service.source_distribution(db, start_dt, end_dt),
        "status_funnel": await lead_service.status_funnel(db, start_dt, end_dt),
        "per_person": per_person,
        "follow_timeliness": await lead_service.follow_timeliness(db, start_dt, end_dt),
        "trend": trend,
    }


async def opportunity_list(
    db: AsyncSession, *, user: User, keyword: str | None = None, offset: int = 0, limit: int = 20
) -> tuple[list[Lead], int]:
    """商机列表（2.1）：只要已进入闭环二（有当前环节）的记录。"""
    stmt = select(Lead).where(Lead.is_deleted.is_(False), Lead.current_stage.is_not(None))
    if not can_view_all_leads(user):
        stmt = stmt.where(Lead.owner_id == user.id)
    if keyword:
        stmt = stmt.where(Lead.name.like(f"%{keyword.strip()}%"))
    total = int((await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar() or 0)
    rows = (
        (await db.execute(stmt.order_by(Lead.stage_entered_at.asc()).offset(offset).limit(limit)))
        .scalars()
        .all()
    )
    return list(rows), total


async def funnel_and_forecast(db: AsyncSession, *, user: User, owner_id: str | None = None) -> dict:
    """业绩漏斗 + 加权预测（2.3）：管理人员可看团队，专员看自己。"""
    scope = owner_id if (is_manager(user) and owner_id) else (None if is_manager(user) else user.id)
    funnel = _stage_funnel(await _funnel_rows(db, scope))
    return {
        "owner_scope": scope or "team",
        "funnel": funnel,
        "total_amount_fen": sum(x["amount_fen"] for x in funnel),
        "weighted_amount_fen": sum(x["weighted_amount_fen"] for x in funnel),
        "baseline": dicts.CONVERSION_BASELINE,
    }


async def risk_center(db: AsyncSession, *, user: User, days: int = 0) -> dict:
    """风险与卡单中心（2.4）：卡单 / 总时长 / 节奏窗口 + 持久化预警 + 转化率。"""
    from app.models.enums import RiskStatus
    from app.serializers import risk_out
    from app.services import risk_service

    scope_owner = None if can_view_all_leads(user) else user.id
    stuck = await _stuck_items(db, scope_owner, limit=100)

    since = datetime.now() - timedelta(
        days=dicts.TOTAL_DURATION_WARNING_DAYS * dicts.TOTAL_DURATION_WARNING_RATIO
    )
    long_running = list(
        (
            await db.execute(
                select(Lead).where(
                    Lead.is_deleted.is_(False),
                    Lead.valid_at.is_not(None),
                    Lead.status.in_([LeadStatus.VALID, LeadStatus.FOLLOWING]),
                    Lead.valid_at <= since,
                )
            )
        )
        .scalars()
        .all()
    )

    pending_alerts, alert_total = await risk_service.list_alerts(
        db, status=RiskStatus.PENDING, owner_id=scope_owner, offset=0, limit=50
    )
    conversion = await risk_service.conversion_analysis(db, owner_id=scope_owner)

    return {
        "stuck": stuck,
        "long_running": long_running,
        "rhythm_window": dicts.current_rhythm_window(),
        "total_duration_warning_days": dicts.TOTAL_DURATION_WARNING_DAYS,
        "alerts": [risk_out(a) for a in pending_alerts],
        "alert_total": alert_total,
        "counts": await risk_service.count_by_status(db, owner_id=scope_owner),
        "conversion": conversion,
    }


async def user_directory(db: AsyncSession, roles: list[str] | None = None) -> list[User]:
    stmt = select(User).where(User.is_active.is_(True))
    if roles:
        stmt = stmt.where(User.role.in_(roles))
    return list((await db.execute(stmt.order_by(User.name))).scalars().all())


async def market_staff_options(db: AsyncSession) -> list[dict]:
    rows = await user_directory(db, [UserRole.MARKET_STAFF, UserRole.MARKET_MANAGER])
    return [{"user_id": u.id, "name": u.name, "role": u.role} for u in rows]


async def alert_lead_names(db: AsyncSession, alerts: list[dict]) -> dict[str, str]:
    """给风险中心的预警补客户名（列表里只显示 UUID 不可用）。"""
    from app.services import risk_service

    return await risk_service.lead_names_for(db, [a.get("lead_id") for a in alerts])
