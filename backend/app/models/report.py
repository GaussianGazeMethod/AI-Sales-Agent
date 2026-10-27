"""P1-c / P1-d 新增模型：预警规则配置、周报、项目评审卡、回款记录。

对应 spec/05 表 15（回款记录表）、表 17（周报表）、表 18（项目评审卡），
外加 P1-c 的**可配置预警规则**（对应页面 6.7）。
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import JSON, Boolean, Date, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDMixin
from app.models.enums import ReviewVerdict


class RuleConfig(Base, UUIDMixin, TimestampMixin):
    """预警规则与阈值配置（页面 6.7）。

    阈值放进表里而不是写死代码 —— 设计要求"规则阈值可配置"
    （spec/10 原 14 号文档 §六.4）。服务层用 `rule_config_service.get(key, default)` 读取。
    """

    __tablename__ = "sys_rule_config"

    key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    value: Mapped[str] = mapped_column(String(255), default="")
    value_type: Mapped[str] = mapped_column(String(16), default="float")  # int/float/str/json
    label: Mapped[str] = mapped_column(String(64), default="")
    group: Mapped[str] = mapped_column(String(32), default="risk", index=True)
    unit: Mapped[str | None] = mapped_column(String(16), nullable=True)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<RuleConfig {self.key}={self.value}>"


class WeeklyReport(Base, UUIDMixin, TimestampMixin):
    """周报（表 17）：自动汇总指标 + 自动生成正文（可编辑）+ 问题与下周计划。"""

    __tablename__ = "biz_weekly_report"

    period: Mapped[str] = mapped_column(String(16), index=True)  # 2026-W40
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)

    user_id: Mapped[str] = mapped_column(String(36), index=True)
    user_name: Mapped[str] = mapped_column(String(64), default="")

    # 自动汇总指标（新增有效商机 / 各环节商机与金额 / 卡单数 / 转化率 / 加权预测 / 签单 / 回款）
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)

    # 正文（AI 生成，人可编辑）
    body: Mapped[str] = mapped_column(Text, default="")
    problems: Mapped[str | None] = mapped_column(Text, nullable=True)  # 问题
    next_plan: Mapped[str | None] = mapped_column(Text, nullable=True)  # 下周计划

    is_submitted: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # pending=指标已汇总但正文未生成 / ai=模型生成 / manual=人工重写
    # rule 仅保留给历史数据
    generated_by: Mapped[str] = mapped_column(String(16), default="pending")
    model_used: Mapped[str | None] = mapped_column(String(64), nullable=True)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<WeeklyReport {self.period} {self.user_name}>"


class ProjectReview(Base, UUIDMixin, TimestampMixin):
    """项目评审卡（表 18）：管理者 5 看 + 真执行判定（能力/态度/正常）。

    对应页面 2.7 与场景 S2-8 第三步。5 看每项：通过 / 有风险 / 不通过。
    """

    __tablename__ = "biz_project_review"

    lead_id: Mapped[str] = mapped_column(String(36), index=True)
    lead_name: Mapped[str] = mapped_column(String(128), default="")
    stage: Mapped[str | None] = mapped_column(String(32), nullable=True)

    reviewer_id: Mapped[str] = mapped_column(String(36), index=True)
    reviewer_name: Mapped[str] = mapped_column(String(64), default="")
    review_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, index=True)

    # 5 看：{m1:{result,note}, m2:{...}, ...}
    m1_decision_maker: Mapped[dict] = mapped_column(JSON, default=dict)
    m2_intent_budget: Mapped[dict] = mapped_column(JSON, default=dict)
    m3_demand_confirm: Mapped[dict] = mapped_column(JSON, default=dict)
    m4_stakeholders: Mapped[dict] = mapped_column(JSON, default=dict)
    m5_unknown_competitor: Mapped[dict] = mapped_column(JSON, default=dict)

    verdict: Mapped[str] = mapped_column(String(16), default=ReviewVerdict.NORMAL, index=True)
    conclusion: Mapped[str | None] = mapped_column(String(255), nullable=True)
    next_review_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    remark: Mapped[str | None] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<ProjectReview {self.lead_name} {self.verdict}>"


class PaymentRecord(Base, UUIDMixin, TimestampMixin):
    """回款记录（表 15）：一笔回款。主表 paid_amount_fen 由本表汇总。"""

    __tablename__ = "biz_payment_record"

    lead_id: Mapped[str] = mapped_column(String(36), index=True)
    lead_name: Mapped[str] = mapped_column(String(128), default="")
    amount_fen: Mapped[int] = mapped_column(Integer, default=0)
    paid_date: Mapped[date] = mapped_column(Date, default=date.today, index=True)
    method: Mapped[str | None] = mapped_column(String(32), nullable=True)  # 银行转账/支票…
    period_no: Mapped[str | None] = mapped_column(String(32), nullable=True)  # 期次：首款/二期…
    remark: Mapped[str | None] = mapped_column(Text, nullable=True)
    operator_id: Mapped[str] = mapped_column(String(36), default="")
    operator_name: Mapped[str] = mapped_column(String(64), default="")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<PaymentRecord {self.lead_name} {self.amount_fen}>"


__all__ = [
    "PaymentRecord",
    "ProjectReview",
    "RuleConfig",
    "WeeklyReport",
]
