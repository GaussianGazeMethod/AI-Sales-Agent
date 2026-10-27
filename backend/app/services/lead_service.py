"""线索服务：录入、查重撞单、列表筛选、编辑、软删除、无效复活、疑似重复裁决。"""

from __future__ import annotations

import re
from datetime import datetime

from sqlalchemy import Select, and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import dicts
from app.core.deps import can_view_all_leads, is_manager
from app.core.errors import ConflictError, NotFoundError, PermissionDeniedError
from app.models.customer import Customer
from app.models.enums import DedupStatus, LeadStatus, ReminderStatus, Stage, UserRole
from app.models.lead import Contact, Lead
from app.models.user import User
from app.schemas import ContactCreate, ContactUpdate, LeadCreate, LeadUpdate
from app.services import audit_service

_WS_RE = re.compile(r"[\s\u3000]+")


def normalize_name(name: str) -> str:
    """客户名称归一化：去空白、去常见后缀差异，用于查重。"""
    return _WS_RE.sub("", (name or "").strip())


def _norm_region(value: str | None) -> str:
    return _WS_RE.sub("", (value or "").strip())


# ══════════════ 查重 / 撞单 ══════════════


async def check_duplicate(
    db: AsyncSession,
    *,
    name: str,
    province: str = "",
    city: str = "",
    district: str = "",
    exclude_id: str | None = None,
) -> list[Lead]:
    """客户级查重（依据已确认：客户名称 + 省 + 市 + 区/县）。

    两档命中都算疑似：
    ① 名称完全相同（忽略空白）——即使地区不同也提示；
    ② 名称相同且省市区一致 —— 强命中。
    """
    norm = normalize_name(name)
    if not norm:
        return []
    stmt = select(Lead).where(Lead.is_deleted.is_(False), Lead.name.is_not(None))
    if exclude_id:
        stmt = stmt.where(Lead.id != exclude_id)

    candidates = (
        (await db.execute(stmt.order_by(Lead.created_at.desc()).limit(500))).scalars().all()
    )
    p, c, d = _norm_region(province), _norm_region(city), _norm_region(district)

    hits: list[Lead] = []
    for item in candidates:
        if normalize_name(item.name) != norm:
            continue
        region_same = (
            _norm_region(item.province) == p
            and _norm_region(item.city) == c
            and _norm_region(item.district) == d
        )
        # 名称相同即提示；地区也相同则标记为强命中（放在最前）
        item.__dict__["_region_same"] = region_same
        hits.append(item)
    hits.sort(key=lambda x: not x.__dict__.get("_region_same", False))
    return hits[:20]


def duplicate_warning(hits: list[Lead]) -> dict:
    return {
        "suspect": bool(hits),
        "duplicates": [
            {
                "id": h.id,
                "name": h.name,
                "owner_name": h.owner_name or "未指派",
                "province": h.province,
                "city": h.city,
                "district": h.district,
                "status": h.status,
                "status_label": dicts.label_of(dicts.LEAD_STATUS_LABELS, h.status),
                "region_same": h.__dict__.get("_region_same", False),
            }
            for h in hits
        ],
    }


# ══════════════ 列表查询 ══════════════


def _apply_view(stmt: Select, view: str, user: User) -> Select:
    """子视图：全部 / 未指派 / 我的 / 公海 / 疑似重复 / 无效。"""
    if view == "unassigned":
        stmt = stmt.where(Lead.owner_id.is_(None), Lead.status == LeadStatus.UNASSIGNED)
    elif view == "mine":
        stmt = stmt.where(Lead.owner_id == user.id)
    elif view == "pool":
        stmt = stmt.where(Lead.owner_id.is_(None))
    elif view == "duplicate":
        stmt = stmt.where(
            Lead.is_suspect_duplicate.is_(True),
            or_(Lead.dedup_status.is_(None), Lead.dedup_status == DedupStatus.PENDING),
        )
    elif view == "invalid":
        stmt = stmt.where(Lead.status == LeadStatus.INVALID)
    return stmt


