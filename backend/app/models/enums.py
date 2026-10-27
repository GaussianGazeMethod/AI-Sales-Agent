"""业务枚举与字典常量。

约定：SQLite 无原生 enum，一律存 TEXT，取值由本模块集中定义并在应用层校验。

枚举实现采用 (str, Enum)，保持字符串比较与数据库存储一致：
   - 继承 (str, Enum) → 可直接与字符串比较、可直接入库
   - 重写 __str__ / __format__ → f-string 与 str() 都给出值（与 3.11 的 StrEnum 行为一致）
"""

from __future__ import annotations

from enum import Enum


class StrEnum(str, Enum):
    """Python 3.10 兼容的 StrEnum（取值即字符串）。"""

    def __str__(self) -> str:
        return str(self.value)

    def __format__(self, format_spec: str) -> str:
        return format(str(self.value), format_spec)


class UserRole(StrEnum):
    """系统角色（已确认：市场=销售一体）。"""

    ADMIN = "admin"  # 管理员
    MARKET_STAFF = "market_staff"  # 市场专员（=销售）
    MARKET_MANAGER = "market_manager"  # 市场部管理人员
    DELIVERY_STAFF = "delivery_staff"  # 运营交付专员
    DELIVERY_MANAGER = "delivery_manager"  # 运营部管理人员
    EXECUTIVE = "executive"  # 管理层
    RND = "rnd"  # 产研
    BIZ_SUPPORT = "biz_support"  # 方案商务部门


class DataScope(StrEnum):
    """数据可见范围。"""

    SELF = "self"  # 仅本人
    DEPARTMENT = "department"  # 本部门
    ALL = "all"  # 全部


class LeadSource(StrEnum):
    """线索来源（已确认 8 类，**不含"销售自拓"**——自主拓展是行为，非来源）。"""

    COMPANY_POOL = "company_pool"  # 公司客户池
    REFERRAL = "referral"  # 客户转介绍
    CHANNEL_PARTNER = "channel_partner"  # 渠道商（非目标客户统称"渠道"）
    INTERNAL_REFERRAL = "internal_referral"  # 内部人员介绍
    ASSOCIATION = "association"  # 协会 / 机构
    MARKET_EVENT = "market_event"  # 市场活动（展会/论坛/峰会）
    PUBLIC_DATA = "public_data"  # 公开数据平台（企查查/领英等）
    TELECOM_OPERATOR = "telecom_operator"  # 运营商


class OrgType(StrEnum):
    """客户（机构）类型。"""

    BUREAU = "bureau"  # 教育局
    PUBLIC_SCHOOL = "public_school"  # 公办学校
    PRIVATE_SCHOOL = "private_school"  # 民办学校
    SECONDARY_VOC = "secondary_voc"  # 中职
    HIGHER_VOC = "higher_voc"  # 高职
    UNIVERSITY = "university"  # 高校


class LeadStatus(StrEnum):
    """线索/商机状态（闭环一 5 状态 + 闭环二丢单 + 已签约）。"""

    UNASSIGNED = "unassigned"  # 未指派（含原"新建"）
    ASSIGNED = "assigned"  # 已指派
    FOLLOWING = "following"  # 跟进中
    VALID = "valid"  # 有效商机（= 闭环二环节一）
    INVALID = "invalid"  # 无效商机（闭环一判定无效）
    LOST = "lost"  # 已丢单（闭环二丢单，必须登记 R1–R5 原因）
    SIGNED = "signed"  # 已签约（进入闭环三）


class Stage(StrEnum):
    """闭环二 7 环节。"""

    S1_VALID_OPPORTUNITY = "s1_valid_opportunity"  # 一 有效商机
    S2_CUSTOMER_REACHED = "s2_customer_reached"  # 二 客户触达
    S3_PROPOSAL_QUOTE = "s3_proposal_quote"  # 三 方案报价
    S4_BUDGET_CONFIRMED = "s4_budget_confirmed"  # 四 预算确认
    S5_PROJECT_REVIEWED = "s5_project_reviewed"  # 五 项目过会
    S6_BIDDING = "s6_bidding"  # 六 招标采购
    S7_CONTRACT_SIGNED = "s7_contract_signed"  # 七 合同签约


