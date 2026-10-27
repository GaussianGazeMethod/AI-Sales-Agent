"""4.1 经营大盘：跨三闭环的一次性汇总。

页面定位：管理层（admin / executive）看"公司整体经营现状"，
所以这里只做**读**与**聚合**，不引入新口径：
- 线索域：新增 / 有效率 / 来源分布 / 跟进时效 → 复用 lead_board 的口径
- 商机域：7 环节金额与数量 / 加权预测 / 卡单 → 复用 funnel 与 risk_center
- 成交域：签约与回款 → 复用 payments 汇总
- 交付域：在执行合同 / 10 阶段进度 / 验收与续费临近 → 复用 delivery 与 acceptance/renewal
- 运营域：活跃率 / 未闭环台账与投诉 → 复用 operation 与 issue

 口径必须与各自页面一致：这里**调用同一批服务函数**，不重写一遍 SQL，
否则大盘和明细页会出现"两个数字"。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import dicts
from app.core.workday import count_working_days
from app.models.delivery import Contract
from app.models.enums import (
    ComplaintStatus,
    ContractStatus,
    IssueStatus,
    LeadStatus,
)
from app.models.lead import Lead
from app.models.operation import Complaint, OperationMetric, ProductIssue
from app.models.report import PaymentRecord
from app.models.stage import RiskAlert, StageCompletion
from app.models.user import User


def _fen_to_wan(fen: int | None) -> float:
    return round((fen or 0) / 100 / 10000, 2)


async def _working_days_left_in_year(db: AsyncSession, today: date) -> int | None:
    """距年末还剩多少个工作日（按库里配置的节假日/调休算，与管理层关心的"还剩多少天"一致）。"""
    from app.services import calendar_service

    holidays, makeups = await calendar_service.load_calendar(db)
    return count_working_days(
        datetime.combine(today, datetime.min.time()),
        datetime.combine(date(today.year, 12, 31), datetime.max.time()),
        holidays,
        makeups,
    )


async def _scalar(db: AsyncSession, stmt) -> int:
    return int((await db.execute(stmt)).scalar() or 0)


async def overview(db: AsyncSession, *, user: User, days: int = 30) -> dict:
    """经营大盘：整体 + 四个域 + 待办清单。

     与 2.3 / 2.4 复用同一批服务函数（含同一个 user），
    这样大盘数字与明细页永远一致；不在这里重写一遍 SQL 口径。
    """
    from app.services import acceptance_service, dashboard_service, issue_service

    today = date.today()
    start = today - timedelta(days=days)
    week_ago = today - timedelta(days=7)

    # ── 线索域 ──
    lead_total = await _scalar(
        db, select(func.count()).select_from(Lead).where(Lead.is_deleted.is_(False))
    )
    new_leads = await _scalar(
        db,
        select(func.count())
        .select_from(Lead)
        .where(Lead.is_deleted.is_(False), Lead.created_at >= start),
    )
    new_leads_week = await _scalar(
        db,
        select(func.count())
        .select_from(Lead)
        .where(Lead.is_deleted.is_(False), Lead.created_at >= week_ago),
    )
    invalid = await _scalar(
        db,
        select(func.count())
        .select_from(Lead)
        .where(Lead.is_deleted.is_(False), Lead.status == LeadStatus.INVALID),
    )
    source_rows = (
        await db.execute(
            select(Lead.source, func.count())
            .where(Lead.is_deleted.is_(False))
            .group_by(Lead.source)
            .order_by(func.count().desc())
            .limit(8)
        )
    ).all()
    valid_rate = round((lead_total - invalid) / lead_total, 4) if lead_total else 0.0

    # ── 商机域（复用 2.3 / 2.4 的服务函数，口径完全一致）──
    from app.serializers import lead_out

    funnel_result = await dashboard_service.funnel_and_forecast(db, user=user)
    risk_center = await dashboard_service.risk_center(db, user=user)
    # 卡单/长周期是 ORM 对象；与 2.4 页面一样用 lead_out 序列化，保证字段一致
    stuck_items = [lead_out(x) for x in risk_center.get("stuck") or []]
    long_running_items = [lead_out(x) for x in risk_center.get("long_running") or []]
    funnel = funnel_result["funnel"]
    funnel_total_amount = funnel_result["total_amount_fen"]
    funnel_weighted = funnel_result["weighted_amount_fen"]

    # ── 成交域（回款复用 payments 汇总）──
    # 回款记录表没有状态字段：全部记录都是已确认回款（与 2.6 汇总口径一致）
    paid_total = await _scalar(db, select(func.coalesce(func.sum(PaymentRecord.amount_fen), 0)))
    signed_total = await _scalar(
        db,
        select(func.coalesce(func.sum(Lead.contract_amount_fen), 0)).where(
            Lead.is_deleted.is_(False), Lead.status == LeadStatus.SIGNED
        ),
    )

    # ── 交付域 ──
    contracts = list(
        (await db.execute(select(Contract).where(Contract.status == ContractStatus.EXECUTING)))
        .scalars()
        .all()
    )
    phase_counts: dict[int, int] = {}
    for contract in contracts:
        phase_counts[contract.current_phase] = phase_counts.get(contract.current_phase, 0) + 1
    acceptance_due = await acceptance_service.acceptance_due_list(db)
    renewal_due = await acceptance_service.renewal_due_list(db)
    renewal_stats = await acceptance_service.renewal_stats(db)

    # ── 运营域 ──
    latest_metrics = list(
        (
            await db.execute(
                select(OperationMetric).order_by(OperationMetric.period.desc()).limit(200)
            )
        )
        .scalars()
        .all()
    )
    total_accounts = sum(m.total_accounts for m in latest_metrics)
    total_login = sum(m.login_count for m in latest_metrics)
    from app.services import operation_service

    open_issues = await _scalar(
        db,
        select(func.count())
        .select_from(ProductIssue)
        .where(ProductIssue.status.in_([IssueStatus.PENDING, IssueStatus.PROCESSING])),
    )
    renewal_risk_issues = await _scalar(
        db,
        select(func.count())
        .select_from(ProductIssue)
        .where(
            ProductIssue.is_renewal_risk.is_(True),
            ProductIssue.status.in_([IssueStatus.PENDING, IssueStatus.PROCESSING]),
        ),
    )
    open_complaints = await _scalar(
        db,
        select(func.count())
        .select_from(Complaint)
        .where(Complaint.status.in_([ComplaintStatus.PENDING, ComplaintStatus.PROCESSING])),
    )
    issue_stats = await issue_service.issue_stats(db)

    # ── 风险与待办 ──
    pending_alerts = await _scalar(
        db, select(func.count()).select_from(RiskAlert).where(RiskAlert.status == "pending")
    )
    delivery_alerts = await _scalar(
        db,
        select(func.count())
        .select_from(RiskAlert)
        .where(RiskAlert.domain == "delivery", RiskAlert.status == "pending"),
    )
    sales_alerts = await _scalar(
        db,
        select(func.count())
        .select_from(RiskAlert)
        .where(RiskAlert.domain != "delivery", RiskAlert.status == "pending"),
    )
    week_advanced = await _scalar(
        db,
        select(func.count())
        .select_from(StageCompletion)
        .where(StageCompletion.is_done.is_(True), StageCompletion.done_at >= week_ago),
    )

    # ── 待办清单（管理层最该先看的几件事）──
    todos: list[dict] = []
    for item in acceptance_due[:5]:
        todos.append(
            {
                "kind": "acceptance_due",
                "kind_label": "验收临近",
                "level": "warning" if item["urgent"] else "info",
                "subject": item["customer_name"],
                "detail": (
                    f"服务期 {item['service_end']} 到期，剩余 {item['workdays_left']} 个工作日"
                ),
                "owner": item["delivery_owner_name"],
                "route": "/acceptances",
            }
        )
    for item in renewal_due[:5]:
        todos.append(
            {
                "kind": "renewal_due",
                "kind_label": "续费到期",
                "level": "warning" if item["urgent"] else "info",
                "subject": item["customer_name"],
                "detail": (
                    f"{item['days_left']} 天后到期，意愿：{item['willingness_label'] or '未评估'}"
                ),
                "owner": item["owner_name"],
                "route": "/renewals",
            }
        )
    for item in stuck_items[:5]:
        # 字段名与 /dashboard/risk-center 的 lead_out 一致（不是 lead_name/stay_days）
        todos.append(
            {
                "kind": "stuck",
                "kind_label": "商机卡单",
                "level": "error",
                "subject": item.get("name"),
                "detail": (
                    f"{item.get('current_stage_label') or '未进入环节'} "
                    f"停留 {item.get('stage_stay_days')} 天"
                    f"（基准 {item.get('stage_base_days')} 天）"
                ),
                "owner": item.get("owner_name"),
                "lead_id": item.get("id"),
                "route": "/risks",
            }
        )
    for item in (issue_stats.get("overdue_items") or [])[:5]:
        todos.append(
            {
                "kind": "issue_overdue",
                "kind_label": "台账久拖",
                "level": "error",
                "subject": item.get("customer_name"),
                "detail": str(item.get("content") or "")[:60],
                "lead_id": item.get("lead_id"),
                "owner": item.get("assignee_name") or item.get("registrar_name"),
                "route": "/issues",
            }
        )
    todos.sort(key=lambda x: {"error": 0, "warning": 1, "info": 2}.get(x["level"], 3))

    return {
        "generated_at": datetime.now().isoformat(),
        "window_days": days,
        "window_start": start.isoformat(),
        "headline": {
            "lead_total": lead_total,
            "new_leads": new_leads,
            "new_leads_week": new_leads_week,
            "valid_rate": valid_rate,
            "in_progress_count": sum(x["count"] for x in funnel),
            "total_amount_fen": funnel_total_amount,
            "total_amount_wan": _fen_to_wan(funnel_total_amount),
            "weighted_amount_fen": funnel_weighted,
            "weighted_amount_wan": _fen_to_wan(funnel_weighted),
            "signed_amount_fen": signed_total,
            "signed_amount_wan": _fen_to_wan(signed_total),
            "paid_amount_fen": paid_total,
            "paid_amount_wan": _fen_to_wan(paid_total),
            "paid_rate": round(paid_total / signed_total, 4) if signed_total else 0.0,
            "executing_contracts": len(contracts),
            "stuck_count": len(stuck_items),
            "pending_alerts": pending_alerts,
            "avg_activity_rate": operation_service.calc_activity_rate(total_login, total_accounts),
        },
        "leads": {
            "total": lead_total,
            "new_in_window": new_leads,
            "new_week": new_leads_week,
            "invalid": invalid,
            "valid_rate": valid_rate,
            "by_source": [
                {
                    "source": source,
                    "label": dicts.label_of(dicts.LEAD_SOURCE_LABELS, source),
                    "count": int(count),
                }
                for source, count in source_rows
            ],
        },
        "opportunities": {
            "funnel": funnel,
            "total_amount_fen": funnel_total_amount,
            "weighted_amount_fen": funnel_weighted,
            "baseline": funnel_result.get("baseline"),
            "stuck": stuck_items,
            "long_running": long_running_items,
            "alert_total": risk_center.get("alert_total", 0),
            "conversion": risk_center.get("conversion"),
            "advanced_standards_week": week_advanced,
        },
        "revenue": {
            "signed_amount_fen": signed_total,
            "signed_amount_wan": _fen_to_wan(signed_total),
            "paid_amount_fen": paid_total,
            "paid_amount_wan": _fen_to_wan(paid_total),
            "paid_rate": round(paid_total / signed_total, 4) if signed_total else 0.0,
            "signed_count": await _scalar(
                db,
                select(func.count())
                .select_from(Lead)
                .where(Lead.is_deleted.is_(False), Lead.status == LeadStatus.SIGNED),
            ),
        },
        "delivery": {
            "executing_contracts": len(contracts),
            "by_phase": [
                {
                    "phase": phase,
                    "label": dicts.DELIVERY_PHASE_LABELS.get(phase, ""),
                    "count": count,
                }
                for phase, count in sorted(phase_counts.items())
            ],
            "acceptance_due": acceptance_due,
            "renewal_due": renewal_due,
            "renewal_stats": renewal_stats,
            "alert_count": delivery_alerts,
        },
        "operation": {
            "customer_count": len(latest_metrics),
            "total_accounts": total_accounts,
            "total_login": total_login,
            "avg_activity_rate": operation_service.calc_activity_rate(total_login, total_accounts),
            "open_issues": open_issues,
            "renewal_risk_issues": renewal_risk_issues,
            "open_complaints": open_complaints,
            "avg_resolve_hours": issue_stats.get("avg_resolve_hours"),
            "top_issue_customers": issue_stats.get("top_customers"),
        },
        "alerts": {
            "pending_total": pending_alerts,
            "sales": sales_alerts,
            "delivery": delivery_alerts,
        },
        "todos": todos,
        "todo_total": len(todos),
        "working_days_to_year_end": await _working_days_left_in_year(db, today),
        "note": "口径与各明细页一致（复用同一批服务函数）；点击待办可跳到对应页面处理。",
    }
