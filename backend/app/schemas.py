"""Pydantic 输入模型（请求体校验）。输出统一由 `app/serializers.py` 构造。"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.sales_rules import raw_condition_contract_error

# ══════════════ 认证 / 用户 ══════════════


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class RefreshRequest(BaseModel):
    refresh_token: str


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str = Field(min_length=6, max_length=128)


class UserCreate(BaseModel):
    username: str = Field(min_length=2, max_length=64)
    name: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=6, max_length=128)
    role: str
    data_scope: str = "self"
    department: str | None = None
    phone: str | None = None
    email: str | None = None


class UserUpdate(BaseModel):
    name: str | None = None
    role: str | None = None
    data_scope: str | None = None
    department: str | None = None
    phone: str | None = None
    email: str | None = None
    is_active: bool | None = None


class ResetPasswordRequest(BaseModel):
    new_password: str = Field(min_length=6, max_length=128)


class DepartmentUpdate(BaseModel):
    name: str | None = None
    parent_id: str | None = None
    sort_order: int | None = None
    leader_name: str | None = None
    remark: str | None = None


class NotificationTemplateCreate(BaseModel):
    code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=128)
    channel: str = "inapp"
    event: str = ""
    title_tpl: str = Field(min_length=1, max_length=255)
    content_tpl: str = ""
    is_active: bool = True
    remark: str | None = None


class NotificationTemplateUpdate(BaseModel):
    name: str | None = None
    channel: str | None = None
    event: str | None = None
    title_tpl: str | None = None
    content_tpl: str | None = None
    is_active: bool | None = None
    remark: str | None = None


class TemplatePreviewRequest(BaseModel):
    """模板渲染预览：传一组变量，看最终文案。"""

    variables: dict[str, str] = Field(default_factory=dict)


class DepartmentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    parent_id: str | None = None
    sort_order: int = 0
    leader_name: str | None = None
    remark: str | None = None


# ══════════════ 线索 / 联系人 / 跟进 ══════════════


class LeadCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    source: str = Field(description="必须是 8 类来源字典之一（spec/02 §4）")

    @field_validator("source")
    @classmethod
    def _check_source(cls, v: str) -> str:
        # 来源是"来源分布/渠道效果"统计的唯一口径，放进自由文本会直接污染报表
        from app.core import dicts

        if v not in dicts.LEAD_SOURCE_LABELS:
            raise ValueError("来源必须是以下之一：" + "、".join(sorted(dicts.LEAD_SOURCE_LABELS)))
        return v

    province: str = ""
    city: str = ""
    district: str = ""
    org_type: str = "public_school"
    channel_unit: str | None = None
    referrer: str | None = None
    remark: str | None = None

    # 录入时可直接带一个联系人（展会快速录入场景）
    contact_name: str | None = None
    contact_phone: str | None = None
    contact_wechat: str | None = None
    contact_position: str | None = None

    # 允许录入人指定归属人（不填则进未指派池）
    owner_id: str | None = None

    # ── 客户-项目两层──
    # 同一客户可有多个项目商机；project_name 用于区分（如「教学平台试点项目」）
    project_name: str | None = None
    # 指定客户（不填则按客户名自动匹配已有客户，没有就新建）
    customer_id: str | None = None


class LeadUpdate(BaseModel):
    name: str | None = None
    source: str | None = None
    province: str | None = None
    city: str | None = None
    district: str | None = None
    org_type: str | None = None
    channel_unit: str | None = None
    referrer: str | None = None
    remark: str | None = None
    budget_amount_fen: int | None = Field(default=None, ge=0)
    expected_sign_date: date | None = None
    owner_id: str | None = None
    project_name: str | None = None


class CheckDuplicateRequest(BaseModel):
    name: str = ""
    province: str = ""
    city: str = ""
    district: str = ""


class LeadIdsRequest(BaseModel):
    lead_ids: list[str] = Field(min_length=1)


class AssignRequest(LeadIdsRequest):
    owner_id: str
    remark: str | None = None


class RecycleRequest(LeadIdsRequest):
    remark: str | None = None


class DedupResolveRequest(BaseModel):
    keep_id: str
    merge_id: str | None = None
    action: str = "keep"  # keep / merge
    remark: str | None = None


class ContactCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    position: str | None = None
    phone: str = ""
    wechat: str | None = None
    is_primary: bool = False
    remark: str | None = None


class ContactUpdate(BaseModel):
    name: str | None = None
    position: str | None = None
    phone: str | None = None
    wechat: str | None = None
    is_primary: bool | None = None
    remark: str | None = None


class FollowUpCreate(BaseModel):
    """跟进记录（闭环一）。

     spec/02 §6：跟进**必须包含「是否有效 + 下一步动作」**，两者都是有效性
    自动判定的**判定依据**，因此这里都是必填（此前写成可选，等于把判定依据弄丢了）。
    """

    method: str
    followed_at: datetime | None = None
    content: str = ""
    is_valid: str = Field(description="valid / invalid（闭环一必填）")
    next_action: str = Field(description="report_meeting / wechat_material / other（闭环一必填）")
    next_follow_at: datetime | None = None
    #: 「客户信息是否核实正确」是判有效的**前提**（spec/02 §7）。
    #: 不给默认 True —— 没明确说明就按"未核实"处理（fail-safe），否则这个判定依据形同虚设。
    info_verified: bool | None = None

    @field_validator("is_valid")
    @classmethod
    def _check_valid(cls, v: str) -> str:
        if v not in {"valid", "invalid"}:
            raise ValueError("is_valid 只能是 valid 或 invalid")
        return v

    @field_validator("next_action")
    @classmethod
    def _check_next_action(cls, v: str) -> str:
        if v not in {"report_meeting", "wechat_material", "other"}:
            raise ValueError("next_action 只能是 report_meeting / wechat_material / other")
        return v


# ══════════════ 客户层（客户-项目两层） ══════════════


class CustomerUpdate(BaseModel):
    level: str | None = None
    remark: str | None = None
    owner_id: str | None = None


class CustomerFollowUpCreate(BaseModel):
    """**客户级**跟进（渠道活动 / 跨项目复盘 / 决策链澄清）。

    与项目跟进的区别：**不填**是否有效与下一步动作 —— 它不属于任何单一项目，
    也不进任何环节、不计环节时长。
    """

    method: str = "phone"
    content: str = Field(min_length=1)
    followed_at: datetime | None = None
    next_follow_at: datetime | None = None


# ══════════════ 目标 ══════════════


class TargetSet(BaseModel):
    period_type: str = "month"
    period: str = Field(pattern=r"^\d{4}-\d{2}$")
    user_id: str
    metric: str
    target_value: float = Field(ge=0)


class TargetBatchSet(BaseModel):
    period: str = Field(pattern=r"^\d{4}-\d{2}$")
    items: list[TargetSet]


# ══════════════ 待办 / 通知 ══════════════


class IdsRequest(BaseModel):
    ids: list[str] = Field(min_length=1)


# ══════════════ 闭环二：环节 / 金额 / 风险 ══════════════


class StandardToggleRequest(BaseModel):
    is_done: bool
    evidence: str | None = None
    remark: str | None = None


class StageAdvanceRequest(BaseModel):
    basis: str | None = None
    remark: str | None = None


class StandardConditionItem(BaseModel):
    """复合标准的一个**固定条件**回传项（助手与 PC 共用同一规则修订）。

    这里只是将判定层给出的分项结果原样带回，服务端**不采信**其状态本身：
    `state=completed` 必须能在该商机真实跟进里逐字唯一定位（沿用档案累计的条件可以不带
    新原话，服务端只沿用其已确立的状态与原来源）；核对不过整批 422。
    """

    condition_id: str = Field(min_length=1, max_length=64)
    state: str = Field(min_length=1, max_length=32)
    quote: str = Field(default="", max_length=2000)
    source_key: str = Field(default="", max_length=32)


class StandardAutoApplyItem(BaseModel):
    """一条待采纳的自动判定结果（前端回传，服务端会**重新核对**）。

     `evidence_quote` 必须是该商机真实跟进里的原文；服务端拿它去
    `followup_id` 那条跟进里逐字核对，核对不过整批 422（不允许编证据）。
     并列交付物的标准（如「建设方案与报价」）还须带 `conditions`：
    round-02 之前本 Schema 没有该字段，Pydantic 会**静默丢弃**前端回传的分项条件，
    导致真实 HTTP 路径永远无法采纳固定双条件（R04）。
    """

    standard_id: str
    followup_id: str | None = None
    evidence_quote: str = Field(min_length=1, max_length=2000)
    reason: str | None = None
    conditions: list[StandardConditionItem] = Field(default_factory=list, max_length=2)

    @model_validator(mode="after")
    def _reject_raw_condition_structure(self):
        """：原始条件集合的结构问题在这里就拒绝，不进服务层"修好"。

        重复 ID / 非法状态若放到 `normalize_condition_entries`，会被取首个去重、
        降级成 unknown，再靠档案累计凑齐 AND 变成成功采纳（AC01 不允许）。
        """
        error = raw_condition_contract_error(self.conditions)
        if error:
            raise ValueError(error)
        return self


class StandardAutoApplyRequest(BaseModel):
    """采纳「按跟进信息自动判定」的结果并落库（用户不需要手打证据）。"""

    items: list[StandardAutoApplyItem] = Field(min_length=1)


class SalesStageConfirmationItem(BaseModel):
    standard_id: str = Field(min_length=1, max_length=36)
    confirmation_text: str = Field(min_length=1, max_length=2000)
    followup_id: str | None = Field(default=None, max_length=36)
    source_quote: str | None = Field(default=None, max_length=2000)


class SalesDossierCorrection(BaseModel):
    fact_key: str = Field(min_length=1, max_length=64)
    before_value: str | None = Field(default=None, max_length=2000)
    after_value: str = Field(min_length=1, max_length=2000)
    followup_id: str = Field(min_length=1, max_length=36)
    source_quote: str = Field(min_length=1, max_length=2000)


class SalesStageConfirmationRequest(BaseModel):
    judgement_id: str = Field(min_length=1, max_length=36)
    stage: str = Field(min_length=1, max_length=32)
    expected_dossier_revision: int | None = Field(default=None, ge=0)
    idempotency_key: str = Field(min_length=1, max_length=128)
    confirmation_note: str = Field(min_length=1, max_length=2000)
    items: list[SalesStageConfirmationItem] = Field(min_length=1, max_length=20)
    corrections: list[SalesDossierCorrection] = Field(default_factory=list, max_length=10)


class AmountChangeRequest(BaseModel):
    amount_fen: int = Field(ge=0)
    reason: str = Field(min_length=1, max_length=500)


class RiskUpdateRequest(BaseModel):
    status: str
    handle_note: str | None = None


class RiskCreateRequest(BaseModel):
    risk_types: list[str]
    description: str = ""


# ══════════════ AI 销售助手 ══════════════


class AiIntakeRequest(BaseModel):
    """专员发的一段结构化信息。"""

    message: str = Field(min_length=2)
    lead_id: str | None = None  # 可显式指定客户；不填则由 AI 定位
    channel: str = "chat"


class AiDraftConfirmRequest(BaseModel):
    """确认草稿：允许在确认前做修正（修正后的内容覆盖草稿）。"""

    payload_override: dict | None = None
    remark: str | None = None


class AiDraftBatchConfirmRequest(BaseModel):
    ids: list[str] = Field(min_length=1)


class AiDiagnoseRequest(BaseModel):
    lead_id: str
    question: str | None = None


class ImIntakeRequest(BaseModel):
    """IM agent 归集预留接口（本期只落 ImMessage，不做推送）。"""

    platform: str = "wecom"
    message_id: str
    group_id: str | None = None
    sender_id: str | None = None
    sender_name: str | None = None
    text: str


class ImportMappingConfig(BaseModel):
    """Excel 表头 → 主表字段的映射（前端字段映射步骤提交）。"""

    model_config = ConfigDict(extra="allow")


# ══════════════ P1-c 预警规则 ══════════════


class RuleUpdateItem(BaseModel):
    key: str
    value: str | int | float


class RuleUpdateRequest(BaseModel):
    items: list[RuleUpdateItem] = Field(min_length=1)


class RiskHandleRequest(BaseModel):
    status: str
    handle_note: str | None = None


# ══════════════ P1-d 评审 / 周报 / 回款 / 丢单 ══════════════


class ReviewItem(BaseModel):
    """评审卡 5 看的单项：通过 / 有风险 / 不通过 + 备注。"""

    result: str = "risk"
    note: str | None = None


class ProjectReviewCreate(BaseModel):
    lead_id: str
    m1_decision_maker: ReviewItem | None = None
    m2_intent_budget: ReviewItem | None = None
    m3_demand_confirm: ReviewItem | None = None
    m4_stakeholders: ReviewItem | None = None
    m5_unknown_competitor: ReviewItem | None = None
    verdict: str = "normal"  # ability / attitude / normal
    conclusion: str | None = None
    next_review_at: date | None = None
    remark: str | None = None


class WeeklyReportBodyRequest(BaseModel):
    """AI 生成周报正文的请求。

    `force=True` 表示允许覆盖人工写过的正文（前端需二次确认）。
    """

    force: bool = False


class WeeklyReportGenerateRequest(BaseModel):
    period: str | None = Field(default=None, pattern=r"^\d{4}-W\d{2}$")
    user_id: str | None = None
    use_ai: bool = True


class WeeklyReportUpdateRequest(BaseModel):
    body: str | None = None
    problems: str | None = None
    next_plan: str | None = None


class PaymentCreate(BaseModel):
    lead_id: str
    amount_fen: int = Field(gt=0)
    paid_date: date | None = None
    method: str | None = None
    period_no: str | None = None
    remark: str | None = None


class PaymentUpdate(BaseModel):
    amount_fen: int | None = Field(default=None, gt=0)
    paid_date: date | None = None
    method: str | None = None
    period_no: str | None = None
    remark: str | None = None


class LoseLeadRequest(BaseModel):
    # 刻意不加 min_length：允许空列表进来，由服务层给出中文校验提示（见 loss_service.register_loss）
    lost_reason_types: list[str] = Field(default_factory=list)
    remark: str | None = None


# ══════════════ P2 闭环三：交付 ══════════════


class ContractCreate(BaseModel):
    lead_id: str
    contract_no: str | None = None
    amount_fen: int = Field(default=0, ge=0)
    sign_date: date | None = None
    service_start: date | None = None
    service_end: date | None = None
    acceptance_standard: str | None = None
    has_hardware: bool = False
    attachment: str | None = None
    remark: str | None = None


class ContractUpdate(BaseModel):
    amount_fen: int | None = Field(default=None, ge=0)
    sign_date: date | None = None
    service_start: date | None = None
    service_end: date | None = None
    acceptance_standard: str | None = None
    has_hardware: bool | None = None
    attachment: str | None = None
    status: str | None = None
    delivery_owner_id: str | None = None
    remark: str | None = None


class AssignDeliveryOwnerRequest(BaseModel):
    delivery_owner_id: str
    remark: str | None = None


class DeliveryItemCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    quantity: str | None = None
    unit: str | None = None
    due_date: date | None = None
    acceptance_standard: str | None = None
    is_standard: bool = False
    sort_order: int = 0
    remark: str | None = None


class DeliveryItemUpdate(BaseModel):
    name: str | None = None
    quantity: str | None = None
    unit: str | None = None
    due_date: date | None = None
    acceptance_standard: str | None = None
    is_standard: bool | None = None
    sort_order: int | None = None
    remark: str | None = None


class HandoverCreate(BaseModel):
    handover_user_id: str | None = None
    receiver_id: str | None = None
    delivery_owner_id: str | None = None
    handover_at: datetime | None = None
    standard_note: str | None = None
    nonstandard_note: str | None = None
    remark: str | None = None


class HandoverConfirm(BaseModel):
    role: str = Field(pattern="^(sales|delivery)$")
    remark: str | None = None


class DeliveryPhaseStandardCreate(BaseModel):
    phase: int = Field(ge=1, le=10)
    seq: str | None = None
    content: str = Field(min_length=1, max_length=255)
    is_required: bool = True
    duration_days: int = Field(default=0, ge=0)
    evidence_hint: str | None = None


class DeliveryPhaseStandardUpdate(BaseModel):
    seq: str | None = None
    content: str | None = None
    is_required: bool | None = None
    duration_days: int | None = Field(default=None, ge=0)
    evidence_hint: str | None = None
    sort_order: int | None = None
    is_active: bool | None = None


class DeliveryStageToggle(BaseModel):
    is_done: bool
    evidence: str | None = None
    remark: str | None = None


class StageActionRequest(BaseModel):
    remark: str | None = None


# ══════════════ P2 闭环三：运营 ══════════════


class OperationRecordCreate(BaseModel):
    lead_id: str
    contract_id: str | None = None
    record_date: date | None = None
    action_type: str = "visit"
    content: str = Field(min_length=1)
    next_plan: str | None = None
    status: str = "done"
    satisfaction: str | None = None


class OperationRecordUpdate(BaseModel):
    record_date: date | None = None
    action_type: str | None = None
    content: str | None = None
    next_plan: str | None = None
    status: str | None = None
    satisfaction: str | None = None


class OperationMetricCreate(BaseModel):
    lead_id: str
    contract_id: str | None = None
    period_type: str = "month"
    period: str
    total_accounts: int = Field(default=0, ge=0)
    login_count: int = Field(default=0, ge=0)
    upload_count: int = Field(default=0, ge=0)
    download_count: int = Field(default=0, ge=0)
    file_count: int = Field(default=0, ge=0)
    high_quality_file_count: int = Field(default=0, ge=0)


class OperationMetricUpdate(BaseModel):
    total_accounts: int | None = Field(default=None, ge=0)
    login_count: int | None = Field(default=None, ge=0)
    upload_count: int | None = Field(default=None, ge=0)
    download_count: int | None = Field(default=None, ge=0)
    file_count: int | None = Field(default=None, ge=0)
    high_quality_file_count: int | None = Field(default=None, ge=0)


# ══════════════ P2 闭环三：台账与投诉 ══════════════


class IssueCreate(BaseModel):
    lead_id: str
    contract_id: str | None = None
    type: str = "product_issue"
    content: str = Field(min_length=1)
    source: str = "group"
    proposer: str | None = None
    promised_date: date | None = None
    is_renewal_risk: bool = False
    remark: str | None = None


class IssueUpdate(BaseModel):
    content: str | None = None
    source: str | None = None
    proposer: str | None = None
    handler_side: str | None = None
    due_date: date | None = None
    promised_date: date | None = None
    is_renewal_risk: bool | None = None
    remark: str | None = None


class IssueAssign(BaseModel):
    handler_side: str
    assignee_id: str | None = None
    due_date: date | None = None
    remark: str | None = None


class IssueResolve(BaseModel):
    solution: str = Field(min_length=1)
    remark: str | None = None


class IssueReply(BaseModel):
    """回复客户（C19 闭环的关键一步）。

    `reply_evidence` **必填**：没有回复证据就不算真的回复过客户，
    因此由 schema 直接挡住（而不是靠前端自觉）。
    """

    reply_note: str = Field(min_length=1)
    reply_channel: str = ""
    reply_target: str = ""
    reply_evidence: str = Field(min_length=1, description="回复证据：消息截图/纪要编号/邮件主题等")
    is_renewal_risk: bool | None = None
    remark: str | None = None


class ComplaintCreate(BaseModel):
    lead_id: str
    contract_id: str | None = None
    complainant: str | None = None
    content: str = Field(min_length=1)
    reason: str = "product"
    received_at: datetime | None = None
    handler_id: str | None = None


class ComplaintUpdate(BaseModel):
    content: str | None = None
    reason: str | None = None
    handle_result: str | None = None
    status: str | None = None
    remark: str | None = None


class ComplaintClose(BaseModel):
    handle_result: str = Field(min_length=1)
    remark: str | None = None


class FaqCreate(BaseModel):
    question: str = Field(min_length=1, max_length=255)
    answer: str = ""
    category: str | None = None
    is_standard: bool = False
    tags: list[str] = Field(default_factory=list)


class FaqUpdate(BaseModel):
    question: str | None = None
    answer: str | None = None
    category: str | None = None
    is_standard: bool | None = None
    tags: list[str] | None = None


# ══════════════ P2 闭环三：验收与续费 ══════════════


class AcceptanceCreate(BaseModel):
    contract_id: str
    apply_date: date | None = None
    meeting_date: date | None = None
    result: str | None = None
    signed_date: date | None = None
    attachment: str | None = None
    rectify_note: str | None = None
    remark: str | None = None


class AcceptanceUpdate(BaseModel):
    apply_date: date | None = None
    meeting_date: date | None = None
    signed_date: date | None = None
    attachment: str | None = None
    rectify_note: str | None = None
    remark: str | None = None


class AcceptancePass(BaseModel):
    signed_date: date
    meeting_date: date | None = None
    attachment: str | None = None
    remark: str | None = None


class AcceptanceFail(BaseModel):
    rectify_note: str = Field(min_length=1)


class RenewalCreate(BaseModel):
    contract_id: str
    assess_date: date | None = None
    activity_note: str | None = None
    satisfaction: str | None = None
    acceptance_result: str | None = None
    willingness: str = "mid"
    plan_note: str | None = None
    conclusion: str | None = None
    expected_amount_fen: int = Field(default=0, ge=0)
    status: str = "pending"


class RenewalUpdate(BaseModel):
    assess_date: date | None = None
    activity_note: str | None = None
    satisfaction: str | None = None
    acceptance_result: str | None = None
    willingness: str | None = None
    plan_note: str | None = None
    conclusion: str | None = None
    expected_amount_fen: int | None = Field(default=None, ge=0)
    status: str | None = None


class RenewalConclude(BaseModel):
    status: str = Field(pattern="^(pending|in_progress|renewed|lost)$")
    conclusion: str | None = None
    expected_amount_fen: int | None = Field(default=None, ge=0)


class SalesActionCreate(BaseModel):
    lead_id: str
    content: str = Field(min_length=1, max_length=255)
    remind_at: datetime
    request_id: str = Field(min_length=16, max_length=64, pattern=r"^[a-zA-Z0-9-]+$")
    diagnosis_id: str | None = None
    action_index: int | None = Field(default=None, ge=0)

    @field_validator("content")
    @classmethod
    def content_nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("请填写要执行的行动")
        return value.strip()


class SalesActionFeedback(BaseModel):
    method: str
    content: str = Field(min_length=1, max_length=10000)
    followed_at: datetime
    next_follow_at: datetime | None = None

    @field_validator("content")
    @classmethod
    def feedback_nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("请填写实际执行结果与客户反馈")
        return value.strip()

    @field_validator("method")
    @classmethod
    def valid_method(cls, value: str) -> str:
        from app.models.enums import FollowMethod

        if value not in set(FollowMethod):
            raise ValueError("请选择有效的跟进方式")
        return value
