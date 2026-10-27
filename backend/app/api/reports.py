"""P1-d 接口：周报（4.4）、周会视图（4.5）、项目评审卡（2.7）、回款（2.6）。"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, require_lead_view, require_lead_write
from app.core.errors import PermissionDeniedError
from app.database import get_db
from app.models.enums import UserRole
from app.models.user import User
from app.schemas import (
    PaymentCreate,
    PaymentUpdate,
    ProjectReviewCreate,
    WeeklyReportBodyRequest,
    WeeklyReportGenerateRequest,
    WeeklyReportUpdateRequest,
)
from app.services import payment_service, report_service, review_service

router = APIRouter(tags=["P1-d · 周报 / 评审 / 回款"])


def _is_manager(user: User) -> bool:
    return user.role in {UserRole.ADMIN, UserRole.MARKET_MANAGER, UserRole.EXECUTIVE}


# ══════════════ 4.4 周报 ══════════════


@router.get("/reports/overview", summary="4.1 经营大盘（管理层）：跨三闭环汇总 + 待办清单")
async def business_overview(
    days: int = 30,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    """仅管理层与管理员可见。

    口径与各明细页一致：这里调用同一批服务函数（漏斗/风险中心/回款/验收/续费/运营），
    不重写 SQL，避免"大盘与明细两个数字"。
    """
    if user.role not in {UserRole.ADMIN, UserRole.EXECUTIVE}:
        raise PermissionDeniedError("只有管理层与管理员可以查看经营大盘")
    from app.services import overview_service

    return await overview_service.overview(db, user=user, days=max(1, min(days, 365)))


@router.get("/reports/weekly/current", summary="我的本周周报（没有则返回 null 并提示可生成）")
async def current_weekly(
    period: str | None = None,
    user: User = Depends(require_lead_view),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    period = period or report_service.current_period()
    reports, _ = await report_service.list_reports(db, period=period, user_id=user.id)
    return {
        "period": period,
        "period_label": report_service.period_label(period),
        "item": report_service.report_out(reports[0]) if reports else None,
    }


@router.get("/reports/weekly", summary="周报列表（个人 / 团队）")
async def list_weekly(
    period: str | None = None,
    user_id: str | None = None,
    page: int = 1,
    page_size: int = 20,
    user: User = Depends(require_lead_view),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    scope = user_id
    if not _is_manager(user):
        scope = user.id
    rows, total = await report_service.list_reports(
        db, period=period, user_id=scope, offset=(page - 1) * page_size, limit=page_size
    )
    return {
        "items": [report_service.report_out(r) for r in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/reports/weekly/team", summary="团队周报汇总（管理人员）")
async def team_weekly(
    period: str | None = None,
    user: User = Depends(require_lead_view),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    if not _is_manager(user):
        raise PermissionDeniedError("只有管理人员可以查看团队周报")
    period = period or report_service.current_period()
    rows, _ = await report_service.list_reports(db, period=period, limit=200)
    return {
        "period": period,
        "period_label": report_service.period_label(period),
        "items": [report_service.report_out(r) for r in rows],
        "submitted": sum(1 for r in rows if r.is_submitted),
        "total": len(rows),
    }


def _check_target(user: User, target_user_id: str | None) -> None:
    if target_user_id and target_user_id != user.id and not _is_manager(user):
        raise PermissionDeniedError("只能生成/汇总本人的周报")


@router.post("/reports/weekly/collect", summary="汇总周报指标（宿主能力，不依赖模型）")
async def collect_weekly(
    payload: WeeklyReportGenerateRequest,
    user: User = Depends(require_lead_view),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    """只汇总确定性指标：AI 不可用也能正常出数据表。"""
    _check_target(user, payload.user_id)
    report = await report_service.collect(
        db, operator=user, period=payload.period, user_id=payload.user_id
    )
    return {"item": report_service.report_out(report)}


@router.post(
    "/reports/weekly/{report_id}/generate-body",
    summary="AI 生成周报正文（模型能力，AI 不可用则 503）",
)
async def generate_weekly_body(
    report_id: str,
    payload: WeeklyReportBodyRequest,
    user: User = Depends(require_lead_view),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    report = await report_service.get_report(db, report_id)
    if report.user_id != user.id and not _is_manager(user):
        raise PermissionDeniedError("只能生成本人周报的正文")
    report = await report_service.generate_body(
        db, operator=user, report=report, force=payload.force
    )
    return {"item": report_service.report_out(report)}


@router.post("/reports/weekly/generate", summary="生成/刷新周报（汇总指标 + 尽力生成 AI 正文）")
async def generate_weekly(
    payload: WeeklyReportGenerateRequest,
    user: User = Depends(require_lead_view),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    """组合操作：指标总是汇总；正文失败时返回 `ai_body_error`（正文留空，不伪造）。"""
    _check_target(user, payload.user_id)
    report, ai_body_error = await report_service.generate(
        db, operator=user, period=payload.period, user_id=payload.user_id
    )
    return {"item": report_service.report_out(report), "ai_body_error": ai_body_error}


@router.patch("/reports/weekly/{report_id}", summary="编辑周报（正文/问题/下周计划）")
async def update_weekly(
    report_id: str,
    payload: WeeklyReportUpdateRequest,
    user: User = Depends(require_lead_view),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    report = await report_service.get_report(db, report_id)
    if report.user_id != user.id and not _is_manager(user):
        raise PermissionDeniedError("只能编辑本人的周报")
    await report_service.update_report(
        db,
        operator=user,
        report=report,
        body=payload.body,
        problems=payload.problems,
        next_plan=payload.next_plan,
    )
    return {"item": report_service.report_out(report)}


@router.post("/reports/weekly/{report_id}/submit", summary="提交周报")
async def submit_weekly(
    report_id: str,
    user: User = Depends(require_lead_view),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    report = await report_service.get_report(db, report_id)
    await report_service.submit_report(db, operator=user, report=report)
    return {"item": report_service.report_out(report)}


# ══════════════ 4.5 周会视图 ══════════════


@router.get("/dashboard/weekly-meeting", summary="周会视图：团队概览 + 逐人项目节奏 + 待评审")
async def weekly_meeting(
    start: date | None = None,
    end: date | None = None,
    user: User = Depends(require_lead_view),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    if not _is_manager(user):
        raise PermissionDeniedError("只有管理人员可以打开周会视图")
    data = await report_service.weekly_meeting(db, start=start, end=end)
    from app.serializers import lead_out

    data["team_overview"]["stuck_items"] = [
        {
            "lead_id": item["lead_id"],
            "lead_name": item["lead_name"],
            "stage_label": item["stage_label"],
            "stay_days": item["stay_days"],
            "base_days": item["base_days"],
        }
        for item in data["team_overview"].get("stuck_items", [])
    ]
    data["team_overview"]["overdue_first_follow"] = [
        lead_out(x) for x in data["team_overview"]["overdue_first_follow"]
    ]
    return data


# ══════════════ 2.7 项目评审卡 ══════════════


@router.post("/reviews", summary="填写项目评审卡（5 看 + 能力/态度判定）")
async def create_review(
    payload: ProjectReviewCreate,
    user: User = Depends(require_lead_write),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    if not _is_manager(user):
        raise PermissionDeniedError("只有市场部管理人员或管理员可以填写评审卡")
    review = await review_service.create_review(db, operator=user, payload=payload)
    return review_service.review_out(review)


@router.get("/reviews", summary="评审记录列表")
async def list_reviews(
    lead_id: str | None = None,
    reviewer_id: str | None = None,
    verdict: str | None = None,
    page: int = 1,
    page_size: int = 20,
    user: User = Depends(require_lead_view),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    rows, total = await review_service.list_reviews(
        db,
        lead_id=lead_id,
        reviewer_id=reviewer_id,
        verdict=verdict,
        offset=(page - 1) * page_size,
        limit=page_size,
    )
    return {
        "items": [review_service.review_out(r) for r in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/reviews/pending", summary="待评审清单")
async def pending_reviews(
    user: User = Depends(require_lead_view), db: AsyncSession = Depends(get_db, scope="function")
) -> dict:
    if not _is_manager(user):
        raise PermissionDeniedError("只有管理人员可以查看待评审清单")
    items = await review_service.pending_review_leads(db)
    return {"items": items, "total": len(items)}


@router.get("/reviews/{review_id}", summary="评审记录详情")
async def get_review(
    review_id: str,
    user: User = Depends(require_lead_view),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    review = await review_service.get_review(db, review_id)
    return review_service.review_out(review)


# ══════════════ 2.6 回款管理 ══════════════


@router.get("/payments", summary="回款记录列表")
async def list_payments(
    lead_id: str | None = None,
    owner_id: str | None = None,
    start: date | None = None,
    end: date | None = None,
    page: int = 1,
    page_size: int = 20,
    user: User = Depends(require_lead_view),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    scope = owner_id
    if not _is_manager(user):
        scope = user.id
    rows, total = await payment_service.list_payments(
        db,
        lead_id=lead_id,
        owner_id=scope,
        start=start,
        end=end,
        offset=(page - 1) * page_size,
        limit=page_size,
    )
    return {
        "items": [payment_service.payment_out(r) for r in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/payments/summary", summary="回款总览（合同额/已回款/未回款/未回款清单）")
async def payment_summary(
    start: date | None = None,
    end: date | None = None,
    owner_id: str | None = None,
    user: User = Depends(require_lead_view),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    scope = owner_id
    if not _is_manager(user):
        scope = user.id
    return await payment_service.summary(db, owner_id=scope, start=start, end=end)


@router.post("/payments", summary="登记回款")
async def create_payment(
    payload: PaymentCreate,
    user: User = Depends(require_lead_write),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    record = await payment_service.create_payment(db, operator=user, payload=payload)
    return payment_service.payment_out(record)


@router.patch("/payments/{payment_id}", summary="修改回款记录")
async def update_payment(
    payment_id: str,
    payload: PaymentUpdate,
    user: User = Depends(require_lead_write),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    record = await payment_service.get_payment(db, payment_id)
    await payment_service.update_payment(db, operator=user, record=record, payload=payload)
    return payment_service.payment_out(record)


@router.delete("/payments/{payment_id}", summary="删除回款记录")
async def delete_payment(
    payment_id: str,
    user: User = Depends(require_lead_write),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    record = await payment_service.get_payment(db, payment_id)
    await payment_service.delete_payment(db, operator=user, record=record)
    return {"ok": True}


# ══════════════ 2.5 丢单登记 ══════════════


@router.post("/opportunities/{lead_id}/lose", summary="登记丢单（必须多选 R1–R6）")
async def lose_opportunity(
    lead_id: str,
    payload: dict,
    user: User = Depends(require_lead_write),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    from app.schemas import LoseLeadRequest
    from app.services import lead_service, loss_service

    lead = await lead_service.get_lead(db, lead_id)
    request = LoseLeadRequest(**payload)
    await loss_service.register_loss(db, operator=user, lead=lead, payload=request)
    return {"lead": loss_service.loss_lead_out(lead)}


@router.post("/opportunities/{lead_id}/lose/revert", summary="撤销丢单")
async def revert_loss(
    lead_id: str,
    payload: dict | None = None,
    user: User = Depends(require_lead_write),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    from app.services import lead_service, loss_service

    lead = await lead_service.get_lead(db, lead_id)
    await loss_service.revive_lost(
        db, operator=user, lead=lead, remark=(payload or {}).get("remark")
    )
    return {"lead": loss_service.loss_lead_out(lead)}
