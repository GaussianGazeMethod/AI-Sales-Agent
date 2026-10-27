"""负责人对 PC 阶段候选的显式确认与字段更正。"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.models.activity import FollowUp
from app.models.dossier import ProjectDossier, ProjectFactEvent
from app.models.enums import DataSource
from app.models.lead import Lead
from app.models.stage import (
    StageCompletion,
    StageSalesConfirmation,
    StageStandard,
)
from app.models.user import User
from app.services import audit_service, dossier_service, judgement_service, stage_service

CORRECTABLE_FACTS = {"contract_arrangement_status", "contract_plan"}
CONTRACT_ARRANGEMENTS = {"single", "multiple", "unclear", "explicitly_pending", "not_discussed"}


def _canonical(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


async def _get_by_key(db: AsyncSession, key: str) -> StageSalesConfirmation | None:
    return (
        await db.execute(
            select(StageSalesConfirmation).where(StageSalesConfirmation.idempotency_key == key)
        )
    ).scalar_one_or_none()


async def get_confirmation(db: AsyncSession, confirmation_id: str) -> StageSalesConfirmation:
    row = await db.get(StageSalesConfirmation, confirmation_id)
    if row is None:
        raise NotFoundError("销售确认记录不存在")
    return row


async def _followup(db: AsyncSession, *, lead_id: str, followup_id: str, quote: str) -> FollowUp:
    row = (
        await db.execute(select(FollowUp).where(FollowUp.id == followup_id))
    ).scalar_one_or_none()
    if row is None or row.lead_id != lead_id:
        raise ValidationError("确认依据的跟进不存在或不属于当前项目")
    if not quote or quote not in (row.content or ""):
        raise ValidationError("确认依据无法在当前项目所选跟进中逐字核对")
    return row


async def confirm_stage_candidates(
    db: AsyncSession,
    *,
    user: User,
    lead: Lead,
    request: dict,
) -> dict:
    """只执行负责人明确选择的候选；不把旧AI satisfied 分类当作再次否决条件。"""
    key = str(request["idempotency_key"])
    canonical = _canonical({k: v for k, v in request.items() if k != "idempotency_key"})
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    previous = await _get_by_key(db, key)
    if previous is not None:
        if previous.lead_id != lead.id or previous.actor_id != user.id:
            raise ConflictError("确认幂等键已用于其他项目或操作人")
        if previous.request_sha256 != digest:
            raise ConflictError("相同确认键不能提交不同内容")
        return {"idempotent_replay": True, "response": previous.response}

    if not lead.current_stage or request["stage"] != lead.current_stage:
        raise ConflictError("项目环节已变化，请重新查看当前候选")
    judgement = await judgement_service.current_judgement(db, lead.id)
    if (
        judgement is None
        or judgement.id != request["judgement_id"]
        or judgement.stage != lead.current_stage
        or judgement.status != "pending"
    ):
        raise ConflictError("阶段候选版本已变化或已处理，请刷新后重新确认")
    latest_followup_at = await judgement_service.latest_followup_at(db, lead.id)
    if latest_followup_at is not None and (
        judgement.basis_followup_at is None or latest_followup_at > judgement.basis_followup_at
    ):
        raise ConflictError("候选依据已过期，请先查看最新跟进和判定")

    dossier = await dossier_service.get(db, lead.id)
    current_revision = dossier.revision if dossier is not None else None
    if request.get("expected_dossier_revision") != current_revision:
        raise ConflictError("项目档案版本已变化，请重新查看冲突和候选")

    items = request["items"]
    if len({item["standard_id"] for item in items}) != len(items):
        raise ValidationError("同一标准不能在一次确认中重复提交")
    candidate_ids = {
        str(item.get("standard_id"))
        for item in (judgement.items or [])
        if item.get("standard_id")
    }
    if any(item["standard_id"] not in candidate_ids for item in items):
        raise ValidationError("确认标准不属于本次展示的阶段候选")

    standard_rows: list[StageStandard] = []
    existing_completions: dict[str, StageCompletion] = {}
    for item in items:
        standard = (
            await db.execute(select(StageStandard).where(StageStandard.id == item["standard_id"]))
        ).scalar_one_or_none()
        if standard is None:
            raise NotFoundError("阶段标准不存在")
        if standard.stage != lead.current_stage or not standard.is_active:
            raise ConflictError("确认标准不属于当前有效环节")
        if item.get("followup_id") or item.get("source_quote"):
            if not item.get("followup_id") or not item.get("source_quote"):
                raise ValidationError("引用跟进时必须同时提供跟进和原文")
            await _followup(
                db,
                lead_id=lead.id,
                followup_id=item["followup_id"],
                quote=item["source_quote"],
            )
        completion = (
            await db.execute(
                select(StageCompletion).where(
                    StageCompletion.lead_id == lead.id,
                    StageCompletion.standard_id == standard.id,
                )
            )
        ).scalar_one_or_none()
        if completion is not None and completion.is_done:
            raise ConflictError("该标准已有完成依据，请勿覆盖原完成记录")
        standard_rows.append(standard)
        existing_completions[standard.id] = completion

    corrections = request.get("corrections") or []
    if len({c["fact_key"] for c in corrections}) != len(corrections):
        raise ValidationError("同一档案字段不能在一次确认中重复更正")
    correction_sources: list[tuple[dict, FollowUp]] = []
    current_facts = dict(dossier.facts or {}) if dossier is not None else {}
    for correction in corrections:
        key_name = correction["fact_key"]
        if key_name not in CORRECTABLE_FACTS:
            raise ValidationError("该档案字段不属于本次可确认更正范围")
        if current_facts.get(key_name) != correction.get("before_value"):
            raise ConflictError(f"档案字段 {key_name} 已变化，请重新展示 before/after")
        if correction["after_value"] == correction.get("before_value"):
            raise ValidationError("档案更正前后内容相同")
        if (
            key_name == "contract_arrangement_status"
            and correction["after_value"] not in CONTRACT_ARRANGEMENTS
        ):
            raise ValidationError("签约安排状态不合法")
        source = await _followup(
            db,
            lead_id=lead.id,
            followup_id=correction["followup_id"],
            quote=correction["source_quote"],
        )
        correction_sources.append((correction, source))

    receipt = StageSalesConfirmation(
        lead_id=lead.id,
        idempotency_key=key,
        request_sha256=digest,
        actor_id=user.id,
        actor_name=user.name,
        judgement_id=judgement.id,
        stage=lead.current_stage,
        dossier_revision=current_revision,
        confirmation_note=request["confirmation_note"],
        selected_items=items,
        corrections=corrections,
        response={},
    )
    db.add(receipt)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raced = await _get_by_key(db, key)
        if raced is not None and raced.lead_id == lead.id and raced.actor_id == user.id:
            if raced.request_sha256 == digest:
                return {"idempotent_replay": True, "response": raced.response}
            raise ConflictError("相同确认键不能提交不同内容")
        raise ConflictError("确认正在处理中，请使用同一确认键重试")

    now = datetime.now()
    if corrections:
        if dossier is None:
            dossier = ProjectDossier(lead_id=lead.id, revision=0, facts={}, evidence={})
            db.add(dossier)
            await db.flush()
        changed = await db.execute(
            update(ProjectDossier)
            .where(ProjectDossier.id == dossier.id, ProjectDossier.revision == current_revision)
            .values(revision=ProjectDossier.revision + 1)
        ) if current_revision is not None else None
        if current_revision is not None and changed.rowcount != 1:
            raise ConflictError("项目档案已并发变化，请重新展示并确认")
        facts, evidence = dict(dossier.facts or {}), dict(dossier.evidence or {})
        if current_revision is None:
            # The newly created row is already revision zero; bump it once below.
            dossier.revision = 0
        for correction, source in correction_sources:
            turn_id = uuid5(
                NAMESPACE_URL,
                f"stage-sales-confirm:{receipt.id}:{correction['fact_key']}",
            ).hex
            applied = dossier_service._apply_fact_update(
                facts,
                evidence,
                key=correction["fact_key"],
                value=correction["after_value"],
                quote=correction["source_quote"],
                occurred=now,
                turn_id=turn_id,
            )
            if not applied:
                raise ConflictError("所选事实来源早于档案当前版本，未应用更正")
            evidence[correction["fact_key"]].update(
                {
                    "source": DataSource.SALES_CONFIRMED.value,
                    "confirmation_id": receipt.id,
                    "confirmed_by": user.id,
                    "confirmed_by_name": user.name,
                    "confirmed_at": now.isoformat(),
                }
            )
            db.add(
                ProjectFactEvent(
                    lead_id=lead.id,
                    turn_id=turn_id,
                    owner_id=user.id,
                    occurred_at=source.followed_at,
                    content=correction["source_quote"],
                    changes={correction["fact_key"]: correction["after_value"]},
                    record={
                        "source": DataSource.SALES_CONFIRMED.value,
                        "confirmation_id": receipt.id,
                        "judgement_id": judgement.id,
                        "actor_id": user.id,
                    },
                    followup_id=source.id,
                )
            )
        dossier.facts, dossier.evidence = facts, evidence
        dossier.revision = (current_revision or 0) + 1

    confirmed_items = []
    for item, standard in zip(items, standard_rows, strict=True):
        completion = await stage_service.toggle_standard(
            db,
            user=user,
            lead=lead,
            standard_id=standard.id,
            is_done=True,
            evidence=item["confirmation_text"],
            remark=(
                f"销售明确确认采纳阶段候选；候选版本 {judgement.id}；"
                f"确认记录 {receipt.id}；{request['confirmation_note']}"
            ),
            source=DataSource.SALES_CONFIRMED.value,
        )
        confirmed_items.append(
            {
                "standard_id": standard.id,
                "content": standard.content,
                "is_done": completion.is_done,
                "evidence": completion.evidence,
                "source": completion.source,
                "done_by": completion.done_by,
                "done_by_name": completion.done_by_name,
                "done_at": completion.done_at.isoformat() if completion.done_at else None,
            }
        )

    judgement.status = "accepted"
    await audit_service.write_log(
        db,
        operator=user,
        action="stage_sales_confirmation",
        target_type="lead",
        target_id=lead.id,
        target_name=lead.name,
        detail=json.dumps(
            {
                "confirmation_id": receipt.id,
                "idempotency_key": key,
                "judgement_id": judgement.id,
                "stage": judgement.stage,
                "confirmation_note": request["confirmation_note"],
                "items": confirmed_items,
                "corrections": corrections,
                "source_followup_text": [source.content for _, source in correction_sources],
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
    )
    response = {
        "confirmation_id": receipt.id,
        "idempotent_replay": False,
        "judgement_id": judgement.id,
        "stage": lead.current_stage,
        "confirmed_by": user.id,
        "confirmed_by_name": user.name,
        "confirmed_at": now.isoformat(),
        "dossier_revision": dossier.revision if dossier is not None else current_revision,
        "applied_count": len(confirmed_items),
        "items": confirmed_items,
        "corrections": corrections,
        "message": "已记录销售确认；AI分类保留为原候选，未选择的档案字段没有更改。",
    }
    receipt.response = response
    await db.flush()
    return response
