"""用户与组织。

说明：本系统为**公司内部系统**，账号由管理员创建，不开放自助注册。
密码使用 bcrypt 哈希存储；认证采用 JWT（access + refresh）。
"""

from __future__ import annotations

from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDMixin
from app.models.enums import DataScope, UserRole


class User(Base, UUIDMixin, TimestampMixin):
    """系统用户。"""

    __tablename__ = "sys_user"

    # 登录名与姓名
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(64))

    # 密码哈希（bcrypt）
    password_hash: Mapped[str] = mapped_column(String(255), default="")

    # 角色与数据范围
    role: Mapped[str] = mapped_column(String(32), default=UserRole.MARKET_STAFF)
    data_scope: Mapped[str] = mapped_column(String(16), default=DataScope.SELF)

    # 组织
    department: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # 联系方式
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    email: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # 预留：三家 IM 的 userid 绑定（供 agent 归集时自动识别发送人）
    wecom_userid: Mapped[str | None] = mapped_column(String(128), nullable=True)
    dingtalk_userid: Mapped[str | None] = mapped_column(String(128), nullable=True)
    feishu_userid: Mapped[str | None] = mapped_column(String(128), nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<User {self.username} ({self.role})>"


class Department(Base, UUIDMixin, TimestampMixin):
    """部门（用于数据范围与统计）。"""

    __tablename__ = "sys_department"

    name: Mapped[str] = mapped_column(String(64), unique=True)
    parent_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    sort_order: Mapped[int] = mapped_column(default=0)
    # 负责人姓名（与 User.department 的姓名字符串口径一致；不建 FK，避免与既有数据迁移冲突）
    leader_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    remark: Mapped[str | None] = mapped_column(String(255), nullable=True)