class RiskType(StrEnum):
    """丢单/流失风险类型。

    R1–R5 为原始 5 类（已确认）；**R6 于 2026-10-11 由业务约定单独新增**：
    演示学校乙案例中"AI 演示崩掉 / 产品不能如期上线 / 客户认为 AI 部署过于激进"，
    本质是**产品能力与客户需求不匹配**，既不是"信任不到"也不是"需求不清"，故独立成类。
    """

    R1_BUDGET_UNCLEAR = "r1_budget_unclear"  # 资金预算不清
    R2_TRUST_MISSING = "r2_trust_missing"  # 信任不到
    R3_DECISION_CHAIN = "r3_decision_chain"  # 决策链不清
    R4_DEMAND_UNCLEAR = "r4_demand_unclear"  # 客户需求不清
    R5_PROJECT_RHYTHM = "r5_project_rhythm"  # 项目节奏不清
    R6_PRODUCT_VALUE_MISMATCH = "r6_product_value_mismatch"  # 产品价值无法匹配客户需求


class FollowMethod(StrEnum):
    """跟进方式（已确认 4 种）。"""

    WECHAT = "wechat"  # 微信联系
    PHONE = "phone"  # 电话联系
    VISIT = "visit"  # 上门拜访
    ONLINE_MEETING = "online_meeting"  # 线上会议


class NextAction(StrEnum):
    """下一步动作（有效性自动判定的关键字段之一）。"""

    REPORT_MEETING = "report_meeting"  # 汇报交流
    WECHAT_MATERIAL = "wechat_material"  # 微信交流看我方资料
    OTHER = "other"  # 其他


class ValidFlag(StrEnum):
    """跟进时填写的"是否有效"。"""

    VALID = "valid"
    INVALID = "invalid"


class ReminderType(StrEnum):
    """待办提醒类型。"""

    FIRST_FOLLOW_DUE = "first_follow_due"  # 即将超时（首次跟进）
    FIRST_FOLLOW_OVERDUE = "first_follow_overdue"  # 已超时
    RECYCLE_DUE = "recycle_due"  # 即将回收
    RECYCLED = "recycled"  # 已回收
    NEXT_FOLLOW_DUE = "next_follow_due"  # 下次跟进到点
    SALES_ACTION = "sales_action"  # 人工确认的销售行动，反馈后完成
    PENDING_CONFIRM = "pending_confirm"  # 待确认（AI 草稿复核）


class ReminderStatus(StrEnum):
    PENDING = "pending"
    DONE = "done"


class AssignmentAction(StrEnum):
    """分配动作。"""

    ASSIGN = "assign"  # 指派
    REASSIGN = "reassign"  # 改派
    RECYCLE = "recycle"  # 回收
    CLAIM = "claim"  # 公海领取


class HolidayKind(StrEnum):
    """法定节假日日历条目类型（闭环一 SLA 依赖：只算工作日）。"""

    HOLIDAY = "holiday"  # 法定节假日（不上班）
    MAKEUP_WORKDAY = "makeup_workday"  # 调休上班日（周末但上班）


class IntakeChannel(StrEnum):
    """录入通道。"""

    MANUAL = "manual"  # PC 表单手工录入
    EXCEL_IMPORT = "excel_import"  # Excel 批量导入
    IM_AGENT = "im_agent"  # IM agent 归集（本期预留）
    AI_INTAKE = "ai_intake"  # AI 录入代理（结构化信息 → 草稿）


class IntakeParseStatus(StrEnum):
    """群消息解析状态。"""

    SUCCESS = "success"
    PENDING_SUPPLEMENT = "pending_supplement"
    UNPARSED = "unparsed"