async def list_leads(
    db: AsyncSession,
    *,
    user: User,
    view: str = "all",
    lifecycle: str | None = None,
    active_only: bool = False,
    keyword: str | None = None,
    status: str | None = None,
    source: str | None = None,
    org_type: str | None = None,
    province: str | None = None,
    city: str | None = None,
    owner_id: str | None = None,
    include_deleted: bool = False,
    sort: str = "recent",
    offset: int = 0,
    limit: int = 20,
) -> tuple[list[Lead], int]:
    conditions = [Lead.is_deleted.is_(True) if include_deleted else Lead.is_deleted.is_(False)]

    stmt: Select = select(Lead).where(*conditions)
    stmt = _apply_view(stmt, view, user)
    if lifecycle == "lead":
        stmt = stmt.where(Lead.current_stage.is_(None))
    elif lifecycle == "opportunity":
        stmt = stmt.where(Lead.current_stage.is_not(None))
    if active_only:
        stmt = stmt.where(Lead.status != LeadStatus.INVALID)

    # 数据范围：非全量角色只能看到自己名下的（公海视图除外，公海是共享池）
    if not can_view_all_leads(user) and view != "pool":
        stmt = stmt.where(Lead.owner_id == user.id)

    if keyword:
        kw = f"%{keyword.strip()}%"
        stmt = stmt.where(
            or_(
                Lead.name.like(kw),
                Lead.channel_unit.like(kw),
                Lead.referrer.like(kw),
                Lead.city.like(kw),
            )
        )
    if status:
        stmt = stmt.where(Lead.status == status)
    if source:
        stmt = stmt.where(Lead.source == source)
    if org_type:
        stmt = stmt.where(Lead.org_type == org_type)
    if province:
        stmt = stmt.where(Lead.province == province)
    if city:
        stmt = stmt.where(Lead.city == city)
    if owner_id:
        stmt = stmt.where(Lead.owner_id == owner_id)

    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = int((await db.execute(count_stmt)).scalar() or 0)

    if sort == "overdue":
        stmt = stmt.order_by(Lead.first_follow_deadline.is_(None), Lead.first_follow_deadline.asc())
    else:
        stmt = stmt.order_by(Lead.updated_at.desc())

    rows = (await db.execute(stmt.offset(offset).limit(limit))).scalars().all()
    return list(rows), total


async def get_lead(db: AsyncSession, lead_id: str, *, include_deleted: bool = False) -> Lead:
    stmt = select(Lead).where(Lead.id == lead_id)
    if not include_deleted:
        stmt = stmt.where(Lead.is_deleted.is_(False))
    lead = (await db.execute(stmt)).scalar_one_or_none()
    if lead is None:
        raise NotFoundError("线索/商机不存在")
    return lead


def assert_can_edit(user: User, lead: Lead) -> None:
    """专员只能编辑本人名下的线索；管理人员/管理员不受限。"""
    if is_manager(user):
        return
    if lead.owner_id != user.id:
        raise PermissionDeniedError("只能编辑本人负责的线索")


def assert_can_view(user: User, lead: Lead) -> None:
    """专员只能查看本人名下的商机；管理人员/管理员不受限。

    数据范围口径与列表页一致（`can_view_all_leads`）。单条详情接口原先只校验角色，
    这里补上归属校验：否则专员可以靠猜 id 读到别人商机的环节与达成依据
    （`GET /opportunities/{id}/stages/{stage}/progress`）。
    """
    if can_view_all_leads(user):
        return
    if lead.owner_id != user.id:
        raise PermissionDeniedError("只能查看本人负责的商机")


