"""项目事实、修订与行动独立于聊天会话保存。新增表不改历史业务记录。"""

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDMixin


class ProjectDossier(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "biz_project_dossier"
    lead_id: Mapped[str] = mapped_column(String(36), unique=True, index=True)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    facts: Mapped[dict] = mapped_column(JSON, default=dict)
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)


class ProjectFactEvent(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "biz_project_fact_event"
    lead_id: Mapped[str] = mapped_column(String(36), index=True)
    turn_id: Mapped[str] = mapped_column(String(36), unique=True)
    owner_id: Mapped[str] = mapped_column(String(36))
    occurred_at: Mapped[datetime] = mapped_column(DateTime)
    content: Mapped[str] = mapped_column(Text)
    changes: Mapped[dict] = mapped_column(JSON, default=dict)
    record: Mapped[dict] = mapped_column(JSON, default=dict)
    followup_id: Mapped[str | None] = mapped_column(String(36), nullable=True)


class AssistantAction(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "biz_assistant_action"
    lead_id: Mapped[str] = mapped_column(String(36), index=True)
    owner_id: Mapped[str] = mapped_column(String(36), index=True)
    origin: Mapped[str] = mapped_column(String(120), unique=True)
    content: Mapped[str] = mapped_column(String(500))
    due_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    customer_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    effect: Mapped[str | None] = mapped_column(Text, nullable=True)
    history: Mapped[list] = mapped_column(JSON, default=list)
