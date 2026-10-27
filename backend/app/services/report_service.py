"""P1-d 报告服务：个人/团队周报 + 周会视图（团队概览 + 逐人项目节奏）。

周报口径（spec/05 表 17）：自动汇总「新增有效商机 / 各环节商机数与金额 / 卡单数 /
转化率 vs 基准 / 加权预测」→ 自动生成正文（AI，可编辑）→ 专员补充问题与下周计划 → 提交。
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.requests import compose_weekly_body
from app.ai.runtime import MODEL_COMPOSE, assert_ai_ready, model_name
from app.core import dicts
from app.core.errors import AiUnavailableError, ConflictError, NotFoundError, PermissionDeniedError
from app.models.activity import MarketTarget
from app.models.ai import AiDiagnosis
from app.models.enums import LeadStatus, ReportGeneratedBy, UserRole
from app.models.lead import Lead
from app.models.report import PaymentRecord, WeeklyReport
from app.models.stage import StageCompletion
from app.models.user import User
from app.services import audit_service, risk_service, stage_service

logger = logging.getLogger(__name__)

# 周报正文的提示词已外置：backend/prompts/weekly_report_body.yaml（Agently 提示词契约）


# ══════════════ 周期工具 ══════════════


def week_bounds(period: str) -> tuple[date, date]:
    """'2026-W40' → (周一, 周日)。"""
    try:
        year_str, week_str = period.split("-W")
        year, week = int(year_str), int(week_str)
        monday = date.fromisocalendar(year, week, 1)
    except (ValueError, AttributeError) as exc:
        raise ConflictError(f"周期格式应为 2026-W40：{period}") from exc
    return monday, monday + timedelta(days=6)


def current_period(today: date | None = None) -> str:
    today = today or date.today()
    iso = today.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def period_label(period: str) -> str:
    start, end = week_bounds(period)
    return f"{period}（{start.isoformat()} ~ {end.isoformat()}）"


# ══════════════ 指标汇总 ══════════════


async def collect_metrics(db: AsyncSession, *, user_id: str | None, start: date, end: date) -> dict:
    """汇总某人（或全团队 user_id=None）在 [start, end] 的经营指标。"""
    start_dt = datetime.combine(start, time.min)
    end_dt = datetime.combine(end, time.max)

    def scope(stmt):
        return stmt.where(Lead.owner_id == user_id) if user_id else stmt

    # 新增有效商机 / 新增线索
    new_valid = int(
        (
            await db.execute(
                scope(
                    select(func.count())
                    .select_from(Lead)
                    .where(
                        Lead.is_deleted.is_(False),
                        Lead.valid_at.is_not(None),
                        Lead.valid_at.between(start_dt, end_dt),
                    )
                )
            )
        ).scalar()
        or 0
    )
    new_leads = int(
        (
            await db.execute(
                scope(
                    select(func.count())
                    .select_from(Lead)
                    .where(Lead.is_deleted.is_(False), Lead.created_at.between(start_dt, end_dt))
                )
            )
        ).scalar()
        or 0
    )

    # 各环节商机数与金额（当前在途）
    stage_rows = (
        await db.execute(
            scope(
                select(
                    Lead.current_stage,
                    func.count(),
                    func.coalesce(func.sum(Lead.budget_amount_fen), 0),
                ).where(
                    Lead.is_deleted.is_(False),
                    Lead.current_stage.is_not(None),
                    Lead.status.in_([LeadStatus.VALID, LeadStatus.FOLLOWING]),
                )
            ).group_by(Lead.current_stage)
        )
    ).all()
    by_stage = {r[0]: (r[1], int(r[2])) for r in stage_rows}
    stage_distribution = [
        {
            "stage": stage,
            "label": dicts.label_of(dicts.STAGE_LABELS, stage),
            "count": by_stage.get(stage, (0, 0))[0],
            "amount_fen": by_stage.get(stage, (0, 0))[1],
            "weighted_amount_fen": int(
                by_stage.get(stage, (0, 0))[1] * dicts.win_probability_of(stage)
            ),
        }
        for stage in dicts.STAGE_ORDER
    ]
    weighted = sum(item["weighted_amount_fen"] for item in stage_distribution)

    # 卡单数与卡单商机
    stuck_leads = (
        (
            await db.execute(
                scope(
                    select(Lead).where(
                        Lead.is_deleted.is_(False),
                        Lead.current_stage.is_not(None),
                        Lead.stage_entered_at.is_not(None),
                        Lead.status.in_([LeadStatus.VALID, LeadStatus.FOLLOWING]),
                    )
                )
            )
        )
        .scalars()
        .all()
    )
    now = datetime.now()
    stuck = [
        lead
        for lead in stuck_leads
        if dicts.duration_of(lead.current_stage)
        and (now - lead.stage_entered_at).days > dicts.duration_of(lead.current_stage)
    ]

    # 签约 / 回款（本周）
    signed_amount = int(
        (
            await db.execute(
                scope(
                    select(func.coalesce(func.sum(Lead.contract_amount_fen), 0)).where(
                        Lead.is_deleted.is_(False),
                        Lead.sign_date.is_not(None),
                        Lead.sign_date.between(start, end),
                    )
                )
            )
        ).scalar()
        or 0
    )
    paid_amount = int(
        (
            await db.execute(
                select(func.coalesce(func.sum(PaymentRecord.amount_fen), 0)).where(
                    PaymentRecord.paid_date.between(start, end),
                    *(
                        [PaymentRecord.lead_id.in_(select(Lead.id).where(Lead.owner_id == user_id))]
                        if user_id
                        else []
                    ),
                )
            )
        ).scalar()
        or 0
    )

    # 本周推进的完成标准（含证据）
    advanced_rows = (
        await db.execute(
            select(StageCompletion, Lead.name)
            .join(Lead, Lead.id == StageCompletion.lead_id)
            .where(
                StageCompletion.is_done.is_(True),
                StageCompletion.done_at.is_not(None),
                StageCompletion.done_at.between(start_dt, end_dt),
                *([StageCompletion.done_by == user_id] if user_id else []),
            )
            .order_by(StageCompletion.done_at.desc())
            .limit(50)
        )
    ).all()
    advanced = [
        {
            "lead_id": c.lead_id,
            "lead_name": lead_name,
            "stage": c.stage,
            "stage_label": dicts.label_of(dicts.STAGE_LABELS, c.stage),
            "standard_content": c.standard_content,
            "evidence": c.evidence,
            "done_at": c.done_at.isoformat() if c.done_at else None,
            "done_by": c.done_by,
            "done_by_name": c.done_by_name,
        }
        for c, lead_name in advanced_rows
    ]

    conversion = await risk_service.conversion_analysis(db, start=start, end=end, owner_id=user_id)

    return {
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
        "new_valid_count": new_valid,
        "new_lead_count": new_leads,
        "stage_distribution": stage_distribution,
        "in_progress_count": sum(item["count"] for item in stage_distribution),
        "weighted_amount_fen": weighted,
        "total_amount_fen": sum(item["amount_fen"] for item in stage_distribution),
        "stuck_count": len(stuck),
        "stuck_items": [
            {
                "lead_id": lead.id,
                "lead_name": lead.name,
                "stage": lead.current_stage,
                "stage_label": dicts.label_of(dicts.STAGE_LABELS, lead.current_stage),
                "stay_days": (now - lead.stage_entered_at).days if lead.stage_entered_at else 0,
                "base_days": dicts.duration_of(lead.current_stage),
            }
            for lead in stuck
        ],
        "signed_amount_fen": signed_amount,
        "paid_amount_fen": paid_amount,
        "conversion": conversion["pairs"],
        "advanced_standards": advanced,
    }


async def _ai_body(metrics: dict, *, user_name: str) -> tuple[str, str, str | None]:
    """生成周报正文（Agently 节点 N4）。

     不做规则降级：AI 不可用直接抛 `AiUnavailableError`，
    不再用 `rule_body` 拼一段确定性文字冒充"模型写的正文"。
    """
    assert_ai_ready()
    try:
        content = await compose_weekly_body(
            {
                "user_name": user_name,
                "period": str(metrics.get("period") or ""),
                "facts": metrics,
                "tone_guide": "真实、具体、不夸大；数字必须与 facts 一致；不写口号与空话",
            }
        )
    except AiUnavailableError:
        raise
    except Exception as exc:  # noqa: BLE001 - AI 层异常统一归为"能力不可用"
        raise AiUnavailableError(f"周报正文生成失败：{exc}") from exc

    if not content.strip():
        raise AiUnavailableError("周报正文生成失败：模型返回空正文")
    return content.strip(), ReportGeneratedBy.AI, model_name(MODEL_COMPOSE)


# ══════════════ 周报 CRUD ══════════════


async def _get_or_create_report(
    db: AsyncSession, *, period: str, target: User
) -> tuple[WeeklyReport, date, date]:
    """取（或建）某人的某期周报；已提交的不可再动。"""
    start, end = week_bounds(period)
    report = (
        await db.execute(
            select(WeeklyReport).where(
                WeeklyReport.period == period, WeeklyReport.user_id == target.id
            )
        )
    ).scalar_one_or_none()
    if report and report.is_submitted:
        raise ConflictError("该周报已提交，如需修改请先撤回（P2 提供）")
    if report is None:
        report = WeeklyReport(period=period, period_start=start, period_end=end, user_id=target.id)
        db.add(report)
    report.user_name = target.name
    return report, start, end


async def _resolve_target(db: AsyncSession, *, operator: User, user_id: str | None) -> User:
    target_id = user_id or operator.id
    target = (await db.execute(select(User).where(User.id == target_id))).scalar_one_or_none()
    if target is None:
        raise NotFoundError("周报对象用户不存在")
    return target


async def collect(
    db: AsyncSession, *, operator: User, period: str | None = None, user_id: str | None = None
) -> WeeklyReport:
    """**只汇总指标**（宿主能力，永不依赖模型）。

    与"生成正文"拆开的原因：指标是确定性产物，不该被模型的可用性绑架。
    正文与 `generated_by` 保持原样（没生成过就是 `pending`）。
    """
    period = period or current_period()
    target = await _resolve_target(db, operator=operator, user_id=user_id)
    report, start, end = await _get_or_create_report(db, period=period, target=target)

    report.metrics = await collect_metrics(db, user_id=target.id, start=start, end=end)
    if not report.body:
        # 从来没生成过正文 → 明确标记为"待生成"，而不是谎称 ai/rule
        report.generated_by = ReportGeneratedBy.PENDING
    await db.flush()
    return report


async def generate_body(
    db: AsyncSession, *, operator: User, report: WeeklyReport, force: bool = False
) -> WeeklyReport:
    """**只生成正文**（模型能力，AI 不可用即 503，不影响已汇总的指标）。

    人工写过的正文默认不覆盖：需要显式 `force=True`（接口层要求前端二次确认）。
    """
    if report.is_submitted:
        raise ConflictError("该周报已提交，如需修改请先撤回（P2 提供）")
    if report.body and report.generated_by == ReportGeneratedBy.MANUAL and not force:
        raise ConflictError("正文已被人工修改；如需用 AI 重写请显式确认（force=true）")
    if not report.metrics:
        raise ConflictError("尚未汇总指标，请先执行「汇总数据」")

    body, generated_by, model_used = await _ai_body(report.metrics, user_name=report.user_name)
    report.body = body
    report.generated_by = generated_by
    report.model_used = model_used
    await db.flush()

    await audit_service.write_log(
        db,
        operator=operator,
        action="weekly_report_body",
        target_type="weekly_report",
        target_id=report.id,
        target_name=f"{report.user_name} {report.period}",
        detail=f"AI 生成周报正文（模型 {model_used}）",
    )
    return report


async def generate(
    db: AsyncSession, *, operator: User, period: str | None = None, user_id: str | None = None
) -> tuple[WeeklyReport, str | None]:
    """组合操作：先汇总指标，再尽力生成 AI 正文。

    返回 `(report, ai_body_error)`：
    - 指标**总是**会被汇总（这是宿主能力，不因模型故障失败）
    - 正文失败时 `ai_body_error` 带明确原因（接口层原样返回给前端展示），
      **正文留空，绝不伪造**
    """
    report = await collect(db, operator=operator, period=period, user_id=user_id)

    # 人工写过的正文不覆盖，且不浪费一次模型调用
    if report.body and report.generated_by == ReportGeneratedBy.MANUAL:
        return report, None

    try:
        await generate_body(db, operator=operator, report=report)
    except AiUnavailableError as exc:
        logger.warning("周报正文生成失败（指标已汇总）：%s", exc.message)
        return report, exc.message
    return report, None


async def list_reports(
    db: AsyncSession,
    *,
    period: str | None = None,
    user_id: str | None = None,
    offset: int = 0,
    limit: int = 20,
) -> tuple[list[WeeklyReport], int]:
    stmt = select(WeeklyReport)
    if period:
        stmt = stmt.where(WeeklyReport.period == period)
    if user_id:
        stmt = stmt.where(WeeklyReport.user_id == user_id)
    total = int((await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar() or 0)
    rows = (
        (
            await db.execute(
                stmt.order_by(WeeklyReport.period.desc(), WeeklyReport.user_name)
                .offset(offset)
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return list(rows), total


async def get_report(db: AsyncSession, report_id: str) -> WeeklyReport:
    report = (
        await db.execute(select(WeeklyReport).where(WeeklyReport.id == report_id))
    ).scalar_one_or_none()
    if report is None:
        raise NotFoundError("周报不存在")
    return report


async def update_report(
    db: AsyncSession,
    *,
    operator: User,
    report: WeeklyReport,
    body: str | None = None,
    problems: str | None = None,
    next_plan: str | None = None,
) -> WeeklyReport:
    if report.is_submitted:
        raise ConflictError("周报已提交，不能再编辑")
    if body is not None and body.strip():
        report.body = body
        report.generated_by = ReportGeneratedBy.MANUAL
    if problems is not None:
        report.problems = problems
    if next_plan is not None:
        report.next_plan = next_plan
    return report


async def submit_report(db: AsyncSession, *, operator: User, report: WeeklyReport) -> WeeklyReport:
    if report.is_submitted:
        raise ConflictError("周报已提交")
    if (
        operator.role not in {UserRole.ADMIN, UserRole.MARKET_MANAGER}
        and report.user_id != operator.id
    ):
        raise PermissionDeniedError("只能提交本人的周报")
    report.is_submitted = True
    report.submitted_at = datetime.now()
    return report


def report_out(report: WeeklyReport) -> dict:
    return {
        "id": report.id,
        "period": report.period,
        "period_label": period_label(report.period),
        "period_start": report.period_start.isoformat(),
        "period_end": report.period_end.isoformat(),
        "user_id": report.user_id,
        "user_name": report.user_name,
        "metrics": report.metrics or {},
        "body": report.body,
        "problems": report.problems,
        "next_plan": report.next_plan,
        "is_submitted": report.is_submitted,
        "submitted_at": report.submitted_at.isoformat() if report.submitted_at else None,
        "generated_by": report.generated_by,
        "model_used": report.model_used,
        "created_at": report.created_at.isoformat() if report.created_at else None,
        "updated_at": report.updated_at.isoformat() if report.updated_at else None,
    }


# ══════════════ 周会视图（SX-2 / 页面 4.5）══════════════


async def weekly_meeting(
    db: AsyncSession, *, start: date | None = None, end: date | None = None
) -> dict:
    """三层：团队概览 → 逐人项目节奏 → 待评审清单。"""
    today = date.today()
    if end is None:
        end = today
    if start is None:
        start = end - timedelta(days=end.weekday())  # 本周一
    now = datetime.now()

    # ── 第一层：团队概览 ──
    team_metrics = await collect_metrics(db, user_id=None, start=start, end=end)

    overdue_first_follow = (
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
                .limit(20)
            )
        )
        .scalars()
        .all()
    )

    team_targets = (
        (
            await db.execute(
                select(MarketTarget).where(MarketTarget.period == today.strftime("%Y-%m"))
            )
        )
        .scalars()
        .all()
    )
    from app.services import target_service

    team_target_items = []
    for target in team_targets:
        actual = await target_service.actual_value(
            db, user_id=target.user_id, metric=target.metric, period=target.period
        )
        team_target_items.append(
            {
                "user_id": target.user_id,
                "user_name": target.user_name,
                "metric": target.metric,
                "target_value": target.target_value,
                "actual_value": actual,
                "achievement_rate": round(actual / target.target_value, 4)
                if target.target_value
                else 0.0,
            }
        )

    # ── 第二层：逐人项目节奏 ──
    staff = (
        (
            await db.execute(
                select(User)
                .where(
                    User.role.in_([UserRole.MARKET_STAFF, UserRole.MARKET_MANAGER]),
                    User.is_active.is_(True),
                )
                .order_by(User.name)
            )
        )
        .scalars()
        .all()
    )

    per_person: list[dict] = []
    for person in staff:
        leads = (
            (
                await db.execute(
                    select(Lead).where(
                        Lead.is_deleted.is_(False),
                        Lead.owner_id == person.id,
                        Lead.status.in_(
                            [LeadStatus.VALID, LeadStatus.FOLLOWING, LeadStatus.SIGNED]
                        ),
                    )
                )
            )
            .scalars()
            .all()
        )

        stage_agg: dict[str, dict] = {}
        stuck_list: list[dict] = []
        weighted = 0
        for lead in leads:
            weighted += lead.weighted_amount_fen
            stage = lead.current_stage
            if stage:
                item = stage_agg.setdefault(
                    stage,
                    {
                        "stage": stage,
                        "label": dicts.label_of(dicts.STAGE_LABELS, stage),
                        "count": 0,
                        "amount_fen": 0,
                        "stay_days_total": 0,
                        "overdue_count": 0,
                    },
                )
                stay = (now - lead.stage_entered_at).days if lead.stage_entered_at else 0
                item["count"] += 1
                item["amount_fen"] += lead.budget_amount_fen or 0
                item["stay_days_total"] += stay
                base = dicts.duration_of(stage)
                if base and stay > base:
                    item["overdue_count"] += 1
                    progress = await stage_service.stage_progress(db, lead)
                    stuck_list.append(
                        {
                            "lead_id": lead.id,
                            "lead_name": lead.name,
                            "stage": stage,
                            "stage_label": dicts.label_of(dicts.STAGE_LABELS, stage),
                            "stay_days": stay,
                            "base_days": base,
                            "missing_hard_gates": progress["missing_hard_gates"],
                            "done_count": progress["done_count"],
                            "total_count": progress["total_count"],
                        }
                    )

        stages = []
        for stage in dicts.STAGE_ORDER:
            item = stage_agg.get(stage)
            if not item:
                continue
            item["stay_days_avg"] = round(item.pop("stay_days_total") / item["count"], 1)
            stages.append(item)

        # 本周推进的标准（含证据）
        advanced = [
            a for a in team_metrics["advanced_standards"] if a["done_by_name"] == person.name
        ]

        # 最近一次 AI 诊断摘要（取该人前 5 个商机的最新一条）
        lead_ids = [lead.id for lead in leads][:20]
        suggestions: list[dict] = []
        if lead_ids:
            rows = (
                (
                    await db.execute(
                        select(AiDiagnosis)
                        .where(AiDiagnosis.lead_id.in_(lead_ids))
                        .order_by(AiDiagnosis.created_at.desc())
                        .limit(20)
                    )
                )
                .scalars()
                .all()
            )
            seen: set[str] = set()
            for diag in rows:
                if diag.lead_id in seen:
                    continue
                seen.add(diag.lead_id)
                suggestions.append(
                    {
                        "lead_id": diag.lead_id,
                        "lead_name": diag.lead_name,
                        "stage_label": dicts.label_of(dicts.STAGE_LABELS, diag.stage),
                        "content": (diag.content or "")[:400],
                        "created_at": diag.created_at.isoformat() if diag.created_at else None,
                    }
                )
                if len(suggestions) >= 3:
                    break

        per_person.append(
            {
                "user_id": person.id,
                "user_name": person.name,
                "total_count": len(leads),
                "weighted_amount_fen": weighted,
                "stages": stages,
                "advanced_standards": advanced,
                "stuck": stuck_list,
                "ai_suggestions": suggestions,
            }
        )

    # ── 第三层：待评审清单 ──
    from app.services import review_service

    pending_reviews = await review_service.pending_review_leads(db)

    return {
        "period": {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "label": f"{start.isoformat()} ~ {end.isoformat()}",
        },
        "team_overview": {
            **{k: v for k, v in team_metrics.items() if k != "advanced_standards"},
            "overdue_first_follow": overdue_first_follow,
            "team_targets": team_target_items,
            "risk_counts": await risk_service.count_by_status(db),
        },
        "per_person": per_person,
        "pending_reviews": pending_reviews,
    }
