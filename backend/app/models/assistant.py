"""应用会话及路由回执；不充当Agently执行器或客户项目事实库。"""

from datetime import datetime

from sqlalchemy import JSON, DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDMixin


class AssistantConversation(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "ai_assistant_conversation"
    owner_id: Mapped[str] = mapped_column(String(36), index=True)
    lead_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    task: Mapped[dict] = mapped_column(JSON, default=dict)
    suspended_tasks: Mapped[list] = mapped_column(JSON, default=list)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    active_request: Mapped[str | None] = mapped_column(String(100), nullable=True)
    processing_since: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class AssistantTurn(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "ai_assistant_turn"
    __table_args__ = (UniqueConstraint("conversation_id", "request_key"),)
    conversation_id: Mapped[str] = mapped_column(String(36), index=True)
    request_key: Mapped[str] = mapped_column(String(100))
    content: Mapped[str] = mapped_column(Text)
    requested_lead_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="processing")
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(String(300), nullable=True)
    # Host-only业务恢复检查点，不进入模型或对外回执。
    checkpoint: Mapped[dict | None] = mapped_column(JSON, nullable=True)
