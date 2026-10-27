"""系统与运营数据：节假日、通知、操作日志、Excel 导入批次、IM 原始消息。"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import JSON, Boolean, Date, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDMixin
from app.models.enums import HolidayKind, IntakeParseStatus


class Holiday(Base, UUIDMixin, TimestampMixin):
    """法定节假日日历（闭环一 SLA 依赖：只算工作日）。

    - kind=holiday：放假（即使在工作日也不计时）
    - kind=makeup_workday：调休上班（即使是周末也计时）
    维护责任见 spec/10-待确认清单（Q20）；管理员可通过接口批量导入校正。
    """

    __tablename__ = "sys_holiday"

    day: Mapped[date] = mapped_column(Date, unique=True, index=True)
    kind: Mapped[str] = mapped_column(String(20), default=HolidayKind.HOLIDAY)
    name: Mapped[str | None] = mapped_column(String(32), nullable=True)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Holiday {self.day} {self.kind}>"


class Notification(Base, UUIDMixin, TimestampMixin):
    """站内通知（预留 IM 推送）。"""

    __tablename__ = "sys_notification"

    user_id: Mapped[str] = mapped_column(String(36), index=True)
    title: Mapped[str] = mapped_column(String(128))
    content: Mapped[str] = mapped_column(Text, default="")
    type: Mapped[str] = mapped_column(String(32), default="system")
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    biz_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    biz_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Notification {self.title}>"


class NotificationTemplate(Base, UUIDMixin, TimestampMixin):
    """通知模板（6.8）。

    站内通知已按业务事件发出（指派 / 回收 / 预警 / 派单 / 投诉…），
    本表把这些通知的**标题与正文话术**外置成可配置模板：
    改文案不改代码。IM（企微/钉钉/飞书）通道为预留，`channel` 先只落 `inapp`。

    模板变量用 `{name}` 形式占位，渲染时由宿主用事件参数填充；
    缺失变量渲染为空串（不抛错），便于业务侧逐步补齐参数。
    """

    __tablename__ = "cfg_notification_template"

    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128))
    # 站内 / 企微 / 钉钉 / 飞书（后三者预留）
    channel: Mapped[str] = mapped_column(String(16), default="inapp")
    title_tpl: Mapped[str] = mapped_column(String(255))
    content_tpl: Mapped[str] = mapped_column(Text, default="")
    # 触发该模板的业务事件（与 audit_service.notify 的 type_ 对齐）
    event: Mapped[str] = mapped_column(String(64), default="", index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    remark: Mapped[str | None] = mapped_column(String(255), nullable=True)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<NotificationTemplate {self.code}>"


class OperationLog(Base, UUIDMixin, TimestampMixin):
    """操作日志（导出 / 删除 / 改派 / 批量修改留痕）。"""

    __tablename__ = "sys_operation_log"

    operator_id: Mapped[str] = mapped_column(String(36), index=True)
    operator_name: Mapped[str] = mapped_column(String(64), default="")
    action: Mapped[str] = mapped_column(String(32), index=True)
    target_type: Mapped[str] = mapped_column(String(32), default="")
    target_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    target_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<OperationLog {self.action} {self.target_type}>"


class ImportBatch(Base, UUIDMixin, TimestampMixin):
    """Excel 导入批次记录。"""

    __tablename__ = "biz_import_batch"

    batch_no: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    filename: Mapped[str] = mapped_column(String(255), default="")
    importer_id: Mapped[str] = mapped_column(String(36), default="")
    importer_name: Mapped[str] = mapped_column(String(64), default="")
    total: Mapped[int] = mapped_column(Integer, default=0)
    success: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[list[dict]] = mapped_column(JSON, default=list)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<ImportBatch {self.batch_no} {self.success}/{self.total}>"


class ImMessage(Base, UUIDMixin, TimestampMixin):
    """群消息原始记录（IM 归集预留 + AI 录入代理的消息溯源）。

    `message_id` 唯一，用于防重复归集。
    """

    __tablename__ = "biz_im_message"

    platform: Mapped[str] = mapped_column(String(16), default="manual", index=True)
    message_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    group_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    sender_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    sender_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    raw_text: Mapped[str] = mapped_column(Text, default="")
    parse_status: Mapped[str] = mapped_column(
        String(32), default=IntakeParseStatus.SUCCESS, index=True
    )
    parse_result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    lead_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, index=True)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<ImMessage {self.message_id}>"
