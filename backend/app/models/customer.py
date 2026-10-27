"""客户与项目分层：同一客户的多个商机分别保存阶段、金额、证据及跟进。"""
from __future__ import annotations

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDMixin
from app.models.enums import OrgType


class Customer(Base, UUIDMixin, TimestampMixin):
    """客户（学校 / 教育局 / 机构）。同名视为同一客户，其下可挂多个项目商机。"""

    __tablename__ = "biz_customer"

    name: Mapped[str] = mapped_column(String(128), index=True)
    province: Mapped[str] = mapped_column(String(32), default="")
    city: Mapped[str] = mapped_column(String(32), default="")
    district: Mapped[str] = mapped_column(String(32), default="")
    org_type: Mapped[str] = mapped_column(String(32), default=OrgType.PUBLIC_SCHOOL)

    # 客户级负责人（交付侧已有"客户级负责人"口径，这里统一）
    owner_id: Mapped[str | None] = mapped_column(String(36), index=True, nullable=True)
    owner_name: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # 客户等级（A/B/C，选填）与备注
    level: Mapped[str | None] = mapped_column(String(8), nullable=True)
    remark: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 创建来源：manual=手工/录入自动建，import=批量导入回填
    created_from: Mapped[str] = mapped_column(String(16), default="manual")

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<Customer {self.name}>"
