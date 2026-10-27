"""系统接口：字典、健康检查、全局搜索、节假日维护、用户与部门、操作日志。"""

from __future__ import annotations

from datetime import date, datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import dicts
from app.core.deps import get_current_user, require_admin
from app.core.errors import ConflictError, NotFoundError, PermissionDeniedError, ValidationError
from app.core.security import hash_password
from app.database import get_db
from app.models.enums import HolidayKind, UserRole
from app.models.lead import Contact, Lead
from app.models.system import Holiday, OperationLog
from app.models.user import Department, User
from app.schemas import (
    DepartmentCreate,
    DepartmentUpdate,
    ResetPasswordRequest,
    UserCreate,
    UserUpdate,
)
from app.serializers import contact_out, lead_out, log_out, user_out
from app.services import admin_service, audit_service

router = APIRouter(tags=["系统"])


# ══════════════ 字典 ══════════════

# 省份（用于录入时的下拉建议；允许自由输入其他值）
PROVINCES = [
    "北京市",
    "天津市",
    "上海市",
    "重庆市",
    "河北省",
    "山西省",
    "辽宁省",
    "吉林省",
    "黑龙江省",
    "江苏省",
    "浙江省",
    "安徽省",
    "福建省",
    "江西省",
    "山东省",
    "河南省",
    "湖北省",
    "湖南省",
    "广东省",
    "海南省",
    "四川省",
    "贵州省",
    "云南省",
    "陕西省",
    "甘肃省",
    "青海省",
    "台湾省",
    "内蒙古自治区",
    "广西壮族自治区",
    "西藏自治区",
    "宁夏回族自治区",
    "新疆维吾尔自治区",
    "香港特别行政区",
    "澳门特别行政区",
]


@router.get("/dicts", summary="全部字典（下拉用）")
async def get_dicts(user: User = Depends(get_current_user)) -> dict:
    return {
        "lead_sources": dicts.options(dicts.LEAD_SOURCE_LABELS),
        "org_types": dicts.options(dicts.ORG_TYPE_LABELS),
        "lead_status": dicts.options(dicts.LEAD_STATUS_LABELS),
        "follow_methods": dicts.options(dicts.FOLLOW_METHOD_LABELS),
        "next_actions": dicts.options(dicts.NEXT_ACTION_LABELS),
        "risk_types": dicts.options(dicts.RISK_TYPE_LABELS),
        "reminder_types": dicts.options(dicts.REMINDER_TYPE_LABELS),
        "stages": dicts.options(dicts.STAGE_LABELS),
        "roles": dicts.options(dicts.ROLE_LABELS),
        "data_scopes": dicts.options(dicts.DATA_SCOPE_LABELS),
        "assignment_actions": dicts.options(dicts.ASSIGNMENT_ACTION_LABELS),
        "dedup_status": dicts.options(dicts.DEDUP_STATUS_LABELS),
        "risk_status": dicts.options(dicts.RISK_STATUS_LABELS),
        "alert_kinds": dicts.options(dicts.ALERT_KIND_LABELS),
        "review_items": dicts.REVIEW_ITEMS,
        "review_verdicts": dicts.options(dicts.REVIEW_VERDICT_LABELS),
        "review_results": dicts.options(dicts.REVIEW_RESULT_LABELS),
        "payment_methods": dicts.options(dicts.PAYMENT_METHODS),
        "payment_periods": dicts.options(dicts.PAYMENT_PERIODS),
        # ── 闭环三（P2）──
        "contract_status": dicts.options(dicts.CONTRACT_STATUS_LABELS),
        "operation_action_types": dicts.options(dicts.OPERATION_ACTION_LABELS),
        "record_status": dicts.options(dicts.RECORD_STATUS_LABELS),
        "metric_period_types": dicts.options(dicts.METRIC_PERIOD_LABELS),
        "issue_types": dicts.options(dicts.ISSUE_TYPE_LABELS),
        "issue_status": dicts.options(dicts.ISSUE_STATUS_LABELS),
        "handler_sides": dicts.options(dicts.HANDLER_SIDE_LABELS),
        "issue_sources": dicts.options(dicts.ISSUE_SOURCE_LABELS),
        "complaint_reasons": dicts.options(dicts.COMPLAINT_REASON_LABELS),
        "complaint_status": dicts.options(dicts.COMPLAINT_STATUS_LABELS),
        "acceptance_status": dicts.options(dicts.ACCEPTANCE_STATUS_LABELS),
        "renewal_status": dicts.options(dicts.RENEWAL_STATUS_LABELS),
        "renewal_willingness": dicts.options(dicts.RENEWAL_WILLINGNESS_LABELS),
        "alert_domains": dicts.options(dicts.ALERT_DOMAIN_LABELS),
        "delivery_phases": [
            {"value": str(phase), "label": label}
            for phase, label in dicts.DELIVERY_PHASE_LABELS.items()
        ],
        "metrics": [
            {"value": "valid_lead_count", "label": "有效商机数"},
            {"value": "new_lead_count", "label": "新增线索数"},
            {"value": "valid_rate", "label": "有效率"},
            {"value": "sign_amount", "label": "签单金额"},
            {"value": "paid_amount", "label": "回款金额"},
        ],
        "provinces": PROVINCES,
        "stage_baseline": {
            "duration_days": {k: v for k, v in dicts.STAGE_DURATION_DAYS.items()},
            "win_probability": {k: v for k, v in dicts.STAGE_WIN_PROBABILITY.items()},
            "conversion_baseline": dicts.CONVERSION_BASELINE,
            "total_days": sum(dicts.STAGE_DURATION_DAYS.values()),
        },
    }


