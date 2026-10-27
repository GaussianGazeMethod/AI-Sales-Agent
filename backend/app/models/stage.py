"""闭环二数据模型：环节标准卡、标准完成、环节流转、金额变更、风险预警。

这批表是 AI 录入代理的落库目的地：AI 生成的"标准勾选草稿 / 金额修正草稿 /
风险草稿"在人工确认后写入这里。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDMixin
from app.models.enums import AlertKind, RiskType


class StageStandard(Base, UUIDMixin, TimestampMixin):
    """环节标准卡配置（7 环节 × 标准项 × 是否硬门槛 × 停留时长 × 所需证据）。

    可在「系统设置 6.5 环节标准卡配置」里改，**不改代码**。
    """

    __tablename__ = "cfg_stage_standard"

    stage: Mapped[str] = mapped_column(String(32), index=True)
    seq: Mapped[str] = mapped_column(String(8), default="1")  # ①②③ 的序号
    content: Mapped[str] = mapped_column(String(255))
    is_hard_gate: Mapped[bool] = mapped_column(Boolean, default=False)
    duration_days: Mapped[int] = mapped_column(Integer, default=0)
    evidence_hint: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<StageStandard {self.stage}-{self.seq} {self.content[:12]}>"


class StageCompletion(Base, UUIDMixin, TimestampMixin):
    """ 环节标准完成记录 —— 解决"真执行"的核心表：勾选必须附证据。"""

    __tablename__ = "biz_stage_completion"

    lead_id: Mapped[str] = mapped_column(String(36), index=True)
    stage: Mapped[str] = mapped_column(String(32), index=True)
    standard_id: Mapped[str] = mapped_column(String(36), index=True)
    standard_content: Mapped[str] = mapped_column(String(255), default="")

    is_done: Mapped[bool] = mapped_column(Boolean, default=False)
    evidence: Mapped[str | None] = mapped_column(Text, nullable=True)
    done_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    done_by_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    done_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    remark: Mapped[str | None] = mapped_column(Text, nullable=True)
    # manual=人工勾选，ai_confirmed=AI 草稿确认后勾选
    source: Mapped[str] = mapped_column(String(16), default="manual")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<StageCompletion {self.lead_id} {self.stage} done={self.is_done}>"


class StandardJudgement(Base, UUIDMixin, TimestampMixin):
    """ AI 对「当前环节完成标准」的判定结果（**一个商机只保留最新一条**）。

    为什么要有这张表：
    > 「项目阶段已显示到阶段四，为啥还需要人工确认点击 AI 分析？」

     两条边界，缺一不可：
    - **触发全自动**：跟进记录写入成功后由服务层自动排一次判定（见
      `app/services/judgement_service.py`），专员不需要点任何按钮；
    - **生效仍人工**：本表只是「判定草稿」（`status=pending`），必须由项目负责人
      点「采纳」才写进 `biz_stage_completion`
      —— spec/10 第 191 行「AI 自动更新后必须由项目负责人人工确认才生效」。

    同一商机重复判定就**覆盖**（先删同 lead 旧记录再插），因此永远只读最新一条。
    """

    __tablename__ = "biz_standard_judgement"

    lead_id: Mapped[str] = mapped_column(String(36), index=True)
    stage: Mapped[str] = mapped_column(String(32), index=True)
    judged_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    #: 判定用的模型（别名解析后的真实名，不写死厂商）
    model_used: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: llm / rule（见 AiParseMode；本系统不做规则降级，正常都是 llm）
    parse_mode: Mapped[str | None] = mapped_column(String(16), nullable=True)

    #: 与 auto-judge 返回的 items **同结构**（含引文、followup_id、置信度、理由）
    items: Mapped[list[dict]] = mapped_column(JSON, default=list)
    #: 判定时**已完成**、未重复下发给模型的标准 id
    already_done: Mapped[list[str]] = mapped_column(JSON, default=list)
    #: 判定时库里的**真实**硬门槛状态（判定不改数据，采纳后才变）
    hard_gate_passed: Mapped[bool] = mapped_column(Boolean, default=False)
    missing_hard_gates: Mapped[list[str]] = mapped_column(JSON, default=list)

    #: 判定所依据的**最新一条跟进时间**：晚于它的跟进 = 还没判定过 → 前端提示 stale
    basis_followup_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    #: pending 待人工确认 / accepted 已采纳 / ignored 已忽略
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<StandardJudgement {self.lead_id} {self.stage} {self.status}>"


class StageSalesConfirmation(Base, UUIDMixin, TimestampMixin):
    """负责人对某版阶段候选的显式采纳回执，兼作重试幂等记录。"""

    __tablename__ = "biz_stage_sales_confirmation"

    lead_id: Mapped[str] = mapped_column(String(36), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    request_sha256: Mapped[str] = mapped_column(String(64))
    actor_id: Mapped[str] = mapped_column(String(36))
    actor_name: Mapped[str] = mapped_column(String(64))
    judgement_id: Mapped[str] = mapped_column(String(36))
    stage: Mapped[str] = mapped_column(String(32))
    dossier_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    confirmation_note: Mapped[str] = mapped_column(Text)
    selected_items: Mapped[list[dict]] = mapped_column(JSON, default=list)
    corrections: Mapped[list[dict]] = mapped_column(JSON, default=list)
    response: Mapped[dict] = mapped_column(JSON, default=dict)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<StageSalesConfirmation {self.lead_id} {self.judgement_id}>"


class StageTransition(Base, UUIDMixin, TimestampMixin):
    """环节流转记录（含回退），用于算转化率与停留时长。"""

    __tablename__ = "biz_stage_transition"

    lead_id: Mapped[str] = mapped_column(String(36), index=True)
    from_stage: Mapped[str | None] = mapped_column(String(32), nullable=True)
    to_stage: Mapped[str] = mapped_column(String(32))
    transition_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, index=True)
    operator_id: Mapped[str] = mapped_column(String(36), default="")
    operator_name: Mapped[str] = mapped_column(String(64), default="")
    stay_days: Mapped[int] = mapped_column(Integer, default=0)
    basis: Mapped[str | None] = mapped_column(Text, nullable=True)
    remark: Mapped[str | None] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<StageTransition {self.from_stage}->{self.to_stage}>"


class AmountChange(Base, UUIDMixin, TimestampMixin):
    """金额变更记录：改金额**必须填变更依据**，支撑业绩漏斗与加权预测。"""

    __tablename__ = "biz_amount_change"

    lead_id: Mapped[str] = mapped_column(String(36), index=True)
    before_fen: Mapped[int] = mapped_column(Integer, default=0)
    after_fen: Mapped[int] = mapped_column(Integer, default=0)
    reason: Mapped[str] = mapped_column(Text, default="")
    stage: Mapped[str | None] = mapped_column(String(32), nullable=True)
    operator_id: Mapped[str] = mapped_column(String(36), default="")
    operator_name: Mapped[str] = mapped_column(String(64), default="")
    source: Mapped[str] = mapped_column(String(16), default="manual")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<AmountChange {self.before_fen}->{self.after_fen}>"


class RiskAlert(Base, UUIDMixin, TimestampMixin):
    """风险预警：R1–R5 丢单风险 + 卡单/转化率/总时长/节奏窗口/运营风险。"""

    __tablename__ = "biz_risk_alert"

    lead_id: Mapped[str] = mapped_column(String(36), index=True)

    # 预警种类：业务风险（R1–R5）、P1-c 四类商机预警、闭环三 6 类运营预警
    kind: Mapped[str] = mapped_column(String(24), default=AlertKind.BUSINESS_RISK, index=True)
    # 预警所属域：sales=闭环二商机侧 / delivery=闭环三运营交付侧
    domain: Mapped[str] = mapped_column(String(16), default="sales", index=True)
    risk_type: Mapped[str] = mapped_column(
        String(32), default=RiskType.R1_BUDGET_UNCLEAR, index=True
    )
    trigger_rule: Mapped[str | None] = mapped_column(String(255), nullable=True)
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    handler_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    handler_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    handled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    handle_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(16), default="manual")

    # 责任人（系统预警默认推给商机归属人）
    owner_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    owner_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # 幂等键：同一商机同一类预警同一天只生成一条（定时扫描反复跑不会刷屏）
    dedup_key: Mapped[str | None] = mapped_column(String(160), index=True, nullable=True)
    # T09发布快照：创建时锚定R1–R6语义基准（spec/13）的内容摘要。
    # 旧行保持NULL，不回填、不重判；历史预警由此可审计「当时按哪版基准产生」。
    rule_revision: Mapped[str | None] = mapped_column(String(64), nullable=True, default=None)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<RiskAlert {self.risk_type} {self.status}>"
