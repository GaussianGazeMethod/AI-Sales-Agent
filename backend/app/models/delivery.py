"""闭环三数据模型（P2-a 交付核心）：合同、交付物清单、交付交接单、10 阶段进度、验收、续费评估。

对应 spec/05 表 19~22 与 spec/04 §4 的 10 阶段 SOP。
 10 阶段的「关键产出材料」来自 spec/04 §4 表格，落为**可配置数据**（页面 6.6），改标准不改代码。

 阶段停留时长基准本期**不设**：`duration_days = 0` 表示未设基准，
   此时不产生交付延期预警，页面上显示"基准未设"。
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDMixin
from app.models.enums import (
    AcceptanceStatus,
    ContractStatus,
    RenewalStatus,
    RenewalWillingness,
)


class Contract(Base, UUIDMixin, TimestampMixin):
    """合同（spec/05 表 19）。

    一个客户可有多个合同，但**运营交付专员按客户归属**（不区分合同数）。
    """

    __tablename__ = "biz_contract"

    lead_id: Mapped[str] = mapped_column(String(36), index=True)  # 客户（= 主表记录）
    customer_name: Mapped[str] = mapped_column(String(128), default="")

    contract_no: Mapped[str] = mapped_column(String(64), index=True)
    amount_fen: Mapped[int] = mapped_column(Integer, default=0)
    sign_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    service_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    service_end: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)

    acceptance_standard: Mapped[str | None] = mapped_column(Text, nullable=True)
    has_hardware: Mapped[bool] = mapped_column(Boolean, default=False)
    attachment: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default=ContractStatus.EXECUTING, index=True)

    # 唯一一次交接的双方
    sales_owner_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    sales_owner_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # 运营交付专员（客户级）
    delivery_owner_id: Mapped[str | None] = mapped_column(String(36), index=True, nullable=True)
    delivery_owner_name: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # 当前交付阶段（1~10）与进入时间
    current_phase: Mapped[int] = mapped_column(Integer, default=1, index=True)
    phase_entered_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    remark: Mapped[str | None] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Contract {self.contract_no} {self.customer_name}>"


class DeliveryItem(Base, UUIDMixin, TimestampMixin):
    """交付物清单（spec/05 表 20）：标准《项目服务清单》项 + 合同特约的非标项。"""

    __tablename__ = "biz_delivery_item"

    contract_id: Mapped[str] = mapped_column(String(36), index=True)
    name: Mapped[str] = mapped_column(String(128))
    quantity: Mapped[str | None] = mapped_column(String(32), nullable=True)
    unit: Mapped[str | None] = mapped_column(String(16), nullable=True)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    acceptance_standard: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 是否标准项（来自标准《项目服务清单》）—— 非标项即"合同特约内容"
    is_standard: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    remark: Mapped[str | None] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<DeliveryItem {self.name} standard={self.is_standard}>"


class StandardServiceItem(Base, UUIDMixin, TimestampMixin):
    """标准《项目服务清单》模板（由运营维护，交付物清单可一键生成）。"""

    __tablename__ = "cfg_standard_service_item"

    name: Mapped[str] = mapped_column(String(128))
    unit: Mapped[str | None] = mapped_column(String(16), nullable=True)
    quantity: Mapped[str | None] = mapped_column(String(32), nullable=True)
    acceptance_standard: Mapped[str | None] = mapped_column(Text, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<StandardServiceItem {self.name}>"


class HandoverSheet(Base, UUIDMixin, TimestampMixin):
    """交付交接单（spec/05 表 21）——  全系统唯一一次真正的交接。"""

    __tablename__ = "biz_handover_sheet"

    contract_id: Mapped[str] = mapped_column(String(36), unique=True, index=True)
    lead_id: Mapped[str] = mapped_column(String(36), index=True)
    customer_name: Mapped[str] = mapped_column(String(128), default="")

    handover_user_id: Mapped[str | None] = mapped_column(
        String(36), nullable=True
    )  # 移交人（销售）
    handover_user_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    receiver_id: Mapped[str | None] = mapped_column(String(36), nullable=True)  # 接收人（运营）
    receiver_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    delivery_owner_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    delivery_owner_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    handover_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # 三段式：标准清单核对说明 / 非标内容澄清记录
    standard_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    nonstandard_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 双方确认
    sales_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    sales_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    delivery_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    delivery_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    remark: Mapped[str | None] = mapped_column(Text, nullable=True)

    @property
    def is_confirmed(self) -> bool:
        return bool(self.sales_confirmed and self.delivery_confirmed)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<HandoverSheet {self.customer_name} confirmed={self.is_confirmed}>"


class DeliveryPhaseStandard(Base, UUIDMixin, TimestampMixin):
    """10 阶段产出材料配置（页面 6.6）：阶段 × 产出材料 × 是否必填 × 停留时长基准。"""

    __tablename__ = "cfg_delivery_phase_standard"

    phase: Mapped[int] = mapped_column(Integer, index=True)  # 1~10
    seq: Mapped[str] = mapped_column(String(8), default="1")
    content: Mapped[str] = mapped_column(String(255))
    is_required: Mapped[bool] = mapped_column(Boolean, default=True)
    # 0 表示**未设基准**→ 不产生交付延期预警
    duration_days: Mapped[int] = mapped_column(Integer, default=0)
    evidence_hint: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<DeliveryPhaseStandard 阶段{self.phase} {self.content[:16]}>"


class DeliveryStage(Base, UUIDMixin, TimestampMixin):
    """交付阶段进度（spec/05 表 22）：某合同某条产出材料的完成情况（含证据）。"""

    __tablename__ = "biz_delivery_stage"

    contract_id: Mapped[str] = mapped_column(String(36), index=True)
    phase: Mapped[int] = mapped_column(Integer, index=True)
    standard_id: Mapped[str] = mapped_column(String(36), index=True)
    standard_content: Mapped[str] = mapped_column(String(255), default="")

    is_done: Mapped[bool] = mapped_column(Boolean, default=False)
    evidence: Mapped[str | None] = mapped_column(Text, nullable=True)
    done_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    done_by_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    done_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    remark: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(16), default="manual")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<DeliveryStage 合同{self.contract_id[:6]} 阶段{self.phase} done={self.is_done}>"


class AcceptanceRecord(Base, UUIDMixin, TimestampMixin):
    """验收管理（页面 3.11）。

    spec/05 的 8 张表里验收由「阶段八 项目验收」的产出材料承载，
    但页面 3.11 需要独立跟踪"到期前 15 个工作日发起 → 验收会 → 客户签署确认书"，
    故单独建一张轻表（属实现补充，已在 spec/08 记录）。
    """

    __tablename__ = "biz_acceptance"

    contract_id: Mapped[str] = mapped_column(String(36), index=True)
    lead_id: Mapped[str] = mapped_column(String(36), index=True)
    customer_name: Mapped[str] = mapped_column(String(128), default="")

    status: Mapped[str] = mapped_column(String(16), default=AcceptanceStatus.PENDING, index=True)
    apply_date: Mapped[date | None] = mapped_column(Date, nullable=True)  # 提交验收申请
    meeting_date: Mapped[date | None] = mapped_column(Date, nullable=True)  # 验收会
    result: Mapped[str | None] = mapped_column(String(16), nullable=True)  # pass / fail
    signed_date: Mapped[date | None] = mapped_column(Date, nullable=True)  # 客户签署确认书
    attachment: Mapped[str | None] = mapped_column(String(255), nullable=True)
    rectify_note: Mapped[str | None] = mapped_column(Text, nullable=True)  # 问题整改
    remark: Mapped[str | None] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<AcceptanceRecord {self.customer_name} {self.status}>"


class RenewalAssessment(Base, UUIDMixin, TimestampMixin):
    """续费评估（页面 3.12）。

     已确认口径：**续费机会不进系统管理，先只看续费率** —— 所以这里只做到期清单 +
    一次轻量评估记录（活跃度/满意度/验收结果 → 意愿分档 → 方案 → 结论），不做商机管道。
    """

    __tablename__ = "biz_renewal_assessment"

    contract_id: Mapped[str] = mapped_column(String(36), index=True)
    lead_id: Mapped[str] = mapped_column(String(36), index=True)
    customer_name: Mapped[str] = mapped_column(String(128), default="")
    owner_name: Mapped[str | None] = mapped_column(String(64), nullable=True)

    service_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    assess_date: Mapped[date | None] = mapped_column(Date, default=date.today)

    activity_note: Mapped[str | None] = mapped_column(String(255), nullable=True)  # 活跃度说明
    satisfaction: Mapped[str | None] = mapped_column(
        String(32), nullable=True
    )  # 满意度（本期仅记录）
    acceptance_result: Mapped[str | None] = mapped_column(String(16), nullable=True)
    willingness: Mapped[str] = mapped_column(String(16), default=RenewalWillingness.MID)
    plan_note: Mapped[str | None] = mapped_column(Text, nullable=True)  # 分档方案
    conclusion: Mapped[str | None] = mapped_column(Text, nullable=True)  # 结论/推进计划
    expected_amount_fen: Mapped[int] = mapped_column(Integer, default=0)

    status: Mapped[str] = mapped_column(String(16), default=RenewalStatus.PENDING, index=True)
    assessed_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    assessed_by_name: Mapped[str | None] = mapped_column(String(64), nullable=True)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<RenewalAssessment {self.customer_name} {self.status}>"