@router.get("/search", summary="全局搜索：客户 / 联系人")
async def global_search(
    keyword: str = Query(min_length=1),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    kw = f"%{keyword.strip()}%"
    leads = (
        (
            await db.execute(
                select(Lead)
                .where(Lead.is_deleted.is_(False), Lead.name.like(kw))
                .order_by(Lead.updated_at.desc())
                .limit(20)
            )
        )
        .scalars()
        .all()
    )

    contacts = (
        (
            await db.execute(
                select(Contact)
                .where(or_(Contact.name.like(kw), Contact.phone.like(kw), Contact.wechat.like(kw)))
                .limit(20)
            )
        )
        .scalars()
        .all()
    )

    return {
        "leads": [lead_out(x) for x in leads],
        "contacts": [contact_out(c) for c in contacts],
    }


# ══════════════ 节假日（闭环一 SLA 依赖）══════════════


class HolidayItem(BaseModel):
    day: date
    kind: str = HolidayKind.HOLIDAY
    name: str | None = None


class HolidayImport(BaseModel):
    items: list[HolidayItem] = Field(min_length=1)
    replace_year: int | None = None


@router.get("/holidays", summary="节假日列表")
async def list_holidays(
    year: int | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    stmt = select(Holiday).order_by(Holiday.day)
    if year:
        stmt = stmt.where(func.strftime("%Y", Holiday.day) == str(year))
    rows = (await db.execute(stmt)).scalars().all()
    return {
        "items": [
            {
                "id": r.id,
                "day": r.day.isoformat(),
                "kind": r.kind,
                "kind_label": dicts.label_of(dicts.HOLIDAY_KIND_LABELS, r.kind),
                "name": r.name,
            }
            for r in rows
        ],
        "total": len(rows),
    }


@router.post("/holidays/import", summary="批量导入/覆盖节假日（管理员）")
async def import_holidays(
    payload: HolidayImport,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    if payload.replace_year:
        old = (
            (
                await db.execute(
                    select(Holiday).where(
                        func.strftime("%Y", Holiday.day) == str(payload.replace_year)
                    )
                )
            )
            .scalars()
            .all()
        )
        for row in old:
            await db.delete(row)

    created = 0
    for item in payload.items:
        existing = (
            await db.execute(select(Holiday).where(Holiday.day == item.day))
        ).scalar_one_or_none()
        if existing:
            existing.kind = item.kind
            existing.name = item.name
        else:
            db.add(Holiday(day=item.day, kind=item.kind, name=item.name))
            created += 1
    await audit_service.write_log(
        db,
        operator=user,
        action="import",
        target_type="holiday",
        detail=f"导入节假日 {len(payload.items)} 条",
    )
    return {"ok": True, "created": created, "total": len(payload.items)}


# ══════════════ 用户与部门 ══════════════


@router.get("/users", summary="用户列表（管理员 / 市场部管理 / 管理层只读）")
async def list_users(
    keyword: str | None = None,
    role: str | None = None,
    is_active: bool | None = None,
    page: int = 1,
    page_size: int = 20,
    _: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    # 分配线索、设目标都需要人员列表；写操作在下面单独限管理员
    if _.role not in {UserRole.ADMIN, UserRole.MARKET_MANAGER, UserRole.EXECUTIVE}:
        raise PermissionDeniedError("当前角色无权查看用户列表，请使用 /users/options")
    stmt = select(User)
    if keyword:
        kw = f"%{keyword.strip()}%"
        stmt = stmt.where(or_(User.name.like(kw), User.username.like(kw)))
    if role:
        stmt = stmt.where(User.role == role)
    if is_active is not None:
        stmt = stmt.where(User.is_active.is_(is_active))
    total = int((await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar() or 0)
    rows = (
        (
            await db.execute(
                stmt.order_by(User.created_at).offset((page - 1) * page_size).limit(page_size)
            )
        )
        .scalars()
        .all()
    )
    return {
        "items": [user_out(u) for u in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/users/options", summary="用户下拉（指派/设目标用，所有登录用户可读）")
async def user_options(
    roles: str | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    stmt = select(User).where(User.is_active.is_(True))
    if roles:
        stmt = stmt.where(User.role.in_([r.strip() for r in roles.split(",") if r.strip()]))
    rows = (await db.execute(stmt.order_by(User.name))).scalars().all()
    return {
        "items": [
            {
                "id": u.id,
                "name": u.name,
                "username": u.username,
                "role": u.role,
                "role_label": dicts.label_of(dicts.ROLE_LABELS, u.role),
                "department": u.department,
            }
            for u in rows
        ],
        "total": len(rows),
    }


@router.post("/users", summary="新建用户（管理员）")
async def create_user(
    payload: UserCreate,
    operator: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    exists = (
        await db.execute(select(User).where(User.username == payload.username))
    ).scalar_one_or_none()
    if exists:
        raise ConflictError("登录名已存在")
    if payload.role not in {r.value for r in UserRole}:
        raise ValidationError("角色不合法")
    user = User(
        username=payload.username,
        name=payload.name,
        password_hash=hash_password(payload.password),
        role=payload.role,
        data_scope=payload.data_scope,
        department=payload.department,
        phone=payload.phone,
        email=payload.email,
    )
    db.add(user)
    await db.flush()
    await audit_service.write_log(
        db,
        operator=operator,
        action="create",
        target_type="user",
        target_id=user.id,
        target_name=user.name,
    )
    return user_out(user)


@router.patch("/users/{user_id}", summary="编辑用户（管理员）")
async def update_user(
    user_id: str,
    payload: UserUpdate,
    operator: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if user is None:
        raise NotFoundError("用户不存在")
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(user, key, value)
    await audit_service.write_log(
        db,
        operator=operator,
        action="update",
        target_type="user",
        target_id=user.id,
        target_name=user.name,
    )
    return user_out(user)


@router.post("/users/{user_id}/reset-password", summary="重置密码（管理员）")
async def reset_password(
    user_id: str,
    payload: ResetPasswordRequest,
    operator: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if user is None:
        raise NotFoundError("用户不存在")
    user.password_hash = hash_password(payload.new_password)
    await audit_service.write_log(
        db, operator=operator, action="reset_password", target_type="user", target_id=user.id
    )
    return {"ok": True}


@router.get("/departments", summary="6.3 部门树（含人数）")
async def list_departments(
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db, scope="function")
) -> dict:
    from app.services import admin_service

    return await admin_service.department_tree(db)


@router.post("/departments", summary="新建部门（管理员）")
async def create_department(
    payload: DepartmentCreate,
    operator: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    exists = (
        await db.execute(select(Department).where(Department.name == payload.name))
    ).scalar_one_or_none()
    if exists:
        raise ConflictError("部门已存在")
    if payload.parent_id:
        await admin_service.get_department(db, payload.parent_id)  # 校验父部门存在
    dept = Department(**payload.model_dump())
    db.add(dept)
    await db.flush()
    return await admin_service.department_tree(db)


@router.patch("/departments/{department_id}", summary="编辑部门（改名会同步账号的部门名）")
async def update_department(
    department_id: str,
    payload: DepartmentUpdate,
    operator: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    department = await admin_service.get_department(db, department_id)
    await admin_service.update_department(db, department=department, payload=payload)
    await audit_service.write_log(
        db,
        operator=operator,
        action="update_department",
        target_type="department",
        target_id=department.id,
        target_name=department.name,
        detail=f"编辑部门：{payload.model_dump(exclude_unset=True)}",
    )
    return await admin_service.department_tree(db)


@router.delete("/departments/{department_id}", summary="删除部门（有下级或有人时拒绝）")
async def delete_department(
    department_id: str,
    operator: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    department = await admin_service.get_department(db, department_id)
    name = department.name
    await admin_service.delete_department(db, department)
    await audit_service.write_log(
        db,
        operator=operator,
        action="delete_department",
        target_type="department",
        target_id=department_id,
        target_name=name,
        detail="删除部门",
    )
    return await admin_service.department_tree(db)


# ══════════════ 操作日志 ══════════════


@router.get("/logs", summary="操作日志（管理员）")
async def list_logs(
    action: str | None = None,
    operator_id: str | None = None,
    target_type: str | None = None,
    page: int = 1,
    page_size: int = 20,
    _: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    stmt = select(OperationLog)
    if action:
        stmt = stmt.where(OperationLog.action == action)
    if operator_id:
        stmt = stmt.where(OperationLog.operator_id == operator_id)
    if target_type:
        stmt = stmt.where(OperationLog.target_type == target_type)
    total = int((await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar() or 0)
    rows = (
        (
            await db.execute(
                stmt.order_by(OperationLog.created_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .all()
    )
    return {
        "items": [log_out(r) for r in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/logs/actions", summary="日志动作枚举")
async def log_actions(user: User = Depends(get_current_user)) -> dict:
    return {
        "items": [
            {"value": "create", "label": "新增"},
            {"value": "update", "label": "修改"},
            {"value": "delete", "label": "删除"},
            {"value": "import", "label": "导入"},
            {"value": "export", "label": "导出"},
            {"value": "assign", "label": "指派"},
            {"value": "reassign", "label": "改派"},
            {"value": "recycle", "label": "回收"},
            {"value": "claim", "label": "公海领取"},
            {"value": "followup", "label": "跟进"},
            {"value": "ai_draft_confirm", "label": "AI 草稿确认"},
            {"value": "ai_diagnose", "label": "AI 诊断"},
        ]
    }


@router.get("/server-time", summary="服务器时间（前端排查时区用）")
async def server_time(user: User = Depends(get_current_user)) -> dict:
    now = datetime.now()
    return {"now": now.isoformat(), "date": now.date().isoformat(), "tz": "Asia/Shanghai"}
