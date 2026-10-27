"""P1-c 预警服务：卡单 / 总时长 / 节奏窗口 / 转化率 四类预警 + 规则阈值配置。

口径（与 spec/03 §8、spec/08 S2-5 对齐）：
- **卡单预警**：当前环节停留 > 该环节基准 × 倍数，**且**完成标准未全部完成（可配置为只看停留）
- **总时长预警**：进入闭环二后的天数 ≥ 90 天 × 比例（默认 0.8 → 72 天开始报）
- **项目节奏不清**：下一关键节点及预计时间缺失或到期，由统一档案事实复核
- **转化率预警**：个人/团队阶段转化率 < 基准 × 比例且样本量达标（一→二 40% / 二→三 50% / 三→七 25%）

前两类按商机落 `RiskAlert`（幂等，同一天同一商机同一类只生成一条）；
转化率是**团队/个人级**、不挂单条商机，因此实时计算 + 命中时给管理人员发一次站内通知。
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import dicts
from app.core.errors import NotFoundError, ValidationError
from app.core.rules_version import risk_rules_digest
from app.models.enums import AlertKind, LeadStatus, RiskStatus, RiskType
from app.models.lead import Lead
from app.models.report import RuleConfig
from app.models.stage import RiskAlert, StageTransition
from app.models.system import Notification
from app.models.user import User
from app.services import audit_service, stage_service

logger = logging.getLogger(__name__)

# ══════════════ 规则阈值配置（页面 6.7）══════════════


async def ensure_rule_defaults(db: AsyncSession) -> int:
    """补齐缺失的规则，并**同步文案**（幂等，启动时调用）。

    - 缺键 → 建行（用默认值）
    - 已存在的键 → 只同步 label / description / unit / group / sort_order 这类**文案与元数据**，
      **绝不覆盖用户改过的 value**（6.7 的阈值是配置，不是代码）

    为什么要同步文案：口径调整后（例如 R5 从"年度窗口"改为"缺时间承诺"），
    老库里的 label/description 会一直是旧口径，页面和代码说的不是一回事。
    """
    meta_fields = ("label", "description", "unit", "group", "sort_order", "value_type")
    rows = {row.key: row for row in (await db.execute(select(RuleConfig))).scalars().all()}
    created = updated = 0
    for item in [*dicts.RULE_DEFAULTS, *dicts.OPS_RULE_DEFAULTS]:
        row = rows.get(item["key"])
        if row is None:
            db.add(RuleConfig(**item))
            created += 1
            continue
        changed = False
        for field in meta_fields:
            if field in item and getattr(row, field) != item[field]:
                setattr(row, field, item[field])
                changed = True
        if changed:
            updated += 1
    if created or updated:
        await db.flush()
        logger.info("预警规则初始化：新增 %s 条，同步文案 %s 条", created, updated)
    return created


async def list_rules(db: AsyncSession) -> list[RuleConfig]:
    rows = (
        (await db.execute(select(RuleConfig).order_by(RuleConfig.group, RuleConfig.sort_order)))
        .scalars()
        .all()
    )
    if not rows:
        await ensure_rule_defaults(db)
        rows = (
            (await db.execute(select(RuleConfig).order_by(RuleConfig.group, RuleConfig.sort_order)))
            .scalars()
            .all()
        )
    active = {item["key"] for item in [*dicts.RULE_DEFAULTS, *dicts.OPS_RULE_DEFAULTS]}
    return [row for row in rows if row.key in active]


def _cast(value: str, value_type: str):
    try:
        if value_type == "int":
            return int(float(value))
        if value_type == "float":
            return float(value)
    except (TypeError, ValueError):
        return 0
    return value


async def get_rules(db: AsyncSession) -> dict[str, object]:
    """返回 {key: 已按类型转换的值}，供业务代码读取。"""
    return {r.key: _cast(r.value, r.value_type) for r in await list_rules(db)}


async def get_rule(db: AsyncSession, key: str, default=None):
    row = (await db.execute(select(RuleConfig).where(RuleConfig.key == key))).scalar_one_or_none()
    if row is None:
        for item in [*dicts.RULE_DEFAULTS, *dicts.OPS_RULE_DEFAULTS]:
            if item["key"] == key:
                return _cast(item["value"], item["value_type"])
        return default
    return _cast(row.value, row.value_type)


async def update_rules(db: AsyncSession, *, operator: User, items: list[dict]) -> list[RuleConfig]:
    """批量更新阈值。"""
    updated: list[RuleConfig] = []
    active = {item["key"] for item in [*dicts.RULE_DEFAULTS, *dicts.OPS_RULE_DEFAULTS]}
    for item in items:
        key = (item or {}).get("key")
        if not key:
            continue
        if key not in active:
            raise NotFoundError(f"预警规则已停用或不存在：{key}")
        row = (
            await db.execute(select(RuleConfig).where(RuleConfig.key == key))
        ).scalar_one_or_none()
        if row is None:
            raise NotFoundError(f"预警规则不存在：{key}")
        value = item.get("value")
        if value is None:
            raise ValidationError(f"规则 {key} 缺少 value")
        # 数值型做一次转换校验，避免写入 "abc" 这种脏值
        if row.value_type in {"int", "float"}:
            try:
                float(str(value))
            except ValueError as exc:
                raise ValidationError(f"规则「{row.label or key}」必须是数字") from exc
        row.value = str(value)
        updated.append(row)

    await audit_service.write_log(
        db,
        operator=operator,
        action="update",
        target_type="rule_config",
        detail="修改预警规则：" + "、".join(r.label or r.key for r in updated),
    )
    return updated


def rule_out(rule: RuleConfig) -> dict:
    return {
        "key": rule.key,
        "value": _cast(rule.value, rule.value_type),
        "value_raw": rule.value,
        "value_type": rule.value_type,
        "label": rule.label,
        "group": rule.group,
        "unit": rule.unit,
        "description": rule.description,
        "sort_order": rule.sort_order,
    }


# ══════════════ 预警落库（幂等） ══════════════


async def _exists(db: AsyncSession, dedup_key: str) -> bool:
    return (
        await db.execute(select(RiskAlert.id).where(RiskAlert.dedup_key == dedup_key))
    ).scalar_one_or_none() is not None


async def create_alert(
    db: AsyncSession,
    *,
    lead: Lead,
    kind: str,
    risk_type: str,
    trigger_rule: str,
    description: str,
    dedup_key: str,
    source: str = "system",
    domain: str = "sales",
    owner_id: str | None = None,
    owner_name: str | None = None,
) -> bool:
    # 总时长是持续条件：未解除时更新同一条预警，不因跨日反复通知。
    # 兼容历史按日键；保留已解除记录，解除后再次触发可生成新事件。
    if kind == AlertKind.TOTAL_DURATION and source == "system":
        ongoing = (
            await db.execute(
                select(RiskAlert)
                .where(
                    RiskAlert.lead_id == lead.id,
                    RiskAlert.kind == kind,
                    RiskAlert.domain == domain,
                    RiskAlert.source == source,
                    RiskAlert.status.in_([RiskStatus.PENDING, RiskStatus.PROCESSING]),
                )
                .order_by(RiskAlert.created_at.desc(), RiskAlert.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if ongoing:
            ongoing.description = description
            ongoing.trigger_rule = trigger_rule
            ongoing.owner_id = owner_id or lead.owner_id
            ongoing.owner_name = owner_name or lead.owner_name
            return False
    if await _exists(db, dedup_key):
        return False
    alert = RiskAlert(
        lead_id=lead.id,
        kind=kind,
        domain=domain,
        risk_type=risk_type,
        trigger_rule=trigger_rule,
        description=description,
        status=RiskStatus.PENDING,
        owner_id=owner_id or lead.owner_id,
        owner_name=owner_name or lead.owner_name,
        dedup_key=dedup_key,
        rule_revision=risk_rules_digest(),
        source=source,
    )
    db.add(alert)
    receiver = owner_id or lead.owner_id
    if receiver:
        await audit_service.notify(
            db,
            user_id=receiver,
            title=f"{dicts.label_of(dicts.ALERT_KIND_LABELS, kind)}：{lead.name}",
            content=description,
            type_="risk_alert",
            biz_type="lead",
            biz_id=lead.id,
            event="risk_alert",
            context={
                "risk_label": dicts.label_of(dicts.ALERT_KIND_LABELS, kind)
                if kind != AlertKind.BUSINESS_RISK
                else dicts.label_of(dicts.RISK_TYPE_LABELS, risk_type),
                "lead_name": lead.name,
                "description": description,
            },
        )
    await audit_service.notify_many(
        db,
        user_ids=await audit_service.manager_ids(db),
        title=f"团队预警：{lead.name}",
        content=description,
        type_="risk_alert",
        biz_type="lead",
        biz_id=lead.id,
    )
    return True


# ══════════════ 扫描：卡单 / 总时长 / 下一节点节奏 ══════════════


async def close_signed_timing_risks(db):
    """签约结束销售计时；旧提醒转入已解除历史，不删除来源。"""
    rows = (await db.execute(select(RiskAlert).join(
        Lead, RiskAlert.lead_id == Lead.id
    ).where(
        Lead.status == LeadStatus.SIGNED, RiskAlert.domain == "sales",
        RiskAlert.status != RiskStatus.RESOLVED,
        or_(RiskAlert.kind.in_([
            AlertKind.STUCK, AlertKind.TOTAL_DURATION,
            AlertKind.RHYTHM_UNCLEAR, AlertKind.RHYTHM_WINDOW,
        ]), RiskAlert.risk_type == RiskType.R5_PROJECT_RHYTHM),
    ))).scalars().all()
    for alert in rows:
        alert.status, alert.handled_at = RiskStatus.RESOLVED, datetime.now()
        alert.handle_note = "项目已签约，销售计时及R5已结束；保留原提醒作为历史。"
        lead = await db.get(Lead, alert.lead_id)
        lead.risk_types = [key for key in (lead.risk_types or [])
                           if key != RiskType.R5_PROJECT_RHYTHM]


async def scan(db: AsyncSession, *, today: date | None = None) -> dict:
    """扫描全部在途商机，按需生成预警。可重复执行（幂等）。"""
    today = today or date.today()
    await close_signed_timing_risks(db)
    rules = await get_rules(db)
    day_key = today.strftime("%Y%m%d")
    created = {"stuck": 0, "total_duration": 0, "rhythm_unclear": 0}
    scanned = 0

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

    total_days = int(rules.get("total_duration_warn_days", 90) or 90)
    total_ratio = float(rules.get("total_duration_warn_ratio", 0.8) or 0.8)
    stuck_ratio = float(rules.get("stuck_warn_ratio", 1.0) or 1.0)
    require_unfinished = int(rules.get("stuck_require_unfinished", 1) or 0) == 1

    for lead in leads:
        scanned += 1
        stage = lead.current_stage or ""
        base_days = dicts.duration_of(stage)
        stay_days = (
            max((datetime.now() - lead.stage_entered_at).days, 0) if lead.stage_entered_at else 0
        )

        # ── ① 卡单预警 ──
        if base_days and stay_days > base_days * stuck_ratio:
            unfinished: list[str] = []
            if require_unfinished:
                progress = await stage_service.stage_progress(db, lead)
                unfinished = [
                    item["standard"].content
                    for item in progress["standards"]
                    if not item["completion"].is_done
                ]
            if not require_unfinished or unfinished:
                detail = (
                    f"当前环节（{dicts.label_of(dicts.STAGE_LABELS, stage)}）"
                    f"已停留 {stay_days} 天，超出基准 {base_days} 天"
                )
                if unfinished:
                    detail += "；未完成标准：" + "、".join(unfinished[:3])
                if await create_alert(
                    db,
                    lead=lead,
                    kind=AlertKind.STUCK,
                    risk_type="",
                    trigger_rule=f"停留 {stay_days} 天 > 基准 {base_days} 天 × {stuck_ratio}"
                    + ("（且标准未完成）" if require_unfinished else ""),
                    description=detail,
                    dedup_key=f"stuck:{lead.id}:{day_key}",
                ):
                    created["stuck"] += 1

        # ── ② 总时长预警 ──
        if lead.valid_at:
            total_stay = max((datetime.now() - lead.valid_at).days, 0)
            if total_stay >= total_days * total_ratio:
                if await create_alert(
                    db,
                    lead=lead,
                    kind=AlertKind.TOTAL_DURATION,
                    risk_type="",
                    trigger_rule=f"进入闭环二 {total_stay} 天 ≥ {total_days} 天 × {total_ratio}",
                    description=(
                        f"进入闭环二已 {total_stay} 天（基准总时长 {total_days} 天 / 3 个月），"
                        f"当前处于{dicts.label_of(dicts.STAGE_LABELS, stage)}，"
                        "请评估能否在窗口内签约"
                    ),
                    dedup_key=f"total_duration:{lead.id}:{day_key}",
                ):
                    created["total_duration"] += 1

        # R5所有入口共用档案事实判断，缺档案也不检查旧日期字段。
        from app.services import dossier_service

        dossier = await dossier_service.get(db, lead.id)
        new_types = await dossier_service.refresh_risks(
            db, lead=lead, dossier=dossier, today=today
        )
        created["rhythm_unclear"] += int(RiskType.R5_PROJECT_RHYTHM in new_types)

    await db.flush()
    return {"scanned": scanned, "created": created, "total_created": sum(created.values())}


# ══════════════ 转化率分析 + 预警 ══════════════


def _stage_entry_query(start: datetime | None, end: datetime | None, owner_id: str | None):
    stmt = (
        select(StageTransition.to_stage, StageTransition.lead_id)
        .join(Lead, Lead.id == StageTransition.lead_id)
        .where(Lead.is_deleted.is_(False))
    )
    if start:
        stmt = stmt.where(StageTransition.transition_at >= start)
    if end:
        stmt = stmt.where(StageTransition.transition_at <= end)
    if owner_id:
        stmt = stmt.where(Lead.owner_id == owner_id)
    return stmt


async def conversion_analysis(
    db: AsyncSession,
    *,
    start: date | None = None,
    end: date | None = None,
    owner_id: str | None = None,
) -> dict:
    """阶段转化率 vs 基准，并标出低于阈值的项。"""
    start_dt = datetime.combine(start, time.min) if start else None
    end_dt = datetime.combine(end, time.max) if end else None

    rows = (await db.execute(_stage_entry_query(start_dt, end_dt, owner_id))).all()
    entries: dict[str, set[str]] = {}
    for to_stage, lead_id in rows:
        entries.setdefault(to_stage, set()).add(lead_id)

    warn_ratio = float(await get_rule(db, "conversion_warn_ratio", 0.7) or 0.7)
    min_sample = int(await get_rule(db, "conversion_min_sample", 5) or 5)

    pairs: list[dict] = []
    for path, baseline in dicts.CONVERSION_BASELINE.items():
        from_stage, to_stage = path.split("->")
        from_count = len(entries.get(from_stage, set()))
        to_count = len(entries.get(to_stage, set()))
        rate = round(to_count / from_count, 4) if from_count else 0.0
        threshold = baseline * warn_ratio
        breached = bool(from_count >= min_sample and rate < threshold)
        pairs.append(
            {
                "path": path,
                "from_stage": from_stage,
                "from_stage_label": dicts.label_of(dicts.STAGE_LABELS, from_stage),
                "to_stage": to_stage,
                "to_stage_label": dicts.label_of(dicts.STAGE_LABELS, to_stage),
                "from_count": from_count,
                "to_count": to_count,
                "rate": rate,
                "baseline": baseline,
                "warn_threshold": round(threshold, 4),
                "gap": round(rate - baseline, 4),
                "alert": breached,
            }
        )

    # 个人维度：以"他名下进入过环节一的商机"为分母
    per_person: list[dict] = []
    person_rows = (
        await db.execute(
            select(Lead.owner_id, Lead.owner_name, Lead.id).where(
                Lead.is_deleted.is_(False),
                Lead.owner_id.is_not(None),
                Lead.status.in_([LeadStatus.VALID, LeadStatus.FOLLOWING, LeadStatus.SIGNED]),
            )
        )
    ).all()
    by_owner: dict[str, dict] = {}
    for owner, owner_name, lead_id in person_rows:
        item = by_owner.setdefault(
            owner,
            {"user_id": owner, "user_name": owner_name or "", "valid_count": 0, "_leads": set()},
        )
        item["_leads"].add(lead_id)
        item["valid_count"] += 1
    for item in by_owner.values():
        lead_ids = item.pop("_leads")
        advanced = int(
            (
                await db.execute(
                    select(func.count(func.distinct(StageTransition.lead_id))).where(
                        StageTransition.lead_id.in_(lead_ids),
                        StageTransition.to_stage.in_([dicts.STAGE_ORDER[2], dicts.STAGE_ORDER[6]]),
                    )
                )
            ).scalar()
            or 0
        )
        item["advanced_count"] = advanced
        item["rate"] = round(advanced / item["valid_count"], 4) if item["valid_count"] else 0.0
        item["baseline"] = dicts.CONVERSION_BASELINE["s3_proposal_quote->s7_contract_signed"]
        item["alert"] = bool(
            item["valid_count"] >= min_sample and item["rate"] < item["baseline"] * warn_ratio
        )
        per_person.append(item)
    if owner_id:
        per_person = [item for item in per_person if item["user_id"] == owner_id]
    per_person.sort(key=lambda x: x["rate"])

    return {
        "start": start.isoformat() if start else None,
        "end": end.isoformat() if end else None,
        "owner_scope": owner_id or "team",
        "pairs": pairs,
        "per_person": per_person,
        "warn_ratio": warn_ratio,
        "min_sample": min_sample,
        "breached": [p["path"] for p in pairs if p["alert"]],
    }


async def notify_conversion_alerts(db: AsyncSession, analysis: dict) -> int:
    """转化率命中时给管理人员发一次站内通知（同一天同一条只发一次）。"""
    if not analysis["breached"]:
        return 0
    managers = await audit_service.manager_ids(db)
    if not managers:
        return 0
    day = date.today().isoformat()
    today_start = datetime.combine(date.today(), time.min)
    sent = 0
    for path in analysis["breached"]:
        pair = next(p for p in analysis["pairs"] if p["path"] == path)
        biz_id = f"conversion:{path}:{day}"
        exists = (
            await db.execute(
                select(func.count())
                .select_from(Notification)
                .where(
                    Notification.biz_id == biz_id,
                    Notification.created_at >= today_start,
                )
            )
        ).scalar() or 0
        if exists:
            continue
        await audit_service.notify_many(
            db,
            user_ids=managers,
            title="转化率预警",
            content=(
                f"{pair['from_stage_label']} → {pair['to_stage_label']} 转化率 "
                f"{pair['rate'] * 100:.1f}%，低于基准 {pair['baseline'] * 100:.0f}%"
            ),
            type_="conversion_alert",
            event="conversion_alert",
            context={
                "from_stage": pair["from_stage_label"],
                "to_stage": pair["to_stage_label"],
                "rate": f"{pair['rate'] * 100:.1f}%",
                "baseline": f"{pair['baseline'] * 100:.0f}%",
                "sample": pair.get("sample", ""),
            },
            biz_type="conversion",
            biz_id=biz_id,
        )
        sent += len(managers)
    return sent


# ══════════════ 预警列表 / 处理 ══════════════


async def list_alerts(
    db: AsyncSession,
    *,
    status: str | None = None,
    kind: str | None = None,
    domain: str | None = None,
    lead_id: str | None = None,
    owner_id: str | None = None,
    delivery_owner_id: str | None = None,
    offset: int = 0,
    limit: int = 20,
) -> tuple[list[RiskAlert], int]:
    stmt = select(RiskAlert)
    if status:
        stmt = stmt.where(RiskAlert.status == status)
    if kind:
        stmt = stmt.where(RiskAlert.kind == kind)
    if domain:
        stmt = stmt.where(RiskAlert.domain == domain)
    if lead_id:
        stmt = stmt.where(RiskAlert.lead_id == lead_id)
    if owner_id:
        stmt = stmt.where(RiskAlert.owner_id == owner_id)
    if delivery_owner_id:
        stmt = stmt.where(_delivery_scope_condition(delivery_owner_id))
    total = int((await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar() or 0)
    rows = (
        (
            await db.execute(
                stmt.order_by(RiskAlert.status, RiskAlert.created_at.desc())
                .offset(offset)
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return list(rows), total


async def get_alert(db: AsyncSession, alert_id: str) -> RiskAlert:
    alert = (
        await db.execute(select(RiskAlert).where(RiskAlert.id == alert_id))
    ).scalar_one_or_none()
    if alert is None:
        raise NotFoundError("预警记录不存在")
    return alert


def _delivery_scope_condition(delivery_owner_id: str):
    """按**客户归属**过滤运营侧预警。

    为什么不用预警的 owner_id：承诺未兑现的负责人是"销售"，但**运营交付专员必须看到他
    自己客户的承诺预警**（他才是催兑现的人）——所以按合同归属过滤才正确。
    """
    from app.models.delivery import Contract

    return RiskAlert.lead_id.in_(
        select(Contract.lead_id).where(Contract.delivery_owner_id == delivery_owner_id)
    )


async def count_by_status(
    db: AsyncSession,
    *,
    owner_id: str | None = None,
    domain: str | None = None,
    delivery_owner_id: str | None = None,
) -> dict:
    """预警状态计数；owner_id / domain / 客户归属 非空时按对应口径过滤。"""
    stmt = select(RiskAlert.status, func.count())
    if owner_id:
        stmt = stmt.where(RiskAlert.owner_id == owner_id)
    if domain:
        stmt = stmt.where(RiskAlert.domain == domain)
    if delivery_owner_id:
        stmt = stmt.where(_delivery_scope_condition(delivery_owner_id))
    rows = (await db.execute(stmt.group_by(RiskAlert.status))).all()
    counts = {RiskStatus.PENDING: 0, RiskStatus.PROCESSING: 0, RiskStatus.RESOLVED: 0}
    for status, count in rows:
        counts[status] = count
    counts["total"] = sum(v for k, v in counts.items() if k != "total")
    return counts


# ══════════════ 闭环三 · 运营侧六类预警（spec/04 §7）══════════════


async def scan_delivery(db: AsyncSession, *, today: date | None = None) -> dict:
    """扫描运营交付侧：交付延期 / 长期未联系 / 验收临近 / 投诉未闭环 / 产品问题久拖 / 承诺未兑现。

     已确认六类全开；阈值全部走 6.7 规则配置。
     「交付延期」依赖阶段停留时长基准，本期基准未设（`delivery_delay_enabled=0`）→ 不产生预警。
    """
    from app.models.delivery import Contract
    from app.models.enums import (
        ComplaintStatus,
        ContractStatus,
        IssueStatus,
        IssueType,
    )
    from app.models.operation import Complaint, ProductIssue
    from app.services import acceptance_service, delivery_stage_service, operation_service

    today = today or date.today()
    day_key = today.strftime("%Y%m%d")
    rules = await get_rules(db)

    counts = {
        "delivery_delay": 0,
        "long_no_contact": 0,
        "acceptance_due": 0,
        "complaint_open": 0,
        "issue_overdue": 0,
        "promise_unmet": 0,
    }
    scanned = 0

    contracts = list(
        (
            await db.execute(
                select(Contract).where(
                    Contract.status.in_([ContractStatus.EXECUTING, ContractStatus.ACCEPTED])
                )
            )
        )
        .scalars()
        .all()
    )

    async def lead_of(contract: Contract) -> Lead | None:
        return (
            await db.execute(select(Lead).where(Lead.id == contract.lead_id))
        ).scalar_one_or_none()

    # ── ① 交付延期 + ② 长期未联系 + ③ 验收临近 ──
    delay_enabled = int(rules.get("delivery_delay_enabled", 0) or 0) == 1
    no_contact_days = int(rules.get("ops_no_contact_days", 30) or 30)
    acceptance_workdays = int(rules.get("acceptance_due_workdays", 15) or 15)

    due_list = {
        item["contract_id"]: item
        for item in await acceptance_service.acceptance_due_list(db, workdays=acceptance_workdays)
    }

    for contract in contracts:
        scanned += 1
        lead = await lead_of(contract)
        if lead is None:
            continue
        owner_id = contract.delivery_owner_id
        owner_name = contract.delivery_owner_name

        # ① 交付延期（基准未设时不报）
        if delay_enabled:
            info = await delivery_stage_service.contract_stage_info(db, contract)
            if info["overdue"]:
                if await create_alert(
                    db,
                    lead=lead,
                    kind=AlertKind.DELIVERY_DELAY,
                    risk_type="",
                    trigger_rule=(
                        f"阶段{contract.current_phase}停留 {info['stay_days']} 天"
                        f" > 基准 {info['base_days']} 天"
                    ),
                    description=(
                        f"{contract.customer_name} 处于"
                        f"{dicts.DELIVERY_PHASE_LABELS.get(contract.current_phase, '')}，"
                        f"已停留 {info['stay_days']} 天（基准 {info['base_days']} 天），"
                        f"产出材料 {info['material_done']}/{info['material_total']}"
                    ),
                    dedup_key=f"delivery_delay:{contract.id}:{day_key}",
                    domain="delivery",
                    owner_id=owner_id,
                    owner_name=owner_name,
                ):
                    counts["delivery_delay"] += 1

        # ② 仅交接确认后启动；没有运营联系时以交接确认日为起点。
        contact_state = await operation_service.contact_rhythm(
            db, contract, today=today, threshold_days=no_contact_days
        )
        confirmed = contact_state["confirmed_at"]
        idle_days = contact_state["idle_days"]
        if contact_state["overdue"]:
            if await create_alert(
                db, lead=lead, kind=AlertKind.LONG_NO_CONTACT, risk_type="",
                trigger_rule=f"交接确认后超过 {no_contact_days} 天未运营联系（{idle_days} 天）",
                description=(
                    f"{contract.customer_name} 交接后已 {idle_days} 天无有效运营联系，请安排回访"
                ),
                dedup_key=f"long_no_contact:{contract.id}:{day_key}", domain="delivery",
                owner_id=owner_id, owner_name=owner_name,
            ):
                counts["long_no_contact"] += 1
        else:
            active = (await db.execute(select(RiskAlert).where(
                RiskAlert.lead_id == lead.id, RiskAlert.domain == "delivery",
                RiskAlert.kind == AlertKind.LONG_NO_CONTACT,
                RiskAlert.status != RiskStatus.RESOLVED,
                RiskAlert.dedup_key.like(f"long_no_contact:{contract.id}:%"),
            ))).scalars().all()
            for alert in active:
                alert.status, alert.handled_at = RiskStatus.RESOLVED, datetime.now()
                alert.handle_note = ("未确认交接，不启动运营联系计时" if confirmed is None
                                     else "已按交接确认及有效运营联系重新核对，未超联系期限")

        # ③ 验收临近（到期前 15 个工作日）
        item = due_list.get(contract.id)
        if item is not None:
            if await create_alert(
                db,
                lead=lead,
                kind=AlertKind.ACCEPTANCE_DUE,
                risk_type="",
                trigger_rule=(
                    f"服务期到期前剩 {item['workdays_left']} 个工作日"
                    f"（SOP 要求 {acceptance_workdays} 个工作日）"
                ),
                description=(
                    f"{contract.customer_name} 服务期 {item['service_end']} 到期，"
                    f"剩余 {item['workdays_left']} 个工作日，"
                    f"验收状态：{item['status_label']}，请及时发起验收"
                ),
                dedup_key=f"acceptance_due:{contract.id}:{day_key}",
                domain="delivery",
                owner_id=owner_id,
                owner_name=owner_name,
            ):
                counts["acceptance_due"] += 1

    # ── ④ 投诉未闭环 ──
    complaint_overdue_days = int(rules.get("complaint_overdue_days", 3) or 3)
    complaints = list(
        (
            await db.execute(
                select(Complaint).where(
                    Complaint.status.in_([ComplaintStatus.PENDING, ComplaintStatus.PROCESSING])
                )
            )
        )
        .scalars()
        .all()
    )
    for complaint in complaints:
        hours = (datetime.now() - complaint.received_at).total_seconds() / 3600
        if hours < complaint_overdue_days * 24:
            continue
        lead = (
            await db.execute(select(Lead).where(Lead.id == complaint.lead_id))
        ).scalar_one_or_none()
        if lead is None:
            continue
        if await create_alert(
            db,
            lead=lead,
            kind=AlertKind.COMPLAINT_OPEN,
            risk_type="",
            trigger_rule=f"投诉登记超过 {complaint_overdue_days} 天未闭环",
            description=(
                f"{complaint.customer_name} 的投诉"
                f"（{dicts.label_of(dicts.COMPLAINT_REASON_LABELS, complaint.reason)}）"
                f"已 {hours / 24:.1f} 天未闭环：{complaint.content[:60]}"
            ),
            dedup_key=f"complaint_open:{complaint.id}:{day_key}",
            domain="delivery",
            owner_id=complaint.receiver_id or None,
            owner_name=complaint.receiver_name,
        ):
            counts["complaint_open"] += 1

    # ── ⑤ 产品问题久拖 + ⑥ 承诺未兑现 ──
    issue_overdue_days = int(rules.get("issue_overdue_days", 7) or 7)
    promise_warn_days = int(rules.get("promise_due_warn_days", 7) or 7)
    issues = list(
        (
            await db.execute(
                select(ProductIssue).where(
                    ProductIssue.status.in_([IssueStatus.PENDING, IssueStatus.PROCESSING])
                )
            )
        )
        .scalars()
        .all()
    )
    for issue in issues:
        lead = (await db.execute(select(Lead).where(Lead.id == issue.lead_id))).scalar_one_or_none()
        if lead is None:
            continue

        if issue.type == IssueType.PROMISE:
            # ⑥ 承诺未兑现：到期前 N 天提醒，或已过期
            if issue.promised_date and issue.promised_date <= today + timedelta(
                days=promise_warn_days
            ):
                overdue = issue.promised_date < today
                if await create_alert(
                    db,
                    lead=lead,
                    kind=AlertKind.PROMISE_UNMET,
                    risk_type="",
                    trigger_rule=(
                        f"承诺兑现时间 {issue.promised_date}"
                        + (
                            "（已过期）"
                            if overdue
                            else f"（还剩 {(issue.promised_date - today).days} 天）"
                        )
                    ),
                    description=(
                        f"{issue.customer_name} 的销售承诺"
                        + ("已过期未兑现" if overdue else "即将到期")
                        + f"：{issue.content[:60]}"
                    ),
                    dedup_key=f"promise_unmet:{issue.id}:{day_key}",
                    domain="delivery",
                    owner_id=issue.assignee_id or issue.registrar_id,
                    owner_name=issue.assignee_name or issue.registrar_name,
                ):
                    counts["promise_unmet"] += 1
        else:
            # ⑤ 产品问题久拖：超过处理时限或派单超过 N 天未解决
            age_days = (datetime.now() - issue.registered_at).days
            due_late = bool(issue.due_date and issue.due_date < today)
            if due_late or age_days >= issue_overdue_days:
                if await create_alert(
                    db,
                    lead=lead,
                    kind=AlertKind.ISSUE_OVERDUE,
                    risk_type="",
                    trigger_rule=(
                        f"处理时限 {issue.due_date}（已超期）"
                        if due_late
                        else f"登记已 {age_days} 天未解决"
                    ),
                    description=(
                        f"{issue.customer_name} 的产品问题"
                        f"（{dicts.label_of(dicts.HANDLER_SIDE_LABELS, issue.handler_side)}"
                        f"{('·' + issue.assignee_name) if issue.assignee_name else ''}）"
                        f"{'已超期' if due_late else f'已 {age_days} 天'}"
                        f"未解决：{issue.content[:60]}"
                    ),
                    dedup_key=f"issue_overdue:{issue.id}:{day_key}",
                    domain="delivery",
                    owner_id=issue.assignee_id or issue.registrar_id,
                    owner_name=issue.assignee_name or issue.registrar_name,
                ):
                    counts["issue_overdue"] += 1

    await db.flush()
    return {"scanned": scanned, "created": counts, "total_created": sum(counts.values())}


async def lead_names_for(db: AsyncSession, lead_ids: list[str]) -> dict[str, str]:
    """预警清单里显示客户名用（避免前端只看到 UUID）。"""
    ids = {lead_id for lead_id in lead_ids if lead_id}
    if not ids:
        return {}
    rows = (await db.execute(select(Lead.id, Lead.name).where(Lead.id.in_(ids)))).all()
    return {str(row[0]): str(row[1]) for row in rows}