async def assert_can_view_lead(db: AsyncSession, user: User, lead: Lead) -> None:
    """线索读取保留回收后原负责人的只读权限；商机仍用严格归属校验。"""
    from app.models.activity import Assignment
    from app.models.enums import AssignmentAction

    if lead.owner_id is None and lead.status == LeadStatus.UNASSIGNED:
        latest = (
            await db.execute(
                select(Assignment)
                .where(Assignment.lead_id == lead.id)
                .order_by(Assignment.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if latest and latest.action == AssignmentAction.RECYCLE and latest.from_owner_id == user.id:
            return
    assert_can_view(user, lead)


# ══════════════ 录入 / 编辑 / 删除 ══════════════


async def create_lead(db: AsyncSession, *, user: User, payload: LeadCreate) -> tuple[Lead, dict]:
    """录入线索。撞单只提示不阻断（已确认 U3）：允许继续录 + 标记疑似重复。"""
    hits = await check_duplicate(
        db,
        name=payload.name,
        province=payload.province,
        city=payload.city,
        district=payload.district,
    )
    warning = duplicate_warning(hits)

    owner_id = payload.owner_id
    owner_name = None
    # 销售自行录入的线索直接归属本人。
    #   市场专员自己录入的线索（转介绍/内部介绍/渠道商等）直接归属本人，
    #   否则要管理者"再分配一次"纯属重复劳动，且线索会先在公海里躺着。
    #   管理人员/管理员录入的仍进公海 —— 他们不"拥有"线索，公海是分配池的来源（1.6 分配管理）。
    auto_own = owner_id is None and user.role == UserRole.MARKET_STAFF
    if owner_id:
        owner = (await db.execute(select(User).where(User.id == owner_id))).scalar_one_or_none()
        if owner is None:
            raise NotFoundError("指定的归属人不存在")
        owner_name = owner.name

    # 客户-项目两层：同名客户自动归集（没有就建），实现"同一客户多个项目商机"
    from app.services import customer_service

    if payload.customer_id:
        customer = (
            await db.execute(select(Customer).where(Customer.id == payload.customer_id))
        ).scalar_one_or_none()
        if customer is None:
            raise NotFoundError("指定的客户不存在")
    else:
        customer, _created = await customer_service.ensure_customer(
            db,
            name=payload.name,
            province=payload.province or "",
            city=payload.city or "",
            district=payload.district or "",
            org_type=payload.org_type,
            owner=None,
            created_from="manual",
        )

    lead = Lead(
        customer_id=customer.id,
        project_name=(payload.project_name or "").strip() or None,
        name=payload.name.strip(),
        source=payload.source,
        province=payload.province or "",
        city=payload.city or "",
        district=payload.district or "",
        org_type=payload.org_type,
        channel_unit=payload.channel_unit,
        referrer=payload.referrer,
        remark=payload.remark,
        creator_id=user.id,
        creator_name=user.name,
        owner_id=owner_id,
        owner_name=owner_name,
        status=LeadStatus.ASSIGNED if owner_id else LeadStatus.UNASSIGNED,
        is_suspect_duplicate=warning["suspect"],
        duplicate_hint=(
            f"疑似重复：已有归属人 {warning['duplicates'][0]['owner_name']}"
            if warning["suspect"]
            else None
        ),
        dedup_status=DedupStatus.PENDING if warning["suspect"] else None,
        assign_at=datetime.now() if owner_id else None,
    )
    db.add(lead)
    await db.flush()

    if auto_own:
        # 复用 change_owner：SLA 计时（1 工作日首跟 / 7 工作日回收）、归属留痕、
        # 操作日志、站内通知都与"管理者分配"走同一条链路，不另写一套
        from app.services import assign_service

        await assign_service.change_owner(
            db,
            lead=lead,
            new_owner_id=user.id,
            operator=user,
            remark="录入即归属本人（无需再分配）",
        )

    # 展会快速录入：顺手把联系人一起建了
    if payload.contact_name:
        db.add(
            Contact(
                lead_id=lead.id,
                name=payload.contact_name,
                phone=payload.contact_phone or "",
                wechat=payload.contact_wechat,
                position=payload.contact_position,
                is_primary=True,
            )
        )

    await audit_service.write_log(
        db,
        operator=user,
        action="create",
        target_type="lead",
        target_id=lead.id,
        target_name=lead.name,
        detail=f"录入线索（来源 {dicts.label_of(dicts.LEAD_SOURCE_LABELS, lead.source)}）"
        + ("，疑似重复" if warning["suspect"] else ""),
    )

    # 分配给本人以外的归属人时，按指派流程设定 SLA
    if owner_id and owner_id != user.id:
        from app.services import assign_service

        await assign_service.setup_owner_sla(
            db, lead, owner_id, operator=user, remark="录入时直接指派"
        )

    return lead, warning


async def update_lead(db: AsyncSession, *, user: User, lead: Lead, payload: LeadUpdate) -> Lead:
    assert_can_edit(user, lead)
    data = payload.model_dump(exclude_unset=True)

    owner_id = data.pop("owner_id", None)
    if owner_id is not None and owner_id != lead.owner_id:
        from app.services import assign_service

        await assign_service.change_owner(
            db, lead=lead, new_owner_id=owner_id or None, operator=user, remark="编辑时变更归属人"
        )

    if "budget_amount_fen" in data and data["budget_amount_fen"] != lead.budget_amount_fen:
        from app.services import stage_service

        await stage_service.change_amount(
            db,
            user=user,
            lead=lead,
            amount_fen=int(data.pop("budget_amount_fen") or 0),
            reason="线索编辑时直接修正预算金额",
        )

    changed: list[str] = []
    for key, value in data.items():
        if value is None and key in {
            "channel_unit",
            "referrer",
            "remark",
            "expected_sign_date",
        }:
            setattr(lead, key, value)
            changed.append(key)
            continue
        if getattr(lead, key, None) != value:
            setattr(lead, key, value)
            changed.append(key)

    if "name" in changed or "province" in changed or "city" in changed or "district" in changed:
        hits = await check_duplicate(
            db,
            name=lead.name,
            province=lead.province,
            city=lead.city,
            district=lead.district,
            exclude_id=lead.id,
        )
        if hits:
            lead.is_suspect_duplicate = True
            lead.duplicate_hint = f"疑似重复：已有归属人 {hits[0].owner_name or '未指派'}"
            if not lead.dedup_status:
                lead.dedup_status = DedupStatus.PENDING

    if changed:
        await audit_service.write_log(
            db,
            operator=user,
            action="update",
            target_type="lead",
            target_id=lead.id,
            target_name=lead.name,
            detail="修改字段：" + "、".join(changed),
        )
    return lead


async def soft_delete(db: AsyncSession, *, user: User, lead: Lead) -> None:
    """软删除（仅管理员，见权限矩阵）。"""
    lead.is_deleted = True
    lead.deleted_at = datetime.now()
    await audit_service.write_log(
        db,
        operator=user,
        action="delete",
        target_type="lead",
        target_id=lead.id,
        target_name=lead.name,
        detail="软删除线索",
    )


async def revive_lead(
    db: AsyncSession, *, user: User, lead: Lead, remark: str | None = None
) -> Lead:
    """无效商机复活（S1-6）：改回跟进中，留操作日志。"""
    if lead.status != LeadStatus.INVALID:
        raise ConflictError("只有无效商机可以复活")
    before = lead.status

    # 复活后重新开始计时；注意顺序：先重置 SLA（会把状态置为"已指派"），
    # 再改回"跟进中"，否则会被 setup_owner_sla 覆盖。
    if lead.owner_id:
        from app.services import assign_service

        await assign_service.setup_owner_sla(
            db, lead, lead.owner_id, operator=user, remark="复活重置 SLA"
        )
        lead.status = LeadStatus.FOLLOWING
    else:
        lead.status = LeadStatus.UNASSIGNED

    await audit_service.write_log(
        db,
        operator=user,
        action="revive",
        target_type="lead",
        target_id=lead.id,
        target_name=lead.name,
        detail=f"状态 {before} → {lead.status}；{remark or ''}",
    )
    return lead


# ══════════════ 疑似重复裁决台 ══════════════


async def list_duplicates(
    db: AsyncSession, *, user: User, offset: int = 0, limit: int = 20
) -> tuple[list[Lead], int]:
    stmt = select(Lead).where(
        Lead.is_deleted.is_(False),
        Lead.is_suspect_duplicate.is_(True),
        or_(Lead.dedup_status.is_(None), Lead.dedup_status == DedupStatus.PENDING),
    )
    if not can_view_all_leads(user):
        stmt = stmt.where(Lead.owner_id == user.id)
    total = int((await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar() or 0)
    rows = (
        (await db.execute(stmt.order_by(Lead.created_at.desc()).offset(offset).limit(limit)))
        .scalars()
        .all()
    )
    return list(rows), total


async def resolve_duplicate(
    db: AsyncSession,
    *,
    user: User,
    keep_id: str,
    merge_id: str | None,
    action: str,
    remark: str | None = None,
) -> None:
    """裁决：keep=判定为独立记录；merge=合并到保留记录。"""
    keep = await get_lead(db, keep_id)
    if action == "merge":
        if not merge_id or merge_id == keep_id:
            raise ConflictError("合并操作必须指定另一条被合并的记录")
        victim = await get_lead(db, merge_id)
        victim.parent_id = keep.id
        victim.dedup_status = DedupStatus.MERGED
        victim.is_deleted = True
        victim.deleted_at = datetime.now()
        # 跟进记录与联系人迁移到保留记录，避免信息丢失
        await db.execute(
            Contact.__table__.update().where(Contact.lead_id == victim.id).values(lead_id=keep.id)
        )
        from app.models.activity import FollowUp

        await db.execute(
            FollowUp.__table__.update().where(FollowUp.lead_id == victim.id).values(lead_id=keep.id)
        )
        # 迁移后可能出现两个主联系人 → 保证"一个客户只有一个主联系人"
        await _dedupe_primary_contact(db, keep.id)
        # 被合并记录若挂着归属人而保留记录没有，则继承过来，避免"合并后没人管"
        if not keep.owner_id and victim.owner_id:
            keep.owner_id = victim.owner_id
            keep.owner_name = victim.owner_name
            keep.status = victim.status
            keep.assign_at = victim.assign_at
            keep.first_follow_deadline = victim.first_follow_deadline
            keep.recycle_deadline = victim.recycle_deadline
        detail = f"合并 {victim.name}({victim.id}) → {keep.name}({keep.id})"
    else:
        detail = "判定为独立记录（保留）"
        action = "keep"

    keep.dedup_status = DedupStatus.KEPT
    keep.is_suspect_duplicate = False
    keep.duplicate_hint = None

    await audit_service.write_log(
        db,
        operator=user,
        action=f"dedup_{action}",
        target_type="lead",
        target_id=keep.id,
        target_name=keep.name,
        detail=f"{detail}；备注：{remark or '无'}",
    )


# ══════════════ 联系人 ══════════════


async def _dedupe_primary_contact(db: AsyncSession, lead_id: str) -> None:
    """保证一个客户最多一个主联系人：保留最早的那个，其余置为否。"""
    rows = (
        (
            await db.execute(
                select(Contact)
                .where(Contact.lead_id == lead_id)
                .order_by(Contact.created_at.asc(), Contact.id.asc())
            )
        )
        .scalars()
        .all()
    )
    primaries = [c for c in rows if c.is_primary]
    if len(primaries) > 1:
        for extra in primaries[1:]:
            extra.is_primary = False
    elif not primaries and rows:
        rows[0].is_primary = True


async def list_contacts(db: AsyncSession, lead_id: str) -> list[Contact]:
    rows = (
        (
            await db.execute(
                select(Contact)
                .where(Contact.lead_id == lead_id)
                .order_by(Contact.is_primary.desc())
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


async def add_contact(
    db: AsyncSession, *, user: User, lead: Lead, payload: ContactCreate
) -> Contact:
    if payload.is_primary:
        await _clear_primary(db, lead.id)
    contact = Contact(lead_id=lead.id, **payload.model_dump())
    db.add(contact)
    await db.flush()
    await audit_service.write_log(
        db,
        operator=user,
        action="create",
        target_type="contact",
        target_id=contact.id,
        target_name=f"{lead.name}-{contact.name}",
        detail="新增联系人",
    )
    return contact


async def update_contact(
    db: AsyncSession, *, user: User, contact: Contact, payload: ContactUpdate
) -> Contact:
    data = payload.model_dump(exclude_unset=True)
    if data.get("is_primary"):
        await _clear_primary(db, contact.lead_id)
    for key, value in data.items():
        setattr(contact, key, value)
    await audit_service.write_log(
        db,
        operator=user,
        action="update",
        target_type="contact",
        target_id=contact.id,
        target_name=contact.name,
        detail="修改联系人",
    )
    return contact


async def delete_contact(db: AsyncSession, *, user: User, contact: Contact) -> None:
    await audit_service.write_log(
        db,
        operator=user,
        action="delete",
        target_type="contact",
        target_id=contact.id,
        target_name=contact.name,
        detail="删除联系人",
    )
    await db.delete(contact)


async def _clear_primary(db: AsyncSession, lead_id: str) -> None:
    rows = (await db.execute(select(Contact).where(Contact.lead_id == lead_id))).scalars().all()
    for row in rows:
        row.is_primary = False


async def get_contact(db: AsyncSession, contact_id: str) -> Contact:
    contact = (
        await db.execute(select(Contact).where(Contact.id == contact_id))
    ).scalar_one_or_none()
    if contact is None:
        raise NotFoundError("联系人不存在")
    return contact


# ══════════════ 看板数据源 ══════════════


async def source_distribution(db: AsyncSession, start: datetime, end: datetime) -> list[dict]:
    """来源分布与有效率（线索看板 4.2）。"""
    rows = (
        await db.execute(
            select(Lead.source, Lead.status, func.count())
            .where(Lead.is_deleted.is_(False), Lead.created_at.between(start, end))
            .group_by(Lead.source, Lead.status)
        )
    ).all()
    agg: dict[str, dict] = {}
    for source, status, count in rows:
        item = agg.setdefault(source, {"source": source, "count": 0, "valid_count": 0})
        item["count"] += count
        if status in (LeadStatus.VALID, LeadStatus.SIGNED):
            item["valid_count"] += count
    result = []
    for source, item in agg.items():
        item["label"] = dicts.label_of(dicts.LEAD_SOURCE_LABELS, source)
        item["valid_rate"] = round(item["valid_count"] / item["count"], 4) if item["count"] else 0.0
        result.append(item)
    result.sort(key=lambda x: x["count"], reverse=True)
    return result


async def status_funnel(db: AsyncSession, start: datetime, end: datetime) -> list[dict]:
    rows = (
        await db.execute(
            select(Lead.status, func.count())
            .where(Lead.is_deleted.is_(False), Lead.created_at.between(start, end))
            .group_by(Lead.status)
        )
    ).all()
    counts = {r[0]: r[1] for r in rows}
    order = [
        LeadStatus.UNASSIGNED,
        LeadStatus.ASSIGNED,
        LeadStatus.FOLLOWING,
        LeadStatus.VALID,
        LeadStatus.INVALID,
        LeadStatus.LOST,
        LeadStatus.SIGNED,
    ]
    return [
        {
            "status": s,
            "label": dicts.label_of(dicts.LEAD_STATUS_LABELS, s),
            "count": counts.get(s, 0),
        }
        for s in order
    ]


async def per_person_output(db: AsyncSession, start: datetime, end: datetime) -> list[dict]:
    """人均产出：新增线索 / 有效商机 / 有效率。"""
    rows = (
        await db.execute(
            select(Lead.owner_id, Lead.owner_name, Lead.status, func.count())
            .where(
                Lead.is_deleted.is_(False),
                Lead.created_at.between(start, end),
                Lead.owner_id.is_not(None),
            )
            .group_by(Lead.owner_id, Lead.owner_name, Lead.status)
        )
    ).all()
    agg: dict[str, dict] = {}
    for owner_id, owner_name, status, count in rows:
        item = agg.setdefault(
            owner_id,
            {
                "user_id": owner_id,
                "user_name": owner_name or "",
                "new_lead_count": 0,
                "valid_count": 0,
            },
        )
        item["new_lead_count"] += count
        if status in (LeadStatus.VALID, LeadStatus.SIGNED):
            item["valid_count"] += count
    result = list(agg.values())
    for item in result:
        item["valid_rate"] = (
            round(item["valid_count"] / item["new_lead_count"], 4)
            if item["new_lead_count"]
            else 0.0
        )
    result.sort(key=lambda x: x["valid_count"], reverse=True)
    return result


async def follow_timeliness(db: AsyncSession, start: datetime, end: datetime) -> dict:
    """跟进时效：平均首次跟进时长、按时率、超时数。"""
    rows = (
        await db.execute(
            select(Lead.assign_at, Lead.first_follow_at, Lead.first_follow_deadline).where(
                Lead.is_deleted.is_(False),
                Lead.assign_at.is_not(None),
                Lead.assign_at.between(start, end),
            )
        )
    ).all()
    if not rows:
        return {"avg_first_follow_hours": 0, "on_time_rate": 0, "overdue_count": 0, "total": 0}

    total_hours = 0.0
    followed = 0
    on_time = 0
    overdue = 0
    for assign_at, first_follow_at, deadline in rows:
        if first_follow_at:
            followed += 1
            total_hours += (first_follow_at - assign_at).total_seconds() / 3600
            if not deadline or first_follow_at <= deadline:
                on_time += 1
        elif deadline and datetime.now() > deadline:
            overdue += 1
    return {
        "avg_first_follow_hours": round(total_hours / followed, 2) if followed else 0,
        "on_time_rate": round(on_time / followed, 4) if followed else 0.0,
        "overdue_count": overdue,
        "total": len(rows),
    }


# ══════════════ 闭环衔接：有效商机 = 环节一 ══════════════


async def mark_as_opportunity(db: AsyncSession, *, lead: Lead, user: User) -> None:
    """【编排 O1】有效性验证通过 → 初始化闭环二环节一。

    域间只有两次交接，这是第一次；此处是"线索域 → 商机域"的唯一入口。
    """
    from app.core.dicts import STAGE_WIN_PROBABILITY

    lead.status = LeadStatus.VALID
    lead.valid_at = datetime.now()
    lead.current_stage = Stage.S1_VALID_OPPORTUNITY
    lead.stage_entered_at = datetime.now()
    lead.win_probability = STAGE_WIN_PROBABILITY[Stage.S1_VALID_OPPORTUNITY]
    # 进入闭环二后不再自动回收
    lead.recycle_deadline = None
    lead.first_follow_deadline = None

    from app.models.activity import Reminder
    from app.models.stage import StageTransition

    old_reminders = (
        (
            await db.execute(
                select(Reminder).where(
                    Reminder.lead_id == lead.id,
                    Reminder.status == "pending",
                    Reminder.type.in_(
                        ["first_follow_due", "first_follow_overdue", "recycle_due", "recycled"]
                    ),
                )
            )
        )
        .scalars()
        .all()
    )
    for reminder in old_reminders:
        reminder.status, reminder.done_at, reminder.done_by = "done", datetime.now(), user.id

    db.add(
        StageTransition(
            lead_id=lead.id,
            from_stage=None,
            to_stage=Stage.S1_VALID_OPPORTUNITY,
            transition_at=datetime.now(),
            operator_id=user.id,
            operator_name=user.name,
            stay_days=0,
            basis="有效性验证通过：客户信息正确且客户有意愿",
        )
    )


async def mark_invalid(db: AsyncSession, *, lead: Lead, user: User) -> None:
    lead.status = LeadStatus.INVALID
    lead.valid_at = datetime.now()
    lead.recycle_deadline = None
    lead.first_follow_deadline = None


async def pending_reminder_count(db: AsyncSession, user: User) -> int:
    from app.models.activity import Reminder

    return int(
        (
            await db.execute(
                select(func.count())
                .select_from(Reminder)
                .where(Reminder.receiver_id == user.id, Reminder.status == ReminderStatus.PENDING)
            )
        ).scalar()
        or 0
    )


__all__ = [
    "add_contact",
    "check_duplicate",
    "create_lead",
    "delete_contact",
    "duplicate_warning",
    "follow_timeliness",
    "get_contact",
    "get_lead",
    "list_contacts",
    "list_duplicates",
    "list_leads",
    "mark_as_opportunity",
    "mark_invalid",
    "per_person_output",
    "resolve_duplicate",
    "revive_lead",
    "soft_delete",
    "source_distribution",
    "status_funnel",
    "update_contact",
    "update_lead",
    "and_",
]
