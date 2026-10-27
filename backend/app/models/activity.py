"""闭环一过程数据：跟进记录、待办提醒、分配记录。"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDMixin
from app.models.enums import (
    AssignmentAction,
    FollowMethod,
    ReminderStatus,
    ReminderType,
)


class FollowUp(Base, UUIDMixin, TimestampMixin):
    """跟进记录。

    `is_valid` / `next_action` 只在闭环一阶段必填；有效性由系统按
    「信息正确 且 有意愿（同意汇报交流 / 微信交流看我方资料）」自动判定。
    """

    __tablename__ = "biz_follow_up"

    # 项目级跟进：lead_id 有值；**客户级跟进**（渠道活动、跨项目复盘、决策链澄清）：
    # lead_id 为空、customer_id 有值 —— 这类记录不进任何环节、不计环节时长
    lead_id: Mapped[str | None] = mapped_column(String(36), index=True, nullable=True)
    customer_id: Mapped[str | None] = mapped_column(String(36), index=True, nullable=True)
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    user_name: Mapped[str] = mapped_column(String(64), default="")

    method: Mapped[str] = mapped_column(String(32), default=FollowMethod.PHONE)
    followed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, index=True)
    content: Mapped[str] = mapped_column(Text, default="")

    # 闭环一：是否有效 + 下一步动作（有效性自动判定的输入）
    is_valid: Mapped[str | None] = mapped_column(String(16), nullable=True)
    next_action: Mapped[str | None] = mapped_column(String(32), nullable=True)
    next_follow_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # 闭环二：这次跟进发生在哪个环节 + 关联的完成标准
    stage: Mapped[str | None] = mapped_column(String(32), nullable=True)
    related_standard_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

    # 数据来源：manual=人工填写，ai_confirmed=AI 草稿经人工确认后落库
    source: Mapped[str] = mapped_column(String(16), default="manual")

    # 客户信息是否核实正确（自动判定的第 3 个字段，默认按"正确"处理）
    info_verified: Mapped[bool] = mapped_column(Boolean, default=True)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<FollowUp {self.lead_id} {self.method}>"


class Reminder(Base, UUIDMixin, TimestampMixin):
    """待办提醒（5 类线索待办 + 商机待办 + 交付待办）。"""

    __tablename__ = "biz_reminder"

    lead_id: Mapped[str | None] = mapped_column(String(36), index=True, nullable=True)
    lead_name: Mapped[str | None] = mapped_column(String(128), nullable=True)

    type: Mapped[str] = mapped_column(String(32), default=ReminderType.FIRST_FOLLOW_DUE, index=True)
    content: Mapped[str] = mapped_column(String(255), default="")
    remind_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, index=True)

    receiver_id: Mapped[str] = mapped_column(String(36), index=True)
    receiver_name: Mapped[str] = mapped_column(String(64), default="")

    status: Mapped[str] = mapped_column(String(16), default=ReminderStatus.PENDING, index=True)
    done_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    done_by: Mapped[str | None] = mapped_column(String(36), nullable=True)

    # 销售行动的出处及反馈；旧系统提醒保持为空。
    source_diagnosis_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    source_action_index: Mapped[int | None] = mapped_column(nullable=True)
    source_suggestion: Mapped[str | None] = mapped_column(Text, nullable=True)
    completion_followup_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

    # 幂等键：同一条线索同一类提醒同一天只生成一次
    dedup_key: Mapped[str] = mapped_column(String(128), unique=True, index=True)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Reminder {self.type} {self.status}>"


class Assignment(Base, UUIDMixin, TimestampMixin):
    """分配记录（指派 / 改派 / 回收 / 公海领取），全程留痕。"""

    __tablename__ = "biz_assignment"

    lead_id: Mapped[str] = mapped_column(String(36), index=True)
    action: Mapped[str] = mapped_column(String(16), default=AssignmentAction.ASSIGN)

    from_owner_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    from_owner_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    to_owner_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    to_owner_name: Mapped[str | None] = mapped_column(String(64), nullable=True)

    operator_id: Mapped[str] = mapped_column(String(36))
    operator_name: Mapped[str] = mapped_column(String(64), default="")
    remark: Mapped[str | None] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Assignment {self.action} {self.lead_id}>"


class MarketTarget(Base, UUIDMixin, TimestampMixin):
    """市场目标（按人 × 月设 有效商机数 / 新增线索数 / 有效率）。"""

    __tablename__ = "biz_market_target"

    period_type: Mapped[str] = mapped_column(String(16), default="month")
    period: Mapped[str] = mapped_column(String(16), index=True)  # 形如 2026-09
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    user_name: Mapped[str] = mapped_column(String(64), default="")

    # valid_lead_count 有效商机数 / new_lead_count 新增线索数 / valid_rate 有效率
    metric: Mapped[str] = mapped_column(String(32), index=True)
    target_value: Mapped[float] = mapped_column(default=0.0)

    setter_id: Mapped[str] = mapped_column(String(36), default="")
    setter_name: Mapped[str] = mapped_column(String(64), default="")
    period_end: Mapped[date | None] = mapped_column(Date, nullable=True)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<MarketTarget {self.period} {self.user_name} {self.metric}>"
