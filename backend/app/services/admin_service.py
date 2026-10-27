"""系统设置服务（收尾页 6.2 角色与权限 / 6.3 部门管理 / 6.8 通知模板 / 6.10 适配配置）。

口径说明：
- **权限矩阵不重复定义**：能力项与其允许角色集中在 `app/core/permissions.py`，那里直接引用
  各 API 真实使用的角色集合常量。本模块只负责把矩阵与"实际有多少账号在用"拼起来。
- **部门**：`User.department` 是部门名字符串（既有数据），本模块按名字匹配统计人数；
  不引入 FK，避免破坏既有数据。
- **通知模板**：站内通知已在业务事件里发出，这里把标题/正文话术外置为可配置模板。
  IM（企微/钉钉/飞书）通道为预留，`channel` 先只落 `inapp`。
- **适配配置**：只做**只读状态投影**（模型 provider / IM / 嵌入 / 知识库 / 上传），
  配置源仍是环境变量与 `SETTINGS.yaml`，页面不写回（避免两处配置打架）。
"""

from __future__ import annotations

import logging
import re
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core import dicts, permissions
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.models.enums import UserRole
from app.models.system import NotificationTemplate
from app.models.user import Department, User
from app.schemas import DepartmentUpdate, NotificationTemplateCreate, NotificationTemplateUpdate

logger = logging.getLogger(__name__)

#: 模板变量占位：{name}
_TPL_VAR_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


# ══════════════ 6.2 角色与权限 ══════════════


async def role_matrix_payload(db: AsyncSession) -> dict[str, Any]:
    """能力矩阵 + 角色清单 + 各角色实际账号数。"""
    counts = dict((await db.execute(select(User.role, func.count()).group_by(User.role))).all())
    scope_counts = dict(
        (await db.execute(select(User.data_scope, func.count()).group_by(User.data_scope))).all()
    )

    roles = []
    for item in permissions.role_summary():
        role = item["role"]
        roles.append({**item, "user_count": int(counts.get(role, 0))})

    return {
        "roles": roles,
        "capabilities": permissions.role_matrix(),
        "role_columns": [
            {
                "role": role.value,
                "label": dicts.label_of(dicts.ROLE_LABELS, role.value),
                "user_count": int(counts.get(role.value, 0)),
            }
            for role in UserRole
        ],
        "scope_summary": [
            {
                "scope": scope,
                "label": label,
                "user_count": int(scope_counts.get(scope, 0)),
            }
            for scope, label in dicts.DATA_SCOPE_LABELS.items()
        ],
        "total_users": sum(int(v) for v in counts.values()),
        "note": (
            "矩阵直接取自各接口真实的角色拦截常量"
            "（app/core/permissions.py 引用 deps.py 与各 API），"
            "不是另写一份文档。改权限请改拦截逻辑后刷新本页。"
        ),
    }


# ══════════════ 6.3 部门管理 ══════════════


async def _department_user_counts(db: AsyncSession) -> dict[str, int]:
    """按部门名统计在职人数（User.department 是名字符串）。"""
    rows = (
        await db.execute(
            select(User.department, func.count())
            .where(User.department.is_not(None), User.is_active.is_(True))
            .group_by(User.department)
        )
    ).all()
    return {str(name): int(count) for name, count in rows if name}


