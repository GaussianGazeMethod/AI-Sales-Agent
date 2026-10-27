"""所有模型的 Declarative Base + 通用混入。

约定：
- `UUIDMixin`：String(36) 主键，应用层生成 uuid4().hex
  
- `TimestampMixin`：使用 **Python 侧默认值** 而非只依赖 server_default。
  原因：若只给 SQL 函数默认值，SQLAlchemy flush 后无法得知库生成的值，
  会把属性标记为 expired，后续访问触发隐式重载；在异步会话中会抛
  `MissingGreenlet`。保留 server_default 是为了让绕过 ORM 的原生 SQL 插入也有值。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class UUIDMixin:
    """UUID 主键（String(36)）。"""

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: uuid.uuid4().hex,
    )


class TimestampMixin:
    """创建时间 / 更新时间。"""

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.now,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.now,
        onupdate=datetime.now,
        server_default=func.now(),
    )