class DedupStatus(StrEnum):
    """疑似重复裁决结论。"""

    PENDING = "pending"  # 待裁决
    KEPT = "kept"  # 判定为独立记录
    MERGED = "merged"  # 已合并到主记录


class AiDraftStatus(StrEnum):
    """AI 更新草稿状态（ 未确认不生效）。"""

    PENDING = "pending"  # 待确认
    CONFIRMED = "confirmed"  # 已确认并落库
    IGNORED = "ignored"  # 已忽略
    FAILED = "failed"  # 解析失败（需人工补录）


class AiParseMode(StrEnum):
    """AI 解析方式。"""

    LLM = "llm"  # 走大模型
    RULE = "rule"  # 无 key / 调用失败时的规则降级解析


class RiskStatus(StrEnum):
    """风险预警处理状态。"""

    PENDING = "pending"  # 待处理
    PROCESSING = "processing"  # 处理中
    RESOLVED = "resolved"  # 已解除


class DataSource(StrEnum):
    """业务记录来源（区分销售确认、AI草稿确认和AI按规则自动采纳）。"""

    MANUAL = "manual"
    AI_CONFIRMED = "ai_confirmed"
    SALES_CONFIRMED = "sales_confirmed"
    #:  业务规则：环节标准由 AI 从**已提交的跟进信息**抽取判定后直接采纳，
    #: 专员不再手打证据（原则「简化事、赋能人」）。与 ai_confirmed 的区别：
    #: 后者是"AI 草稿 + 人工逐条确认"，这里是"AI 判定 + 引用经宿主逐字核对后采纳"。
    AI_AUTO = "ai_auto"


class AlertKind(StrEnum):
    """预警种类：业务风险（R1–R5，人工/AI 登记） + P1-c 四类系统预警。"""

    BUSINESS_RISK = "business_risk"  # R1–R5 业务风险（人工或 AI 登记）
    STUCK = "stuck"  # 卡单预警：停留超基准且标准未完成
    TOTAL_DURATION = "total_duration"  # 总时长预警：单项目接近 90 天
    RHYTHM_UNCLEAR = "rhythm_unclear"  # 项目节奏不清：既无本环节预计时间，也无客户确认时间
    # 年度节奏窗口「另外的参照」：**仅在**项目长期（默认 14 天）仍无时间承诺、
    # 且当前正处该环节对应的申报/立项/挂网/封账窗口时生成，提醒"错过等一年"。
    RHYTHM_WINDOW = "rhythm_window"
    CONVERSION = "conversion"  # 转化率预警：阶段转化率显著低于基准
    # ── 以下 6 类为闭环三（运营交付侧）预警，见 spec/04 §7 ──
    DELIVERY_DELAY = "delivery_delay"  # 交付延期：阶段停留超期未完成
    LONG_NO_CONTACT = "long_no_contact"  # 长期未联系：运营超过 1 个月未联系
    ACCEPTANCE_DUE = "acceptance_due"  # 验收临近：到期前 15 个工作日
    COMPLAINT_OPEN = "complaint_open"  # 投诉未闭环
    ISSUE_OVERDUE = "issue_overdue"  # 产品问题久拖：派产研后超期未解决
    PROMISE_UNMET = "promise_unmet"  # 承诺未兑现：销售承诺到期未兑现


class ReviewVerdict(StrEnum):
    """评审卡「真执行判定」——这是解决"能力还是态度"的落点。"""

    ABILITY = "ability"  # 能力问题
    ATTITUDE = "attitude"  # 态度问题
    NORMAL = "normal"  # 正常


class ReviewResult(StrEnum):
    """评审卡 5 看每项的结论。"""

    PASS = "pass"  # 通过
    RISK = "risk"  # 有风险
    FAIL = "fail"  # 不通过


