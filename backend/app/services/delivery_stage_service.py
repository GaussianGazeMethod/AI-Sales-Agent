"""闭环三 · 10 阶段进度服务（P2-a）。

口径（spec/04 §4）：10 阶段 = 项目管理 / 项目启动 / 项目交付 / 场景应用培训 /
服务支持 / 主动运营 / 建立标杆 / 项目验收 / 项目增购 / 续费。
- 每个阶段有若干**关键产出材料**（= 天然完成标准），落为可配置数据（页面 6.6）
-  **勾选产出材料必须附证据**（与闭环二同一机制，解决"真执行"）
-  **阶段停留时长基准本期未设**：`duration_days = 0` → 不报超期（页面上显示"基准未设"）
"""

from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import dicts
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.models.delivery import Contract, DeliveryPhaseStandard, DeliveryStage
from app.models.user import User
from app.schemas import (
    DeliveryPhaseStandardCreate,
    DeliveryPhaseStandardUpdate,
    DeliveryStageToggle,
)
from app.services import audit_service

logger = logging.getLogger(__name__)

PHASES = list(range(1, 11))


# ══════════════ 配置（页面 6.6）══════════════


async def list_phase_standards(
    db: AsyncSession, *, phase: int | None = None
) -> list[DeliveryPhaseStandard]:
    stmt = select(DeliveryPhaseStandard).where(DeliveryPhaseStandard.is_active.is_(True))
    if phase:
        stmt = stmt.where(DeliveryPhaseStandard.phase == phase)
    return list(
        (
            await db.execute(
                stmt.order_by(DeliveryPhaseStandard.phase, DeliveryPhaseStandard.sort_order)
            )
        )
        .scalars()
        .all()
    )


async def create_phase_standard(
    db: AsyncSession, *, operator: User, payload: DeliveryPhaseStandardCreate
) -> DeliveryPhaseStandard:
    if payload.phase not in PHASES:
        raise ValidationError("阶段只能是 1~10")
    if payload.duration_days < 0:
        raise ValidationError("停留时长基准不能为负（0 表示未设基准）")
    max_sort = int(
        (
            await db.execute(
                select(func.coalesce(func.max(DeliveryPhaseStandard.sort_order), 0)).where(
                    DeliveryPhaseStandard.phase == payload.phase
                )
            )
        ).scalar()
        or 0
    )
    row = DeliveryPhaseStandard(
        phase=payload.phase,
        seq=payload.seq or "1",
        content=payload.content,
        is_required=payload.is_required,
        duration_days=payload.duration_days,
        evidence_hint=payload.evidence_hint,
        sort_order=max_sort + 1,
    )
    db.add(row)
    await db.flush()
    await audit_service.write_log(
        db,
        operator=operator,
        action="create",
        target_type="delivery_phase_standard",
        target_id=row.id,
        target_name=row.content,
        detail=f"新增阶段 {row.phase} 产出材料",
    )
    return row


async def update_phase_standard(
    db: AsyncSession,
    *,
    operator: User,
    row: DeliveryPhaseStandard,
    payload: DeliveryPhaseStandardUpdate,
) -> DeliveryPhaseStandard:
    data = payload.model_dump(exclude_unset=True)
    if data.get("duration_days") is not None and data["duration_days"] < 0:
        raise ValidationError("停留时长基准不能为负（0 表示未设基准）")
    for key, value in data.items():
        if value is not None:
            setattr(row, key, value)
    await audit_service.write_log(
        db,
        operator=operator,
        action="update",
        target_type="delivery_phase_standard",
        target_id=row.id,
        target_name=row.content,
        detail=f"修改阶段 {row.phase} 产出材料：" + "、".join(data.keys()),
    )
    return row


