"""AI 销售助手数据模型：更新草稿（草稿区）+ 诊断报告。

 安全底线：AI **绝不直连业务库**，也不直接改业务数据。
AI 只写本文件的 `AiDraft`（草稿区），由项目负责人在「5.2 我的待确认」确认后才
由服务层写入正式业务表（跟进记录 / 标准完成 / 金额变更 / 风险预警）。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDMixin


class AiDraft(Base, UUIDMixin, TimestampMixin):
    """AI 生成的更新草稿（未确认不生效）。"""

    __tablename__ = "ai_draft"

    # 来源消息（专员发的那段结构化信息）
    source_message: Mapped[str] = mapped_column(Text, default="")
    channel: Mapped[str] = mapped_column(String(16), default="chat")
    im_message_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

    # AI 定位到的客户/商机（可能定位不到 → 待人工指定）
    lead_id: Mapped[str | None] = mapped_column(String(36), index=True, nullable=True)
    lead_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    match_confidence: Mapped[float] = mapped_column(default=0.0)
    match_candidates: Mapped[list[dict]] = mapped_column(JSON, default=list)

    # 回报给专员的一句话
    summary: Mapped[str] = mapped_column(Text, default="")

    # 草稿内容：{followup:{...}, standards:[...], amount:{...}, risks:[...], stage_to:...}
    payload: Mapped[dict] = mapped_column(JSON, default=dict)

    # pending 待确认 / confirmed 已确认 / ignored 已忽略 / failed 解析失败
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)

    created_by: Mapped[str] = mapped_column(String(36), index=True)  # 发起人（=项目负责人）
    created_by_name: Mapped[str] = mapped_column(String(64), default="")
    confirmed_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    confirmed_by_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    apply_result: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # llm=模型解析 / rule=无 key 时的规则降级解析
    parse_mode: Mapped[str] = mapped_column(String(16), default="rule")
    model_used: Mapped[str | None] = mapped_column(String(64), nullable=True)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<AiDraft {self.status} {self.lead_name}>"


class AiDiagnosis(Base, UUIDMixin, TimestampMixin):
    """AI 项目诊断报告（5.3 诊断报告历史）。"""

    __tablename__ = "ai_diagnosis"

    lead_id: Mapped[str] = mapped_column(String(36), index=True)
    lead_name: Mapped[str] = mapped_column(String(128), default="")
    stage: Mapped[str | None] = mapped_column(String(32), nullable=True)

    trigger: Mapped[str] = mapped_column(String(32), default="manual")  # manual / stuck_alert
    content: Mapped[str] = mapped_column(Text, default="")  # 正文（Markdown）
    structured: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    adopted: Mapped[bool | None] = mapped_column(nullable=True)  # 后续是否采纳

    created_by: Mapped[str] = mapped_column(String(36), index=True)
    created_by_name: Mapped[str] = mapped_column(String(64), default="")
    parse_mode: Mapped[str] = mapped_column(String(16), default="rule")
    model_used: Mapped[str | None] = mapped_column(String(64), nullable=True)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<AiDiagnosis {self.lead_name} {self.trigger}>"
