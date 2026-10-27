"""P1-d 项目评审服务（页面 2.7 / 场景 S2-8 第三步）。

评审卡 = 管理者 5 看逐项判定 + **真执行判定（能力问题 / 态度问题 / 正常）** + 下次评审时间。
这是业务规则最痛的点（"看不到每一步有没有真执行，是能力还是态度问题"）的落点。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import dicts
from app.core.errors import NotFoundError, ValidationError
from app.models.enums import LeadStatus, ReviewResult, ReviewVerdict
from app.models.lead import Lead
from app.models.report import ProjectReview
from app.models.user import User
from app.schemas import ProjectReviewCreate
from app.services import audit_service, risk_service

_REVIEW_FIELDS = [item["key"] for item in dicts.REVIEW_ITEMS]


def _norm_item(raw) -> dict:
    """规整 5 看某一项：{result, note}。入参可能是 pydantic 模型或 dict。"""
    if raw is None:
        raw = {}
    elif hasattr(raw, "model_dump"):
        raw = raw.model_dump()
    elif not isinstance(raw, dict):
        raw = {}
    result = raw.get("result") or ReviewResult.RISK
    if result not in {r.value for r in ReviewResult}:
        raise ValidationError(f"5 看结论只能是 {[r.value for r in ReviewResult]}")
    return {"result": result, "note": (raw.get("note") or "").strip()}


def result_label(review: ProjectReview, item: dict) -> str:
    """把 5 看某一项的结论翻成中文（日志用）。"""
    value = getattr(review, item["key"]) or {}
    return dicts.label_of(dicts.REVIEW_RESULT_LABELS, value.get("result"))


async def create_review(
    db: AsyncSession, *, operator: User, payload: ProjectReviewCreate
) -> ProjectReview:
    lead = (
        await db.execute(select(Lead).where(Lead.id == payload.lead_id, Lead.is_deleted.is_(False)))
    ).scalar_one_or_none()
    if lead is None:
        raise NotFoundError("商机不存在")
    if payload.verdict not in {v.value for v in ReviewVerdict}:
        raise ValidationError("真执行判定只能是 ability / attitude / normal")

    review = ProjectReview(
        lead_id=lead.id,
        lead_name=lead.name,
        stage=lead.current_stage,
        reviewer_id=operator.id,
        reviewer_name=operator.name,
        review_at=datetime.now(),
        verdict=payload.verdict,
        conclusion=payload.conclusion,
        next_review_at=payload.next_review_at,
        remark=payload.remark,
    )
    for field in _REVIEW_FIELDS:
        setattr(review, field, _norm_item(getattr(payload, field, None)))

    db.add(review)
    # 必须先 flush 拿到 review.id：否则下面的 dedup_key 会退化成 review:None，
    # 第二次评审起就不会再生成预警（实测踩过）
    await db.flush()

    # 判定为「能力/态度问题」时同步登记一条业务风险，便于预警中心统一跟踪
    if payload.verdict in {ReviewVerdict.ABILITY, ReviewVerdict.ATTITUDE}:
        label = dicts.label_of(dicts.REVIEW_VERDICT_LABELS, payload.verdict)
        await risk_service.create_alert(
            db,
            lead=lead,
            kind="business_risk",
            risk_type="",
            trigger_rule="项目评审卡真执行判定",
            description=(
                f"评审判定为「{label}」：{payload.conclusion or payload.remark or '详见评审卡'}"
            ),
            dedup_key=f"review:{review.id}",
            source="review",
        )

    await audit_service.write_log(
        db,
        operator=operator,
        action="review",
        target_type="lead",
        target_id=lead.id,
        target_name=lead.name,
        detail=(
            "项目评审：5 看 "
            + "、".join(
                f"{item['code']}={result_label(review, item)}" for item in dicts.REVIEW_ITEMS
            )
            + f"；判定={dicts.label_of(dicts.REVIEW_VERDICT_LABELS, payload.verdict)}"
        ),
    )
    await db.flush()
    return review


async def list_reviews(
    db: AsyncSession,
    *,
    lead_id: str | None = None,
    reviewer_id: str | None = None,
    verdict: str | None = None,
    offset: int = 0,
    limit: int = 20,
) -> tuple[list[ProjectReview], int]:
    stmt = select(ProjectReview)
    if lead_id:
        stmt = stmt.where(ProjectReview.lead_id == lead_id)
    if reviewer_id:
        stmt = stmt.where(ProjectReview.reviewer_id == reviewer_id)
    if verdict:
        stmt = stmt.where(ProjectReview.verdict == verdict)
    total = int((await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar() or 0)
    rows = (
        (
            await db.execute(
                stmt.order_by(ProjectReview.review_at.desc()).offset(offset).limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return list(rows), total


async def get_review(db: AsyncSession, review_id: str) -> ProjectReview:
    review = (
        await db.execute(select(ProjectReview).where(ProjectReview.id == review_id))
    ).scalar_one_or_none()
    if review is None:
        raise NotFoundError("评审记录不存在")
    return review


async def latest_reviews_by_lead(db: AsyncSession, lead_ids: list[str]) -> dict[str, ProjectReview]:
    if not lead_ids:
        return {}
    rows = (
        (
            await db.execute(
                select(ProjectReview)
                .where(ProjectReview.lead_id.in_(lead_ids))
                .order_by(ProjectReview.review_at.desc())
            )
        )
        .scalars()
        .all()
    )
    result: dict[str, ProjectReview] = {}
    for review in rows:
        result.setdefault(review.lead_id, review)
    return result


async def pending_review_leads(db: AsyncSession, *, limit: int = 50) -> list[dict]:
    """待评审清单：进入闭环二后，从未评审 / 距下次评审时间已到 / 超过 N 天未评审。"""
    after_days = int(await risk_service.get_rule(db, "stuck_missing_review_days", 14) or 14)
    today = date.today()

    leads = (
        (
            await db.execute(
                select(Lead).where(
                    Lead.is_deleted.is_(False),
                    Lead.current_stage.is_not(None),
                    Lead.status.in_([LeadStatus.VALID, LeadStatus.FOLLOWING]),
                )
            )
        )
        .scalars()
        .all()
    )

    latest = await latest_reviews_by_lead(db, [lead.id for lead in leads])
    result: list[dict] = []
    for lead in leads:
        review = latest.get(lead.id)
        reason = None
        if review is None:
            entered = lead.stage_entered_at or lead.valid_at
            if entered and (datetime.now() - entered).days >= after_days:
                reason = f"进入闭环二后 {after_days} 天未评审"
        elif review.next_review_at and review.next_review_at <= today:
            reason = f"已到下次评审时间（{review.next_review_at.isoformat()}）"
        if reason:
            result.append(
                {
                    "lead_id": lead.id,
                    "lead_name": lead.name,
                    "owner_name": lead.owner_name,
                    "stage": lead.current_stage,
                    "stage_label": dicts.label_of(dicts.STAGE_LABELS, lead.current_stage),
                    "stay_days": (datetime.now() - lead.stage_entered_at).days
                    if lead.stage_entered_at
                    else 0,
                    "reason": reason,
                    "last_review_at": review.review_at.isoformat() if review else None,
                    "last_verdict": review.verdict if review else None,
                    "last_verdict_label": dicts.label_of(
                        dicts.REVIEW_VERDICT_LABELS, review.verdict
                    )
                    if review
                    else None,
                    "last_conclusion": review.conclusion if review else None,
                }
            )
    result.sort(key=lambda x: x["stay_days"], reverse=True)
    return result[:limit]


def review_out(review: ProjectReview) -> dict:
    items = []
    for item in dicts.REVIEW_ITEMS:
        value = getattr(review, item["key"]) or {}
        items.append(
            {
                "key": item["key"],
                "code": item["code"],
                "label": item["label"],
                "hint": item["hint"],
                "result": value.get("result"),
                "result_label": dicts.label_of(dicts.REVIEW_RESULT_LABELS, value.get("result")),
                "note": value.get("note") or "",
            }
        )
    return {
        "id": review.id,
        "lead_id": review.lead_id,
        "lead_name": review.lead_name,
        "stage": review.stage,
        "stage_label": dicts.label_of(dicts.STAGE_LABELS, review.stage),
        "reviewer_id": review.reviewer_id,
        "reviewer_name": review.reviewer_name,
        "review_at": review.review_at.isoformat() if review.review_at else None,
        "items": items,
        "verdict": review.verdict,
        "verdict_label": dicts.label_of(dicts.REVIEW_VERDICT_LABELS, review.verdict),
        "conclusion": review.conclusion,
        "next_review_at": review.next_review_at.isoformat() if review.next_review_at else None,
        "remark": review.remark,
        "created_at": review.created_at.isoformat() if review.created_at else None,
    }


def default_next_review_date(days: int = 7) -> date:
    """默认下次评审时间：一周后（周会节奏）。"""
    return date.today() + timedelta(days=days)
