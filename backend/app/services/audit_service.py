"""审计与通知服务：所有导出/删除/改派/批量修改都留痕。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.system import Notification, OperationLog
from app.models.user import User


async def write_log(
    db: AsyncSession,
    *,
    operator: User,
    action: str,
    target_type: str,
    target_id: str | None = None,
    target_name: str | None = None,
    detail: str | None = None,
    ip: str | None = None,
) -> OperationLog:
    """写一条操作日志（不 commit，由调用方事务统一提交）。"""
    log = OperationLog(
        operator_id=operator.id,
        operator_name=operator.name,
        action=action,
        target_type=target_type,
        target_id=target_id,
        target_name=target_name,
        detail=detail,
        ip=ip,
    )
    db.add(log)
    return log


async def render_event_text(
    db: AsyncSession,
    *,
    event: str | None,
    title: str,
    content: str,
    context: dict | None,
) -> tuple[str, str, str]:
    """按业务事件套用 6.8 的通知模板 → (标题, 正文, 用了哪个模板 code 或 "")。

     6.8 的通告模板是**真正生效**的：有启用中的模板 → 用模板渲染（缺失变量填空串）；
    没有对应模板 → 用调用方给的默认话术。这样"改模板立即生效"不是一句空话。
    """
    if not event:
        return title, content, ""
    from app.models.system import NotificationTemplate
    from app.services.admin_service import render_template_text

    tpl = (
        (
            await db.execute(
                select(NotificationTemplate).where(
                    NotificationTemplate.event == event, NotificationTemplate.is_active.is_(True)
                )
            )
        )
        .scalars()
        .first()
    )
    if tpl is None:
        return title, content, ""
    variables = {k: ("" if v is None else str(v)) for k, v in (context or {}).items()}
    return (
        render_template_text(tpl.title_tpl, variables) or title,
        render_template_text(tpl.content_tpl, variables),
        tpl.code,
    )


async def notify(
    db: AsyncSession,
    *,
    user_id: str,
    title: str,
    content: str = "",
    type_: str = "system",
    biz_type: str | None = None,
    biz_id: str | None = None,
    event: str | None = None,
    context: dict | None = None,
) -> Notification:
    """站内通知（IM 推送为预留，见 spec/07 适配器层）。

    `event` + `context`：套用 6.8 通知模板（见 `render_event_text`）；不传则用默认话术。
    """
    title, content, _ = await render_event_text(
        db, event=event, title=title, content=content, context=context
    )
    n = Notification(
        user_id=user_id,
        title=title,
        content=content,
        type=type_,
        biz_type=biz_type,
        biz_id=biz_id,
    )
    db.add(n)
    return n


async def notify_many(
    db: AsyncSession,
    *,
    user_ids: list[str],
    title: str,
    content: str = "",
    type_: str = "system",
    biz_type: str | None = None,
    biz_id: str | None = None,
    event: str | None = None,
    context: dict | None = None,
) -> None:
    # 模板只解析一次（同一事件同一批收件人话术一致，避免 N 次查询）
    title, content, _ = await render_event_text(
        db, event=event, title=title, content=content, context=context
    )
    for uid in {u for u in user_ids if u}:
        await notify(
            db,
            user_id=uid,
            title=title,
            content=content,
            type_=type_,
            biz_type=biz_type,
            biz_id=biz_id,
        )


async def manager_ids(db: AsyncSession) -> list[str]:
    """取所有市场部管理人员与管理员 id（预警抄送用）。"""
    from app.models.enums import UserRole

    rows = (
        (
            await db.execute(
                select(User.id).where(
                    User.role.in_([UserRole.MARKET_MANAGER, UserRole.ADMIN]),
                    User.is_active.is_(True),
                )
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")