class ReportGeneratedBy(StrEnum):
    """周报正文的产生方式。

    `PENDING` 表示"指标已汇总、正文尚未生成"——把"汇总指标"与"生成正文"拆成
    两个独立能力后必须有这个状态：不能因为没正文就谎称是 ai/rule 生成的。
    `RULE` 只保留给历史数据（规则降级已废止）。
    """

    PENDING = "pending"
    AI = "ai"
    RULE = "rule"
    MANUAL = "manual"


class ContractStatus(StrEnum):
    """合同状态（spec/05 表 19）。"""

    EXECUTING = "executing"  # 执行中
    ACCEPTED = "accepted"  # 已验收
    RENEWED = "renewed"  # 已续费
    TERMINATED = "terminated"  # 已终止


class OperationActionType(StrEnum):
    """运营记录的动作类型（spec/05 表 23）。"""

    VISIT = "visit"  # 定期回访
    TRAINING = "training"  # 使用培训
    REPORT = "report"  # 数据报告
    ACTIVITY = "activity"  # 活动运营
    REQUIREMENT = "requirement"  # 需求收集
    COMPLAINT_FOLLOW = "complaint_follow"  # 投诉跟进


class RecordStatus(StrEnum):
    """运营记录 / 需求登记的处理状态。"""

    PENDING = "pending"  # 待处理
    DONE = "done"  # 已完成


class MetricPeriodType(StrEnum):
    """运营指标的统计周期（日活/周活/月活）。"""

    DAY = "day"
    WEEK = "week"
    MONTH = "month"


class IssueType(StrEnum):
    """产品问题与承诺台账的类型（spec/05 表 26）。"""

    PRODUCT_ISSUE = "product_issue"  # 产品问题
    PROMISE = "promise"  # 销售承诺


class IssueStatus(StrEnum):
    """台账状态：登记 → 派单 → 跟踪 → 回复客户 → 闭环。"""

    PENDING = "pending"  # 待处理
    PROCESSING = "processing"  # 处理中
    RESOLVED = "resolved"  # 已解决
    REPLIED = "replied"  # 已回复客户


class HandlerSide(StrEnum):
    """台账负责方（spec/05 表 26）。"""

    RND = "rnd"  # 产研（问题修复）
    SALES = "sales"  # 销售（承诺兑现）


class IssueSource(StrEnum):
    """台账来源（spec/05 表 26）。"""

    COMPLAINT = "complaint"  # 投诉
    VISIT = "visit"  # 回访
    GROUP = "group"  # 群反馈
    ACCEPTANCE = "acceptance"  # 验收


class ComplaintReason(StrEnum):
    """投诉原因分类（spec/04 §9）。"""

    PRODUCT = "product"  # 产品问题（最重要）
    COMMITMENT = "commitment"  # 承诺没兑现
    OTHER = "other"  # 其他


class ComplaintStatus(StrEnum):
    PENDING = "pending"  # 待处理
    PROCESSING = "processing"  # 处理中
    CLOSED = "closed"  # 已闭环


class AcceptanceStatus(StrEnum):
    """验收状态（页面 3.11）。"""

    PENDING = "pending"  # 待发起（服务期到期前 15 个工作日提醒）
    APPLIED = "applied"  # 已提交验收申请
    MEETING = "meeting"  # 验收会/现场核查中
    PASSED = "passed"  # 已通过（客户已签确认书）
    FAILED = "failed"  # 未通过（需整改）


class RenewalStatus(StrEnum):
    """续费状态（页面 3.12；本期只做到期清单 + 评估 + 续费率）。"""

    PENDING = "pending"  # 待评估
    IN_PROGRESS = "in_progress"  # 推进中
    RENEWED = "renewed"  # 已续费
    LOST = "lost"  # 已流失


class RenewalWillingness(StrEnum):
    """续费意愿分档。"""

    HIGH = "high"
    MID = "mid"
    LOW = "low"
