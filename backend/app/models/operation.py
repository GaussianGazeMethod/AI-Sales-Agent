"""闭环三数据模型（P2-b 运营与客户成功）：运营记录、运营指标、投诉、产品问题与承诺台账、FAQ。

对应 spec/05 表 23~26 与 spec/04 §3（运营 KPI）、§6（C19）、§9（投诉）。
 《产品问题与承诺台账》是 C19 痛点的落点，也是续费率 20% 的共同根因。
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import JSON, Boolean, Date, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDMixin
from app.models.enums import (
    ComplaintReason,
    ComplaintStatus,
    HandlerSide,
    IssueSource,
    IssueStatus,
    IssueType,
    MetricPeriodType,
    OperationActionType,
    RecordStatus,
)


class OperationRecord(Base, UUIDMixin, TimestampMixin):
    """运营记录（spec/05 表 23）：定期回访 / 使用培训 / 数据报告 / 活动运营 / 需求收集。

    3.6 服务支持的「需求登记清单」与 3.10 回访记录都复用本表（用 action_type 区分）。
    """

    __tablename__ = "biz_operation_record"

    lead_id: Mapped[str] = mapped_column(String(36), index=True)
    contract_id: Mapped[str | None] = mapped_column(String(36), index=True, nullable=True)
    customer_name: Mapped[str] = mapped_column(String(128), default="")

    record_date: Mapped[date] = mapped_column(Date, default=date.today, index=True)
    action_type: Mapped[str] = mapped_column(
        String(24), default=OperationActionType.VISIT, index=True
    )
    content: Mapped[str] = mapped_column(Text, default="")
    next_plan: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 需求登记/回访后续的处理状态
    status: Mapped[str] = mapped_column(String(16), default=RecordStatus.DONE, index=True)
    # 满意度本期仅记录（不做 NPS 系统）
    satisfaction: Mapped[str | None] = mapped_column(String(32), nullable=True)

    executor_id: Mapped[str] = mapped_column(String(36), default="")
    executor_name: Mapped[str] = mapped_column(String(64), default="")

    # 来源与导入批次（手工录入为 manual；Excel 导入落在同一批次号里，便于回查）
    source: Mapped[str] = mapped_column(String(16), default="manual")
    batch_no: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<OperationRecord {self.customer_name} {self.action_type}>"


class OperationMetric(Base, UUIDMixin, TimestampMixin):
    """运营指标数据（spec/05 表 24）：先手工从平台后台导出后导入，不打通产品埋点。

    核心指标：**活跃率 = 登陆人数 ÷ 总账号数**（日/周/月三种口径）。
    """

    __tablename__ = "biz_operation_metric"

    lead_id: Mapped[str] = mapped_column(String(36), index=True)
    contract_id: Mapped[str | None] = mapped_column(String(36), index=True, nullable=True)
    customer_name: Mapped[str] = mapped_column(String(128), default="")

    period_type: Mapped[str] = mapped_column(String(8), default=MetricPeriodType.MONTH, index=True)
    period: Mapped[str] = mapped_column(String(16), index=True)  # 2026-09 / 2026-W36 / 2026-09-30

    total_accounts: Mapped[int] = mapped_column(Integer, default=0)
    login_count: Mapped[int] = mapped_column(Integer, default=0)
    activity_rate: Mapped[float] = mapped_column(default=0.0)  # 自动 = login_count / total_accounts

    upload_count: Mapped[int] = mapped_column(Integer, default=0)
    download_count: Mapped[int] = mapped_column(Integer, default=0)
    file_count: Mapped[int] = mapped_column(Integer, default=0)
    # 「高质量文件」由人工标记（spec/04 §3）
    high_quality_file_count: Mapped[int] = mapped_column(Integer, default=0)

    source: Mapped[str] = mapped_column(String(32), default="manual_import")
    batch_no: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    imported_by_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    imported_by_name: Mapped[str | None] = mapped_column(String(64), nullable=True)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<OperationMetric {self.customer_name} {self.period} {self.activity_rate:.2f}>"


class Complaint(Base, UUIDMixin, TimestampMixin):
    """投诉记录（spec/05 表 25）。

     无独立流程：**谁接到谁第一时间处理** + 登记；原因是闭环节点的"爆发形态"。
    """

    __tablename__ = "biz_complaint"

    lead_id: Mapped[str] = mapped_column(String(36), index=True)
    contract_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    customer_name: Mapped[str] = mapped_column(String(128), default="")

    complainant: Mapped[str | None] = mapped_column(String(64), nullable=True)
    receiver_id: Mapped[str] = mapped_column(String(36), default="")
    receiver_name: Mapped[str] = mapped_column(String(64), default="")
    received_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, index=True)

    content: Mapped[str] = mapped_column(Text, default="")
    reason: Mapped[str] = mapped_column(String(16), default=ComplaintReason.PRODUCT, index=True)
    status: Mapped[str] = mapped_column(String(16), default=ComplaintStatus.PENDING, index=True)
    handle_result: Mapped[str | None] = mapped_column(Text, nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    escalated: Mapped[bool] = mapped_column(Boolean, default=False)
    remark: Mapped[str | None] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Complaint {self.customer_name} {self.reason} {self.status}>"


class ProductIssue(Base, UUIDMixin, TimestampMixin):
    """ 产品问题与承诺台账（spec/05 表 26）—— 对应痛点 C19。

    登记 → 派单（产研/销售）→ 跟踪 → 回复客户 → 统计；
    承诺类必须填"承诺兑现时间"，到期未兑现产生预警。
    """

    __tablename__ = "biz_product_issue"

    lead_id: Mapped[str] = mapped_column(String(36), index=True)
    contract_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    customer_name: Mapped[str] = mapped_column(String(128), default="")

    type: Mapped[str] = mapped_column(String(16), default=IssueType.PRODUCT_ISSUE, index=True)
    content: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(16), default=IssueSource.GROUP, index=True)
    proposer: Mapped[str | None] = mapped_column(String(64), nullable=True)  # 提出人

    registrar_id: Mapped[str] = mapped_column(String(36), default="")
    registrar_name: Mapped[str] = mapped_column(String(64), default="")
    registered_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, index=True)

    # 负责方：产研（问题修复）/ 销售（承诺兑现）
    handler_side: Mapped[str] = mapped_column(String(16), default=HandlerSide.RND, index=True)
    assignee_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    assignee_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)  # 处理时限

    status: Mapped[str] = mapped_column(String(16), default=IssueStatus.PENDING, index=True)
    # 承诺类必填：承诺兑现时间
    promised_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    replied_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    solution: Mapped[str | None] = mapped_column(Text, nullable=True)
    reply_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 回复客户留痕（页面 3.7）：渠道 / 对象 / 证据。证据必填 —— 未回复不算闭环。
    reply_channel: Mapped[str] = mapped_column(String(16), default="")
    reply_target: Mapped[str] = mapped_column(String(64), default="")
    reply_evidence: Mapped[str] = mapped_column(Text, default="")

    # 是否影响续费（用于续费风险提示）
    is_renewal_risk: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    remark: Mapped[str | None] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<ProductIssue {self.customer_name} {self.type} {self.status}>"


class FaqItem(Base, UUIDMixin, TimestampMixin):
    """服务支持 FAQ（页面 3.6）：标准版 FAQ + 实时更新。"""

    __tablename__ = "biz_faq_item"

    question: Mapped[str] = mapped_column(String(255))
    answer: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    is_standard: Mapped[bool] = mapped_column(Boolean, default=False)
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    view_count: Mapped[int] = mapped_column(Integer, default=0)
    updated_by_name: Mapped[str | None] = mapped_column(String(64), nullable=True)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<FaqItem {self.question[:20]}>"