def _department_out(row: Department, user_count: int, children: list[dict]) -> dict:
    return {
        "id": row.id,
        "name": row.name,
        "parent_id": row.parent_id,
        "sort_order": row.sort_order,
        "leader_name": row.leader_name,
        "remark": row.remark,
        "user_count": user_count,
        "children": children,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


async def department_tree(db: AsyncSession) -> dict[str, Any]:
    """部门树（含人数）。根节点为 parent_id 为空的部门。"""
    rows = list(
        (await db.execute(select(Department).order_by(Department.sort_order, Department.name)))
        .scalars()
        .all()
    )
    counts = await _department_user_counts(db)
    by_parent: dict[str | None, list[Department]] = {}
    for row in rows:
        by_parent.setdefault(row.parent_id, []).append(row)

    def build(parent_id: str | None) -> list[dict]:
        return [
            _department_out(row, counts.get(row.name, 0), build(row.id))
            for row in by_parent.get(parent_id, [])
        ]

    tree = build(None)
    unmatched = sorted(name for name in counts if name not in {row.name for row in rows})
    return {
        "items": tree,
        "total": len(rows),
        "total_users": sum(counts.values()),
        "unmatched_departments": unmatched,
        "note": (
            "账号的部门是名字符串（既有数据口径）；上方列出「有账号但未建部门」的名字，可据此补建。"
        ),
    }


async def get_department(db: AsyncSession, department_id: str) -> Department:
    row = (
        await db.execute(select(Department).where(Department.id == department_id))
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError("部门不存在")
    return row


async def update_department(
    db: AsyncSession, *, department: Department, payload: DepartmentUpdate
) -> Department:
    data = payload.model_dump(exclude_unset=True)
    if "name" in data and data["name"] and data["name"] != department.name:
        exists = (
            await db.execute(select(Department).where(Department.name == data["name"]))
        ).scalar_one_or_none()
        if exists is not None:
            raise ConflictError("部门名称已存在")
        # 部门改名 → 账号上的部门名同步，避免出现"孤儿部门"
        await db.execute(
            User.__table__.update()
            .where(User.department == department.name)
            .values(department=data["name"])
        )
    if data.get("parent_id") == department.id:
        raise ValidationError("不能把部门挂到自己下面")
    if data.get("parent_id"):
        parent = await get_department(db, data["parent_id"])
        if await _is_descendant(db, candidate_parent_id=parent.id, department_id=department.id):
            raise ValidationError("不能把部门挂到自己的下级部门下面")
    for key, value in data.items():
        setattr(department, key, value)
    await db.flush()
    return department


async def _is_descendant(db: AsyncSession, *, candidate_parent_id: str, department_id: str) -> bool:
    """候选父节点是否是本部门的后代（防止成环）。"""
    cursor: str | None = candidate_parent_id
    seen: set[str] = set()
    while cursor and cursor not in seen:
        if cursor == department_id:
            return True
        seen.add(cursor)
        row = (
            await db.execute(select(Department.parent_id).where(Department.id == cursor))
        ).scalar_one_or_none()
        cursor = row
    return False


async def delete_department(db: AsyncSession, department: Department) -> None:
    """删除部门：有下级或仍有账号归属时拒绝（避免产生孤儿数据）。"""
    children = int(
        (
            await db.execute(
                select(func.count())
                .select_from(Department)
                .where(Department.parent_id == department.id)
            )
        ).scalar()
        or 0
    )
    if children:
        raise ConflictError(f"该部门下还有 {children} 个子部门，请先处理子部门")
    user_count = int(
        (
            await db.execute(
                select(func.count()).select_from(User).where(User.department == department.name)
            )
        ).scalar()
        or 0
    )
    if user_count:
        raise ConflictError(f"该部门下还有 {user_count} 个账号，请先调整账号部门")
    await db.delete(department)
    await db.flush()


# ══════════════ 6.8 通知模板 ══════════════


def template_variables(*texts: str | None) -> list[str]:
    """从模板文案里抽出变量名（去重、保序）。"""
    names: list[str] = []
    for text in texts:
        for match in _TPL_VAR_RE.findall(text or ""):
            if match not in names:
                names.append(match)
    return names


def render_template_text(text: str | None, variables: dict[str, str]) -> str:
    """渲染模板：缺失变量填空串（不抛错），便于业务侧逐步补齐参数。"""

    def replace(match: re.Match[str]) -> str:
        return str(variables.get(match.group(1), ""))

    return _TPL_VAR_RE.sub(replace, text or "")


def template_out(row: NotificationTemplate) -> dict:
    return {
        "id": row.id,
        "code": row.code,
        "name": row.name,
        "channel": row.channel,
        "channel_label": dicts.label_of(dicts.NOTIFY_CHANNEL_LABELS, row.channel),
        "event": row.event,
        "event_label": dicts.label_of(dicts.NOTIFY_EVENT_LABELS, row.event) if row.event else "",
        "title_tpl": row.title_tpl,
        "content_tpl": row.content_tpl,
        "variables": template_variables(row.title_tpl, row.content_tpl),
        # 该事件的模板是否真的影响发送（前端不再硬编码清单，避免与后端漂移）
        "wired": (row.event or "") in dicts.WIRED_NOTIFY_EVENTS,
        "wired_note": (
            "改动即时生效，会影响真实站内通知"
            if (row.event or "") in dicts.WIRED_NOTIFY_EVENTS
            else dicts.RESERVED_NOTIFY_EVENTS.get(
                row.event or "", "该事件当前不发送站内通知，模板保存后暂不影响发送"
            )
        ),
        "is_active": row.is_active,
        "remark": row.remark,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


async def list_templates(
    db: AsyncSession, *, channel: str | None = None, event: str | None = None
) -> list[NotificationTemplate]:
    stmt = select(NotificationTemplate)
    if channel:
        stmt = stmt.where(NotificationTemplate.channel == channel)
    if event:
        stmt = stmt.where(NotificationTemplate.event == event)
    return list((await db.execute(stmt.order_by(NotificationTemplate.code))).scalars().all())


async def get_template(db: AsyncSession, template_id: str) -> NotificationTemplate:
    row = (
        await db.execute(select(NotificationTemplate).where(NotificationTemplate.id == template_id))
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError("通知模板不存在")
    return row


async def create_template(
    db: AsyncSession, *, payload: NotificationTemplateCreate
) -> NotificationTemplate:
    exists = (
        await db.execute(
            select(NotificationTemplate).where(NotificationTemplate.code == payload.code)
        )
    ).scalar_one_or_none()
    if exists is not None:
        raise ConflictError("模板编码已存在")
    row = NotificationTemplate(**payload.model_dump())
    db.add(row)
    await db.flush()
    return row


async def update_template(
    db: AsyncSession, *, row: NotificationTemplate, payload: NotificationTemplateUpdate
) -> NotificationTemplate:
    for key, value in payload.model_dump(exclude_unset=True).items():
        if value is not None:
            setattr(row, key, value)
    await db.flush()
    return row


# ══════════════ 6.10 适配配置（只读状态投影）══════════════


def adapter_status() -> dict[str, Any]:
    """适配配置状态：模型 / IM / 嵌入 / 知识库 / 上传。

    只读：配置源是环境变量与 `backend/SETTINGS.yaml`，页面不写回，避免两处配置打架。
    """
    settings = get_settings()
    from app.ai.runtime import describe_ai

    ai = describe_ai()
    im_adapters = [
        {
            "code": "wecom",
            "label": "企业微信",
            "configured": bool(getattr(settings, "WECOM_WEBHOOK_URL", "")),
            "env": "WECOM_WEBHOOK_URL",
        },
        {
            "code": "dingtalk",
            "label": "钉钉",
            "configured": bool(getattr(settings, "DINGTALK_WEBHOOK_URL", "")),
            "env": "DINGTALK_WEBHOOK_URL",
        },
        {
            "code": "feishu",
            "label": "飞书",
            "configured": bool(getattr(settings, "FEISHU_WEBHOOK_URL", "")),
            "env": "FEISHU_WEBHOOK_URL",
        },
    ]
    return {
        "model": {
            "provider": ai.get("provider"),
            "settings_file": ai.get("settings_file"),
            "profiles": ai.get("profiles"),
            "configured": ai.get("enabled", False),
            "degrade_policy": ai.get("degrade_policy"),
            "note": ai.get("note") or "模型别名 extract / diagnose / compose 由 SETTINGS.yaml 定义",
        },
        "im_adapters": im_adapters,
        "im_ready": any(item["configured"] for item in im_adapters),
        "embedding": {
            "configured": bool(settings.DASHSCOPE_API_KEY),
            "model": settings.EMBEDDING_MODEL,
            "dimension": settings.EMBEDDING_DIMENSION,
        },
        "knowledge_base": {
            "configured": bool(settings.DASHSCOPE_API_KEY),
            "collection": settings.CHROMA_COLLECTION,
            "persist_dir": settings.CHROMA_PERSIST_DIR,
            "retrieval_top_k": settings.RETRIEVAL_TOP_K,
        },
        "upload": {
            "max_size_mb": settings.MAX_UPLOAD_SIZE_MB,
            "extensions": settings.allowed_upload_extension_list,
        },
        "note": (
            "本页只读：模型配置在 backend/SETTINGS.yaml（provider 命名空间 + ${ENV.*} 占位），"
            "IM / 嵌入 / 知识库配置在 backend/.env。改配置请改文件后重启后端。"
        ),
    }