async def get_phase_standard(db: AsyncSession, standard_id: str) -> DeliveryPhaseStandard:
    row = (
        await db.execute(
            select(DeliveryPhaseStandard).where(DeliveryPhaseStandard.id == standard_id)
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError("阶段材料配置不存在")
    return row


async def phase_config(db: AsyncSession) -> list[dict]:
    """10 阶段的基准与材料数（3.5 页展示用）。"""
    rows = (
        await db.execute(
            select(
                DeliveryPhaseStandard.phase,
                func.count(),
                func.coalesce(func.max(DeliveryPhaseStandard.duration_days), 0),
            )
            .where(DeliveryPhaseStandard.is_active.is_(True))
            .group_by(DeliveryPhaseStandard.phase)
        )
    ).all()
    by_phase = {r[0]: (r[1], int(r[2])) for r in rows}
    return [
        {
            "phase": phase,
            "phase_label": dicts.DELIVERY_PHASE_LABELS.get(phase, ""),
            "material_count": by_phase.get(phase, (0, 0))[0],
            "duration_days": by_phase.get(phase, (0, 0))[1],
            "has_baseline": by_phase.get(phase, (0, 0))[1] > 0,
        }
        for phase in PHASES
    ]


def phase_standard_out(row: DeliveryPhaseStandard) -> dict:
    return {
        "id": row.id,
        "phase": row.phase,
        "phase_label": dicts.DELIVERY_PHASE_LABELS.get(row.phase, ""),
        "seq": row.seq,
        "content": row.content,
        "is_required": row.is_required,
        "duration_days": row.duration_days,
        "evidence_hint": row.evidence_hint,
        "sort_order": row.sort_order,
        "is_active": row.is_active,
    }


# ══════════════ 进度 ══════════════


async def ensure_all_stages(db: AsyncSession, contract: Contract) -> int:
    """为合同初始化全部 10 阶段的材料完成记录（幂等）。"""
    standards = await list_phase_standards(db)
    existing = {
        row.standard_id
        for row in (
            await db.execute(select(DeliveryStage).where(DeliveryStage.contract_id == contract.id))
        )
        .scalars()
        .all()
    }
    created = 0
    for std in standards:
        if std.id in existing:
            continue
        db.add(
            DeliveryStage(
                contract_id=contract.id,
                phase=std.phase,
                standard_id=std.id,
                standard_content=std.content,
                is_done=False,
            )
        )
        created += 1
    if created:
        await db.flush()
    return created


async def ensure_phase_materials(
    db: AsyncSession, contract: Contract, phase: int
) -> list[DeliveryStage]:
    await ensure_all_stages(db, contract)
    return list(
        (
            await db.execute(
                select(DeliveryStage).where(
                    DeliveryStage.contract_id == contract.id, DeliveryStage.phase == phase
                )
            )
        )
        .scalars()
        .all()
    )


async def phase_detail(
    db: AsyncSession,
    contract: Contract,
    phase: int,
    *,
    standards_map: dict[str, DeliveryPhaseStandard] | None = None,
) -> dict:
    """单阶段明细：材料清单 + 完成情况 + 停留天数 vs 基准。

    `standards_map` 可传入已加载的全部标准（批量渲染 10 阶段时避免 N+1 查询）。
    """
    if phase not in PHASES:
        raise ValidationError("阶段只能是 1~10")

    rows = await ensure_phase_materials(db, contract, phase)
    if standards_map is None:
        standards = {s.id: s for s in await list_phase_standards(db, phase=phase)}
    else:
        standards = {sid: s for sid, s in standards_map.items() if s.phase == phase}
    materials = []
    missing_required: list[str] = []
    for row in rows:
        std = standards.get(row.standard_id)
        if std is None:
            continue
        materials.append({"standard": std, "completion": row})
        if std.is_required and not row.is_done:
            missing_required.append(std.content)
    materials.sort(key=lambda x: x["standard"].sort_order)

    base_days = max([s.duration_days for s in standards.values()] or [0])
    info = await stage_timing(db, contract, phase, base_days=base_days)
    return {
        "phase": phase,
        "phase_label": dicts.DELIVERY_PHASE_LABELS.get(phase, ""),
        "is_current": contract.current_phase == phase,
        "is_done": not missing_required and bool(materials),
        "done_count": sum(1 for m in materials if m["completion"].is_done),
        "total_count": len(materials),
        "missing_required": missing_required,
        "can_complete": not missing_required,
        #  基准未设（base_days=0）时 overdue 恒为 False
        "stay_days": info["stay_days"],
        "base_days": info["base_days"],
        "has_baseline": info["base_days"] > 0,
        "overdue": info["overdue"],
        "materials": [
            {
                "standard": phase_standard_out(m["standard"]),
                "completion": stage_out(m["completion"]),
            }
            for m in materials
        ],
    }


async def contract_progress(db: AsyncSession, contract: Contract) -> dict:
    """当前阶段进度（3.2/3.5 的主视图）。"""
    return await phase_detail(db, contract, contract.current_phase)


async def contract_stage_info(db: AsyncSession, contract: Contract) -> dict:
    """合同的阶段统计（合同列表派生字段用）。

    批量实现：只做 3 次查询（阶段记录 / 材料配置 / 计数），避免在列表里 N+1。
    """
    await ensure_all_stages(db, contract)
    rows = list(
        (await db.execute(select(DeliveryStage).where(DeliveryStage.contract_id == contract.id)))
        .scalars()
        .all()
    )
    standards = await list_phase_standards(db)
    by_phase: dict[int, list[DeliveryPhaseStandard]] = {}
    for std in standards:
        by_phase.setdefault(std.phase, []).append(std)

    done_map = {r.standard_id: r.is_done for r in rows}
    done_phases = 0
    for phase, stds in by_phase.items():
        required = [s for s in stds if s.is_required]
        if required and all(done_map.get(s.id) for s in required):
            done_phases += 1

    current_base = max([s.duration_days for s in by_phase.get(contract.current_phase, [])] or [0])
    timing = await stage_timing(db, contract, contract.current_phase, base_days=current_base)
    return {
        "done_phases": done_phases,
        "total_phases": len(PHASES),
        "stay_days": timing["stay_days"],
        "base_days": timing["base_days"],
        "overdue": timing["overdue"],
        "material_done": sum(1 for r in rows if r.is_done),
        "material_total": len(standards),
    }


async def stages_summary(db: AsyncSession, contract: Contract) -> list[dict]:
    """10 阶段概览（进度条用）。共享一次加载的材料配置，避免 N+1。"""
    await ensure_all_stages(db, contract)
    standards_map = {s.id: s for s in await list_phase_standards(db)}
    result = []
    for phase in PHASES:
        detail = await phase_detail(db, contract, phase, standards_map=standards_map)
        result.append(
            {
                "phase": phase,
                "phase_label": detail["phase_label"],
                "is_current": detail["is_current"],
                "is_done": detail["is_done"],
                "done_count": detail["done_count"],
                "total_count": detail["total_count"],
                "stay_days": detail["stay_days"],
                "base_days": detail["base_days"],
                "has_baseline": detail["has_baseline"],
                "overdue": detail["overdue"],
            }
        )
    return result


async def stage_timing(
    db: AsyncSession,
    contract: Contract,
    phase: int,
    *,
    base_days: int | None = None,
) -> dict:
    """某阶段的停留天数与基准。

     现只有"当前阶段"能算真实停留（用 `contract.phase_entered_at`）；
    历史阶段无法反推进入时间（本期不建阶段流转表），因此非当前阶段返回 0，
    并明确 `has_baseline=False` 时不显示超期。
    """
    if base_days is None:
        standards = await list_phase_standards(db, phase=phase)
        base_days = max([s.duration_days for s in standards] or [0])
    stay_days = 0
    if contract.current_phase == phase and contract.phase_entered_at:
        stay_days = max((datetime.now() - contract.phase_entered_at).days, 0)
    overdue = bool(contract.current_phase == phase and base_days > 0 and stay_days > base_days)
    return {"stay_days": stay_days, "base_days": base_days, "overdue": overdue}


# ══════════════ 勾选材料（证据必填）══════════════


async def toggle_material(
    db: AsyncSession,
    *,
    operator: User,
    contract: Contract,
    phase: int,
    standard_id: str,
    payload: DeliveryStageToggle,
) -> DeliveryStage:
    standard = await get_phase_standard(db, standard_id)
    if standard.phase != phase:
        raise ValidationError("该材料不属于这个阶段")
    if payload.is_done and not (payload.evidence or "").strip():
        raise ValidationError("勾选产出材料必须附证据（文件 / 签字扫描件 / 记录）")

    rows = await ensure_phase_materials(db, contract, phase)
    target = next((r for r in rows if r.standard_id == standard_id), None)
    if target is None:
        raise NotFoundError("阶段材料记录不存在")

    target.is_done = payload.is_done
    if payload.is_done:
        target.evidence = payload.evidence
        target.done_by = operator.id
        target.done_by_name = operator.name
        target.done_at = datetime.now()
    if payload.remark is not None:
        target.remark = payload.remark
    target.source = "manual"

    await audit_service.write_log(
        db,
        operator=operator,
        action="delivery_material_toggle",
        target_type="contract",
        target_id=contract.id,
        target_name=contract.customer_name,
        detail=(
            f"阶段{phase} {'勾选' if payload.is_done else '取消'}产出材料「{standard.content}」；"
            f"证据：{(payload.evidence or '')[:80]}"
        ),
    )
    return target


async def complete_phase(
    db: AsyncSession, *, operator: User, contract: Contract, phase: int, remark: str | None = None
) -> Contract:
    """完成阶段：必填材料未完成则拒绝，通过后进入下一阶段。"""
    if phase != contract.current_phase:
        raise ConflictError(f"只能完成当前阶段（当前为第 {contract.current_phase} 阶段）")

    detail = await phase_detail(db, contract, phase)
    if detail["missing_required"]:
        raise ConflictError(
            "必填产出材料未完成，不能进入下一阶段。缺失：" + "；".join(detail["missing_required"])
        )
    if phase >= 10:
        raise ConflictError("已是最后一个阶段（十 续费）")

    contract.current_phase = phase + 1
    contract.phase_entered_at = datetime.now()
    await ensure_all_stages(db, contract)

    await audit_service.write_log(
        db,
        operator=operator,
        action="delivery_phase_complete",
        target_type="contract",
        target_id=contract.id,
        target_name=contract.customer_name,
        detail=(
            f"阶段{phase}（{dicts.DELIVERY_PHASE_LABELS.get(phase, '')}）完成 → "
            f"进入阶段{contract.current_phase}；{remark or ''}"
        ),
    )
    return contract


async def reopen_phase(
    db: AsyncSession, *, operator: User, contract: Contract, phase: int, remark: str | None = None
) -> Contract:
    """重新打开某阶段（回退），用于整改或补录。"""
    if phase >= contract.current_phase:
        raise ConflictError("只能回退到当前阶段之前的阶段")
    contract.current_phase = phase
    contract.phase_entered_at = datetime.now()
    await audit_service.write_log(
        db,
        operator=operator,
        action="delivery_phase_reopen",
        target_type="contract",
        target_id=contract.id,
        target_name=contract.customer_name,
        detail=f"重新打开阶段{phase}；{remark or ''}",
    )
    return contract


def stage_out(row: DeliveryStage) -> dict:
    return {
        "id": row.id,
        "contract_id": row.contract_id,
        "phase": row.phase,
        "phase_label": dicts.DELIVERY_PHASE_LABELS.get(row.phase, ""),
        "standard_id": row.standard_id,
        "standard_content": row.standard_content,
        "is_done": row.is_done,
        "evidence": row.evidence,
        "done_by": row.done_by,
        "done_by_name": row.done_by_name,
        "done_at": row.done_at.isoformat() if row.done_at else None,
        "remark": row.remark,
        "source": row.source,
    }
