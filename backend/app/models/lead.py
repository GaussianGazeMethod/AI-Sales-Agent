"""闭环一主数据：客户/商机主表（唯一主表）+ 客户联系人。

 关键前提（已确认）：**客户线索与商机是同一条记录**（"有效商机"就是闭环二环节一），
所以全系统只有这一张主表，闭环二/闭环三通过字段扩展而非新建表。
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import JSON, Boolean, Date, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDMixin
from app.models.enums import LeadSource, LeadStatus, OrgType


class Lead(Base, UUIDMixin, TimestampMixin):
    """客户/商机主表。"""

    __tablename__ = "biz_lead"

    # ── 0. 客户层归属（客户-项目两层，见 app/models/customer.py）──
    # customer_id 为空表示尚未归集（历史数据由迁移脚本回填）
    customer_id: Mapped[str | None] = mapped_column(String(36), index=True, nullable=True)
    # 项目名：同一客户下区分不同项目，如「教学平台试点项目」
    # （设计稿 04 的标题是「客户 / 项目名」，此前只有客户名，用环节名占位）
    project_name: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # ── 1.1 客户基本信息 ──
    name: Mapped[str] = mapped_column(String(128), index=True)
    source: Mapped[str] = mapped_column(String(32), default=LeadSource.COMPANY_POOL)
    province: Mapped[str] = mapped_column(String(32), default="")
    city: Mapped[str] = mapped_column(String(32), default="")
    district: Mapped[str] = mapped_column(String(32), default="")
    org_type: Mapped[str] = mapped_column(String(32), default=OrgType.PUBLIC_SCHOOL)

    # 来源=渠道商 / 内部人员介绍 时的报备信息（本期只记录这两类）
    channel_unit: Mapped[str | None] = mapped_column(String(128), nullable=True)
    referrer: Mapped[str | None] = mapped_column(String(64), nullable=True)

    creator_id: Mapped[str] = mapped_column(String(36), index=True)
    creator_name: Mapped[str] = mapped_column(String(64), default="")

    # ── 1.2 流程与时间 ──
    owner_id: Mapped[str | None] = mapped_column(String(36), index=True, nullable=True)
    owner_name: Mapped[str | None] = mapped_column(String(64), nullable=True)

    status: Mapped[str] = mapped_column(String(32), default=LeadStatus.UNASSIGNED, index=True)

    # 疑似重复（撞单）：提示 + 允许继续录 + 标记，由管理人员裁决
    is_suspect_duplicate: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    duplicate_hint: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # 合并时指向保留的主记录
    parent_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    # 疑似重复裁决结论：keep=保留为独立记录 / merged=已合并
    dedup_status: Mapped[str | None] = mapped_column(String(16), nullable=True)

    assign_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    first_follow_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_follow_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    valid_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    sign_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    # SLA 截止时刻（按工作日算，冗余存储便于列表排序与扫描）
    first_follow_deadline: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, index=True
    )
    recycle_deadline: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)

    # ── 1.3 商机金额与预测（闭环二）──
    budget_amount_fen: Mapped[int] = mapped_column(Integer, default=0)
    contract_amount_fen: Mapped[int] = mapped_column(Integer, default=0)
    paid_amount_fen: Mapped[int] = mapped_column(Integer, default=0)
    expected_sign_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    # 历史日期列只保留旧记录；不再用于R5判断或新写入。
    stage_expected_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    current_stage: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    stage_entered_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    win_probability: Mapped[float] = mapped_column(default=0.0)

    # ── 1.4 风险与结果 ──
    risk_types: Mapped[list[str]] = mapped_column(JSON, default=list)
    lost_reason_types: Mapped[list[str]] = mapped_column(JSON, default=list)
    lost_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    lost_remark: Mapped[str | None] = mapped_column(Text, nullable=True)
    deliver_handoff_status: Mapped[str | None] = mapped_column(String(32), nullable=True)

    remark: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ── 软删除（仅管理员可删，且写操作日志）──
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Lead {self.name} {self.status}>"

    # ── 派生属性（响应里直接可用，不落库）──
    @property
    def unpaid_amount_fen(self) -> int:
        return max(self.contract_amount_fen - self.paid_amount_fen, 0)

    @property
    def weighted_amount_fen(self) -> int:
        return int(round(self.budget_amount_fen * (self.win_probability or 0.0)))


class Contact(Base, UUIDMixin, TimestampMixin):
    """客户联系人（一个客户可多个，只允许一个主联系人）。"""

    __tablename__ = "biz_contact"

    lead_id: Mapped[str] = mapped_column(String(36), index=True)
    name: Mapped[str] = mapped_column(String(64))
    position: Mapped[str | None] = mapped_column(String(64), nullable=True)
    phone: Mapped[str] = mapped_column(String(32), default="")
    wechat: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)
    remark: Mapped[str | None] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Contact {self.name} of {self.lead_id}>"
