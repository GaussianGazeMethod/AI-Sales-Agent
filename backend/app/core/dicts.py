"""字典与业务基准常量（唯一事实来源）。

内容与 `spec/doing/01-需求基线.md`、`spec/03-闭环二` 已确认口径一致：
- 来源 8 类（不含"销售自拓"）
- 7 环节名称 / 停留时长基准（自然日）/ 成交概率
- 环节标准卡种子数据（含硬门槛）
- 教育/政府项目年度节奏日历（节奏窗口预警用）
"""

from __future__ import annotations

import re
from datetime import date

from app.models.enums import (
    AcceptanceStatus,
    AlertKind,
    AssignmentAction,
    ComplaintReason,
    ComplaintStatus,
    ContractStatus,
    DataScope,
    DataSource,
    DedupStatus,
    FollowMethod,
    HandlerSide,
    HolidayKind,
    IssueSource,
    IssueStatus,
    IssueType,
    LeadSource,
    LeadStatus,
    MetricPeriodType,
    NextAction,
    OperationActionType,
    OrgType,
    RecordStatus,
    ReminderType,
    RenewalStatus,
    RenewalWillingness,
    ReviewResult,
    ReviewVerdict,
    RiskStatus,
    RiskType,
    Stage,
    UserRole,
)

# ══════════════ 标签字典 ══════════════

LEAD_SOURCE_LABELS: dict[str, str] = {
    LeadSource.COMPANY_POOL: "公司客户池",
    LeadSource.REFERRAL: "客户转介绍",
    LeadSource.CHANNEL_PARTNER: "渠道商",
    LeadSource.INTERNAL_REFERRAL: "内部人员介绍",
    LeadSource.ASSOCIATION: "协会/机构",
    LeadSource.MARKET_EVENT: "市场活动（展会/论坛/峰会）",
    LeadSource.PUBLIC_DATA: "公开数据平台",
    LeadSource.TELECOM_OPERATOR: "运营商",
}

ORG_TYPE_LABELS: dict[str, str] = {
    OrgType.BUREAU: "教育局",
    OrgType.PUBLIC_SCHOOL: "公办学校",
    OrgType.PRIVATE_SCHOOL: "民办学校",
    OrgType.SECONDARY_VOC: "中职",
    OrgType.HIGHER_VOC: "高职",
    OrgType.UNIVERSITY: "高校",
}

LEAD_STATUS_LABELS: dict[str, str] = {
    LeadStatus.UNASSIGNED: "未指派",
    LeadStatus.ASSIGNED: "已指派",
    LeadStatus.FOLLOWING: "跟进中",
    LeadStatus.VALID: "有效商机",
    LeadStatus.INVALID: "无效商机",
    LeadStatus.LOST: "已丢单",
    LeadStatus.SIGNED: "已签约",
}

FOLLOW_METHOD_LABELS: dict[str, str] = {
    FollowMethod.WECHAT: "微信联系",
    FollowMethod.PHONE: "电话联系",
    FollowMethod.VISIT: "上门拜访",
    FollowMethod.ONLINE_MEETING: "线上会议",
}

NEXT_ACTION_LABELS: dict[str, str] = {
    NextAction.REPORT_MEETING: "汇报交流",
    NextAction.WECHAT_MATERIAL: "微信交流看我方资料",
    NextAction.OTHER: "其他",
}

RISK_TYPE_LABELS: dict[str, str] = {
    RiskType.R1_BUDGET_UNCLEAR: "R1 资金预算不清",
    RiskType.R2_TRUST_MISSING: "R2 信任不到",
    RiskType.R3_DECISION_CHAIN: "R3 决策链不清",
    RiskType.R4_DEMAND_UNCLEAR: "R4 客户需求不清",
    RiskType.R5_PROJECT_RHYTHM: "R5 项目节奏不清",
    RiskType.R6_PRODUCT_VALUE_MISMATCH: "R6 产品价值无法匹配客户需求",
}

REMINDER_TYPE_LABELS: dict[str, str] = {
    ReminderType.FIRST_FOLLOW_DUE: "即将超时（首次跟进）",
    ReminderType.FIRST_FOLLOW_OVERDUE: "已超时（首次跟进）",
    ReminderType.RECYCLE_DUE: "即将回收",
    ReminderType.RECYCLED: "已回收",
    ReminderType.NEXT_FOLLOW_DUE: "下次跟进到点",
    ReminderType.SALES_ACTION: "销售行动",
    ReminderType.PENDING_CONFIRM: "待确认（AI 草稿）",
}

STAGE_LABELS: dict[str, str] = {
    Stage.S1_VALID_OPPORTUNITY: "一 有效商机",
    Stage.S2_CUSTOMER_REACHED: "二 客户触达",
    Stage.S3_PROPOSAL_QUOTE: "三 方案报价",
    Stage.S4_BUDGET_CONFIRMED: "四 预算确认",
    Stage.S5_PROJECT_REVIEWED: "五 项目过会",
    Stage.S6_BIDDING: "六 招标采购",
    Stage.S7_CONTRACT_SIGNED: "七 合同签约",
}

NOTIFY_CHANNEL_LABELS: dict[str, str] = {
    "inapp": "站内通知",
    "wecom": "企业微信（预留）",
    "dingtalk": "钉钉（预留）",
    "feishu": "飞书（预留）",
}

#: 可与通知模板绑定的业务事件（与 audit_service.notify 的 type_ 对齐）
NOTIFY_EVENT_LABELS: dict[str, str] = {
    "lead_assigned": "线索指派",
    "lead_recycled": "线索回收",
    "lead_lost": "线索判无效 / 丢单",
    "first_follow_due": "首次跟进即将超时",
    "first_follow_overdue": "首次跟进已超时",
    "recycle_due": "即将自动回收",
    "risk_alert": "风险预警（卡单 / 总时长 / 节奏）",
    "conversion_alert": "转化率预警",
    "review_due": "项目评审到期",
    "ai_draft": "AI 草稿待确认",
    "contract_signed": "合同签约（交付接手）",
    "delivery_assigned": "交付指派",
    "handover_confirmed": "交接单确认",
    "issue_assigned": "台账派单",
    "complaint": "投诉登记",
    "complaint_escalated": "投诉升级",
    "system": "系统通知",
}

ROLE_LABELS: dict[str, str] = {
    UserRole.ADMIN: "系统管理员",
    UserRole.MARKET_STAFF: "销售人员（=销售）",
    UserRole.MARKET_MANAGER: "管理人员",
    UserRole.DELIVERY_STAFF: "运营交付专员",
    UserRole.DELIVERY_MANAGER: "运营部管理人员",
    UserRole.EXECUTIVE: "管理层",
    UserRole.RND: "产研",
    UserRole.BIZ_SUPPORT: "方案商务部门",
}

DATA_SCOPE_LABELS: dict[str, str] = {
    DataScope.SELF: "仅本人",
    DataScope.DEPARTMENT: "本部门",
    DataScope.ALL: "全部",
}

ASSIGNMENT_ACTION_LABELS: dict[str, str] = {
    AssignmentAction.ASSIGN: "指派",
    AssignmentAction.REASSIGN: "改派",
    AssignmentAction.RECYCLE: "回收",
    AssignmentAction.CLAIM: "公海领取",
}

DEDUP_STATUS_LABELS: dict[str, str] = {
    DedupStatus.PENDING: "待裁决",
    DedupStatus.KEPT: "判定为独立记录",
    DedupStatus.MERGED: "已合并",
}

RISK_STATUS_LABELS: dict[str, str] = {
    RiskStatus.PENDING: "待处理",
    RiskStatus.PROCESSING: "处理中",
    RiskStatus.RESOLVED: "已解除",
}

HOLIDAY_KIND_LABELS: dict[str, str] = {
    HolidayKind.HOLIDAY: "法定节假日",
    HolidayKind.MAKEUP_WORKDAY: "调休上班日",
}

# ══════════════ 闭环二基准 ══════════════

# 7 环节停留时长基准（自然日），合计 90 天 = 3 个月
STAGE_DURATION_DAYS: dict[str, int] = {
    Stage.S1_VALID_OPPORTUNITY: 7,
    Stage.S2_CUSTOMER_REACHED: 10,
    Stage.S3_PROPOSAL_QUOTE: 15,
    Stage.S4_BUDGET_CONFIRMED: 10,
    Stage.S5_PROJECT_REVIEWED: 20,
    Stage.S6_BIDDING: 20,
    Stage.S7_CONTRACT_SIGNED: 8,
}

# 成交概率（加权预测用）；四/五/六为插值，待业务校正
STAGE_WIN_PROBABILITY: dict[str, float] = {
    Stage.S1_VALID_OPPORTUNITY: 0.05,
    Stage.S2_CUSTOMER_REACHED: 0.125,
    Stage.S3_PROPOSAL_QUOTE: 0.25,
    Stage.S4_BUDGET_CONFIRMED: 0.40,
    Stage.S5_PROJECT_REVIEWED: 0.60,
    Stage.S6_BIDDING: 0.80,
    Stage.S7_CONTRACT_SIGNED: 1.00,
}

STAGE_ORDER: list[str] = [
    Stage.S1_VALID_OPPORTUNITY,
    Stage.S2_CUSTOMER_REACHED,
    Stage.S3_PROPOSAL_QUOTE,
    Stage.S4_BUDGET_CONFIRMED,
    Stage.S5_PROJECT_REVIEWED,
    Stage.S6_BIDDING,
    Stage.S7_CONTRACT_SIGNED,
]

# 转化率基准（预警用）
CONVERSION_BASELINE: dict[str, float] = {
    f"{Stage.S1_VALID_OPPORTUNITY}->{Stage.S2_CUSTOMER_REACHED}": 0.40,
    f"{Stage.S2_CUSTOMER_REACHED}->{Stage.S3_PROPOSAL_QUOTE}": 0.50,
    f"{Stage.S3_PROPOSAL_QUOTE}->{Stage.S7_CONTRACT_SIGNED}": 0.25,
}

# 总时长预警阈值（天）
TOTAL_DURATION_WARNING_DAYS = 90
TOTAL_DURATION_WARNING_RATIO = 0.8


def stage_index(stage: str | None) -> int:
    """返回环节序号（0 基），未知返回 -1。"""
    if stage in STAGE_ORDER:
        return STAGE_ORDER.index(stage)
    return -1


def next_stage(stage: str | None) -> str | None:
    idx = stage_index(stage)
    if idx < 0 or idx >= len(STAGE_ORDER) - 1:
        return None
    return STAGE_ORDER[idx + 1]


def win_probability_of(stage: str | None) -> float:
    return STAGE_WIN_PROBABILITY.get(stage or "", 0.0)


def duration_of(stage: str | None) -> int:
    return STAGE_DURATION_DAYS.get(stage or "", 0)


#: 环节标准完成记录的来源标签（`biz_stage_completion.source`）。
#: `ai_auto` = 业务规则：口径：由 AI 从**已提交的跟进信息**抽取判定并采纳，
#: 依据是跟进原文（宿主逐字核对过），专员不再手打证据。
STANDARD_SOURCE_LABELS: dict[str, str] = {
    DataSource.MANUAL: "人工勾选",
    DataSource.AI_CONFIRMED: "AI 草稿确认后勾选",
    DataSource.SALES_CONFIRMED: "销售确认采纳",
    DataSource.AI_AUTO: "按跟进信息自动判定并采纳",
}


# ══════════════ 环节标准卡种子数据（7 环节 × 标准项）══════════════
# 元组：(环节, 序号, 标准内容, 是否硬门槛, 所需证据提示)

STAGE_STANDARD_SEED: list[tuple[str, str, str, bool, str]] = [
    # 一 有效商机
    (
        Stage.S1_VALID_OPPORTUNITY,
        "1",
        "客户信息真实正确（名称、地址、机构类型经核实）",
        False,
        "核实记录或截图",
    ),
    (
        Stage.S1_VALID_OPPORTUNITY,
        "2",
        "拿到关键决策人（董事长/校长/中层主任）的直接联系方式",
        True,
        "决策人姓名+电话/微信",
    ),
    (
        Stage.S1_VALID_OPPORTUNITY,
        "3",
        "完成电话触达，或添加微信并发送公司及产品简介",
        False,
        "通话记录/微信截图",
    ),
    # 二 客户触达
    (
        Stage.S2_CUSTOMER_REACHED,
        "1",
        "上门拜访 / 客户来访 / 线上会议，向关键决策人或中层汇报方案",
        False,
        "拜访/会议记录（时间+对象）",
    ),
    (
        Stage.S2_CUSTOMER_REACHED,
        "2",
        "输出《客户需求分析报告》（需求痛点、场景与范围；预算未知标待确认）",
        True,
        "AI整理或人工需求报告 + 客户需求事实依据",
    ),
    (
        Stage.S2_CUSTOMER_REACHED,
        "3",
        "与客户约定下一步见面沟通",
        False,
        "约定时间与形式",
    ),
    # 三 方案报价
    (
        Stage.S3_PROPOSAL_QUOTE,
        "1",
        "针对初步需求完成建设方案与报价",
        False,
        "方案与报价单文件",
    ),
    (
        Stage.S3_PROPOSAL_QUOTE,
        "2",
        "与客户反复确认设计方案和报价",
        True,
        "确认记录/邮件/微信截图",
    ),
    # 四 预算确认
    (
        Stage.S4_BUDGET_CONFIRMED,
        "1",
        "围绕最终需求提供最终解决方案",
        False,
        "最终方案文件",
    ),
    (
        Stage.S4_BUDGET_CONFIRMED,
        "2",
        "与关键决策人再次确认经费",
        True,
        "确认人+时间+金额",
    ),
    # 五 项目过会
    (
        Stage.S5_PROJECT_REVIEWED,
        "1",
        "协助客户编写立项报告",
        False,
        "立项报告文件",
    ),
    (
        Stage.S5_PROJECT_REVIEWED,
        "2",
        "获得立项会结果，并针对结果制定下一步",
        True,
        "立项会结果记录",
    ),
    # 六 招标采购
    (
        Stage.S6_BIDDING,
        "1",
        "准备招投标文件及方案 / 直接采购材料",
        False,
        "投标文件 / 直接采购合同与建设方案",
    ),
    (
        Stage.S6_BIDDING,
        "2",
        "按实际采购要求配合投标答辩或处理直接采购审核意见（按需适用）",
        False,
        "答辩记录 / 法务及采购审核意见",
    ),
    (
        Stage.S6_BIDDING,
        "3",
        "获得招投标结果 / 直接采购审核通过结果",
        True,
        "中标通知 / 直接采购审核通过记录",
    ),
    # 七 合同签约
    (
        Stage.S7_CONTRACT_SIGNED,
        "1",
        "核对合同条款，价格/付款方式/验收标准达成一致",
        True,
        "合同评审记录",
    ),
    (Stage.S7_CONTRACT_SIGNED, "2", "完成双方内部评审流程", False, "双方评审意见"),
    (
        Stage.S7_CONTRACT_SIGNED,
        "3",
        "完成双方签约并承接交付交接（交付启动另行推进）",
        False,
        "合同扫描件/签约照片",
    ),
]

# ══════════════ 教育/政府项目年度节奏日历（节奏窗口预警）══════════════

RHYTHM_WINDOWS: list[tuple[int, int, str, str]] = [
    (7, 10, "次年预算申报关键窗口", "错过等一年，务必确认本年申报是否已提交"),
    (11, 12, "预算上报审核 / 12 月中下旬财政封账", "封账期签约付款受阻，环节七须赶在封账前"),
    (1, 3, "预算批复、项目立项", "环节五（项目过会）高发期"),
    (3, 6, "挂网招标、采购执行", "环节六（招标采购）高发期"),
    (9, 12, "验收、结算（赶在封账前付款）", "回款关键期"),
]


def current_rhythm_window(today: date | None = None) -> dict | None:
    """返回今天所处的年度节奏窗口（没有则 None）。"""
    today = today or date.today()
    for start_month, end_month, title, hint in RHYTHM_WINDOWS:
        if start_month <= today.month <= end_month:
            return {"month": today.month, "title": title, "hint": hint}
    return None


# ══════════════ P1-c 预警 / P1-d 评审与回款 ══════════════

#: 6.8 通知模板里**已真正接线**的事件：这些事件发通知时会套用模板。
#: 其余模板为预留（例：首次跟进/回收/AI 草稿当前是"待办提醒"而非站内通知）。
#:  新增发送点时（`notify(event=...)`）必须同步加到这里，页面据此判断模板是否生效。
WIRED_NOTIFY_EVENTS: set[str] = {
    "lead_assigned",
    "risk_alert",
    "conversion_alert",
    "contract_signed",
    "delivery_assigned",
    "handover_confirmed",
    "issue_assigned",
    "complaint",
    "complaint_escalated",
    # 手工/自动回收通知（spec/02 §8「回收之后…通知本人 + 管理人员」）
    "recycle_due",
}

#: 预留模板的原因说明（页面直接展示，避免"改了没反应"的黑盒）
RESERVED_NOTIFY_EVENTS: dict[str, str] = {
    "first_follow_due": "首次跟进提醒当前是「待办提醒」，不发站内通知",
    "first_follow_overdue": "首次跟进超时当前是「待办提醒」，不发站内通知",
    "ai_draft": "AI 草稿走「待确认列表 + 待办提醒」，不发站内通知",
}

ALERT_KIND_LABELS: dict[str, str] = {
    # 闭环二（商机侧）
    AlertKind.BUSINESS_RISK: "业务风险（R1–R6）",
    AlertKind.STUCK: "卡单预警",
    AlertKind.TOTAL_DURATION: "总时长预警",
    AlertKind.RHYTHM_WINDOW: "年度节奏窗口参照",
    AlertKind.RHYTHM_UNCLEAR: "项目节奏不清预警",
    AlertKind.CONVERSION: "转化率预警",
    # 闭环三（运营交付侧）
    AlertKind.DELIVERY_DELAY: "交付延期预警",
    AlertKind.LONG_NO_CONTACT: "长期未联系预警",
    AlertKind.ACCEPTANCE_DUE: "验收临近预警",
    AlertKind.COMPLAINT_OPEN: "投诉未闭环预警",
    AlertKind.ISSUE_OVERDUE: "产品问题久拖预警",
    AlertKind.PROMISE_UNMET: "承诺未兑现预警",
}

#: 预警域（区分闭环二/闭环三，页面按域展示）
ALERT_DOMAIN_LABELS: dict[str, str] = {
    "sales": "商机侧（闭环二）",
    "delivery": "运营侧（闭环三）",
}

REVIEW_VERDICT_LABELS: dict[str, str] = {
    ReviewVerdict.ABILITY: "能力问题",
    ReviewVerdict.ATTITUDE: "态度问题",
    ReviewVerdict.NORMAL: "正常",
}

REVIEW_RESULT_LABELS: dict[str, str] = {
    ReviewResult.PASS: "通过",
    ReviewResult.RISK: "有风险",
    ReviewResult.FAIL: "不通过",
}

# 评审卡「5 看」（管理者过项目时逐项判定）
REVIEW_ITEMS: list[dict[str, str]] = [
    {
        "key": "m1_decision_maker",
        "code": "M1",
        "label": "关键决策者",
        "hint": "是不是/有没有接触到关键决策者",
    },
    {
        "key": "m2_intent_budget",
        "code": "M2",
        "label": "意向与资金",
        "hint": "有没有意向、有没有资金",
    },
    {
        "key": "m3_demand_confirm",
        "code": "M3",
        "label": "需求与确认人",
        "hint": "需求是什么、跟谁确认过",
    },
    {
        "key": "m4_stakeholders",
        "code": "M4",
        "label": "干系人识别",
        "hint": "项目相关干系人识别全了吗",
    },
    {
        "key": "m5_unknown_competitor",
        "code": "M5",
        "label": "未知竞品",
        "hint": "有没有我们不知道的竞争对手",
    },
]

PAYMENT_METHODS: dict[str, str] = {
    "bank": "银行转账",
    "check": "支票",
    "cash": "现金",
    "other": "其他",
}

PAYMENT_PERIODS: dict[str, str] = {
    "first": "首款",
    "second": "二期",
    "third": "三期",
    "final": "尾款",
    "full": "全款",
}

# 预警规则默认值（页面 6.7 可改，服务层通过 rule_config_service 读取）
RULE_DEFAULTS: list[dict] = [
    {
        "key": "stuck_warn_ratio",
        "value": "1.0",
        "value_type": "float",
        "label": "卡单预警：停留超基准的倍数",
        "group": "risk",
        "unit": "倍",
        "description": "当前环节停留天数 > 基准天数 × 该倍数 时触发卡单预警（1.0 = 一超基准就报）",
        "sort_order": 10,
    },
    {
        "key": "stuck_require_unfinished",
        "value": "1",
        "value_type": "int",
        "label": "卡单预警：需完成标准未全完成",
        "group": "risk",
        "unit": "0/1",
        "description": (
            "1 = 只有「停留超基准 且 完成标准未全部完成」才报卡单（spec/03 §8 口径）；0 = 只看停留"
        ),
        "sort_order": 20,
    },
    {
        "key": "total_duration_warn_days",
        "value": "90",
        "value_type": "int",
        "label": "总时长预警：项目总天数",
        "group": "risk",
        "unit": "天",
        "description": "7 环节合计基准 90 天（3 个月）",
        "sort_order": 30,
    },
    {
        "key": "total_duration_warn_ratio",
        "value": "0.8",
        "value_type": "float",
        "label": "总时长预警：提前报警比例",
        "group": "risk",
        "unit": "比例",
        "description": "进入闭环二后的天数 ≥ 总天数 × 该比例 时报警（0.8 = 72 天开始报）",
        "sort_order": 40,
    },
    {
        "key": "conversion_warn_ratio",
        "value": "0.7",
        "value_type": "float",
        "label": "转化率预警：低于基准的比例",
        "group": "risk",
        "unit": "比例",
        "description": "实际阶段转化率 < 基准 × 该比例 时报警（0.7 = 低于基准七成）",
        "sort_order": 50,
    },
    {
        "key": "conversion_min_sample",
        "value": "5",
        "value_type": "int",
        "label": "转化率预警：最小样本量",
        "group": "risk",
        "unit": "条",
        "description": "分母小于该值时不报警，避免小样本误报",
        "sort_order": 60,
    },
    {
        "key": "stuck_missing_review_days",
        "value": "14",
        "value_type": "int",
        "label": "待评审判定：多久没评审算待评审",
        "group": "review",
        "unit": "天",
        "description": (
            "进入闭环二后，超过该天数未评审（或已到下次评审时间）的商机进入「待评审」清单"
        ),
        "sort_order": 80,
    },
]


def review_item_labels() -> list[dict[str, str]]:
    """5 看的展示用清单。"""
    return REVIEW_ITEMS


# ══════════════ 闭环三（P2）：交付与客户成功 ══════════════

CONTRACT_STATUS_LABELS: dict[str, str] = {
    ContractStatus.EXECUTING: "执行中",
    ContractStatus.ACCEPTED: "已验收",
    ContractStatus.RENEWED: "已续费",
    ContractStatus.TERMINATED: "已终止",
}

OPERATION_ACTION_LABELS: dict[str, str] = {
    OperationActionType.VISIT: "定期回访",
    OperationActionType.TRAINING: "使用培训",
    OperationActionType.REPORT: "数据报告",
    OperationActionType.ACTIVITY: "活动运营",
    OperationActionType.REQUIREMENT: "需求收集",
    OperationActionType.COMPLAINT_FOLLOW: "投诉跟进",
}

RECORD_STATUS_LABELS: dict[str, str] = {
    RecordStatus.PENDING: "待处理",
    RecordStatus.DONE: "已完成",
}

METRIC_PERIOD_LABELS: dict[str, str] = {
    MetricPeriodType.DAY: "日活",
    MetricPeriodType.WEEK: "周活",
    MetricPeriodType.MONTH: "月活",
}

ISSUE_TYPE_LABELS: dict[str, str] = {
    IssueType.PRODUCT_ISSUE: "产品问题",
    IssueType.PROMISE: "销售承诺",
}

ISSUE_STATUS_LABELS: dict[str, str] = {
    IssueStatus.PENDING: "待处理",
    IssueStatus.PROCESSING: "处理中",
    IssueStatus.RESOLVED: "已解决",
    IssueStatus.REPLIED: "已回复客户",
}

HANDLER_SIDE_LABELS: dict[str, str] = {
    HandlerSide.RND: "产研",
    HandlerSide.SALES: "销售",
}

ISSUE_SOURCE_LABELS: dict[str, str] = {
    IssueSource.COMPLAINT: "投诉",
    IssueSource.VISIT: "回访",
    IssueSource.GROUP: "群反馈",
    IssueSource.ACCEPTANCE: "验收",
}

COMPLAINT_REASON_LABELS: dict[str, str] = {
    ComplaintReason.PRODUCT: "产品问题",
    ComplaintReason.COMMITMENT: "承诺没兑现",
    ComplaintReason.OTHER: "其他",
}

COMPLAINT_STATUS_LABELS: dict[str, str] = {
    ComplaintStatus.PENDING: "待处理",
    ComplaintStatus.PROCESSING: "处理中",
    ComplaintStatus.CLOSED: "已闭环",
}

ACCEPTANCE_STATUS_LABELS: dict[str, str] = {
    AcceptanceStatus.PENDING: "待发起",
    AcceptanceStatus.APPLIED: "已提交申请",
    AcceptanceStatus.MEETING: "验收中",
    AcceptanceStatus.PASSED: "已通过",
    AcceptanceStatus.FAILED: "未通过",
}

RENEWAL_STATUS_LABELS: dict[str, str] = {
    RenewalStatus.PENDING: "待评估",
    RenewalStatus.IN_PROGRESS: "推进中",
    RenewalStatus.RENEWED: "已续费",
    RenewalStatus.LOST: "已流失",
}

RENEWAL_WILLINGNESS_LABELS: dict[str, str] = {
    RenewalWillingness.HIGH: "高",
    RenewalWillingness.MID: "中",
    RenewalWillingness.LOW: "低",
}

# 10 阶段（spec/04 §4）
DELIVERY_PHASE_LABELS: dict[int, str] = {
    1: "一 项目管理",
    2: "二 项目启动",
    3: "三 项目交付",
    4: "四 场景应用培训",
    5: "五 服务支持",
    6: "六 主动运营",
    7: "七 建立标杆",
    8: "八 项目验收",
    9: "九 项目增购",
    10: "十 续费",
}

# 10 阶段「关键产出材料」（= 天然完成标准），来自 spec/04 §4 表格
# 元组：(阶段, 是否必填, 材料名, 所需证据提示)
DELIVERY_PHASE_MATERIALS: list[tuple[int, bool, str, str]] = [
    # 一 项目管理（贯穿全程）
    (1, True, "项目整体管控计划", "计划文件"),
    (1, False, "项目风险登记册", "登记册文件"),
    (1, False, "项目进度周报/月报", "周报或月报文件"),
    (1, False, "项目复盘报告", "复盘报告文件"),
    # 二 项目启动
    (2, True, "《项目服务清单》与客户逐条核对", "双方核对记录"),
    (2, True, "《项目启动确认单》双方签字", "签字扫描件"),
    (2, True, "《客户档案》建立（会后 1 个工作日）", "客户档案记录"),
    (2, True, "三方项目启动会召开（客户+销售+交付）", "会议记录/签到"),
    (2, False, "服务群建立", "群名称/截图"),
    # 三 项目交付
    (3, True, "平台部署与开户完成（域名/功能清单/权限）", "平台上线信息确认单"),
    (3, True, "平台上线信息确认单双方签字", "签字扫描件"),
    (3, False, "AI 平台交付手册", "手册文件"),
    (3, False, "客户信息收集表", "收集表文件"),
    (3, False, "项目实施计划", "计划文件"),
    (3, False, "硬件（平板）交付与检测", "送货单核对表 + 硬件检测记录"),
    (3, False, "培训签到表", "签到表扫描件"),
    (3, False, "培训手册", "手册文件"),
    # 四 场景应用培训
    (4, True, "线下培训组织完成（运营主导）", "培训计划/通知 + 签到表"),
    (4, True, "线上培训组织完成（客服主导）", "培训记录"),
    (4, False, "实操培训 PPT", "PPT 文件"),
    (4, False, "用户配置指南", "指南文件"),
    (4, False, "培训任务单", "任务单文件"),
    (4, False, "培训问卷调查", "问卷结果"),
    (4, False, "平台操作手册（标准版）", "手册文件"),
    (4, False, "培训情况记录", "记录文件"),
    # 五 服务支持
    (5, True, "常见问题 FAQ 建立并实时更新", "FAQ 条目"),
    (5, True, "需求登记清单建立（需求收集与同步）", "需求登记记录"),
    (5, False, "迭代视频与更新说明同步客户", "视频/说明文件"),
    # 六 主动运营
    (6, True, "每周数据分析（活跃度异常监控）", "周分析记录"),
    (6, True, "每月回访一线老师 + 满意度评估", "客户回访记录"),
    (6, False, "每季度向决策人汇报", "阶段性运营总结汇报"),
    (6, False, "月度运营分析报告", "报告文件"),
    (6, False, "推送 AI 工具适配资源包", "资源包/推送记录"),
    # 七 建立标杆
    (7, False, "标杆课例打造（省级/国家级公开课）", "公开课打造 SOP + 课例"),
    (7, False, "标杆教师/标杆校案例", "推广先进案例"),
    (7, False, "名校入校参观交流", "邀请函 + 交流总结记录"),
    (7, False, "学校推广活动方案", "活动方案文件"),
    # 八 项目验收
    (8, True, "到期前 15 个工作日提交验收申请", "验收申请函"),
    (8, True, "验收会议 + 现场核查 + 功能测试", "验收资料清单 + 测试记录"),
    (8, True, "《交付验收报告确认书》双方签字", "签字扫描件"),
    (8, False, "问题整改与项目复盘总报告", "复盘总报告"),
    # 九 项目增购
    (9, False, "增购需求分析报告", "分析报告文件"),
    (9, False, "个性化增购方案", "方案文件"),
    (9, False, "增购协议签署", "协议扫描件"),
    # 十 续费
    (10, True, "续费评估（活跃度/满意度/验收结果）", "续费评估报告"),
    (10, False, "续费策略分档方案", "方案文件"),
    (10, False, "向核心负责人汇报并闭环", "汇报 PPT + 结论"),
    (10, False, "续费协议签署", "协议扫描件"),
]

# 标准《项目服务清单》（运营维护；交付物清单可一键生成）
DELIVERY_STANDARD_SERVICE_ITEMS: list[dict] = [
    {
        "name": "平台部署与开户（域名/功能清单/用户权限）",
        "unit": "项",
        "quantity": "1",
        "acceptance_standard": "平台可正常登录，功能清单与权限配置与合同一致",
    },
    {
        "name": "账号开通与权限配置",
        "unit": "套",
        "quantity": "1",
        "acceptance_standard": "学校管理员与教师账号按约定开通并可登录",
    },
    {
        "name": "场景应用培训（线下）",
        "unit": "场",
        "quantity": "1",
        "acceptance_standard": "完成线下培训并有签到表与培训记录",
    },
    {
        "name": "场景应用培训（线上）",
        "unit": "场",
        "quantity": "1",
        "acceptance_standard": "完成线上培训并有培训记录",
    },
    {
        "name": "平台操作手册（标准版）",
        "unit": "份",
        "quantity": "1",
        "acceptance_standard": "交付电子版操作手册",
    },
    {
        "name": "7×12 在线技术支持（微信群/电话）",
        "unit": "年",
        "quantity": "1",
        "acceptance_standard": "服务期内响应及时，有服务记录",
    },
    {
        "name": "月度运营分析报告",
        "unit": "份",
        "quantity": "12",
        "acceptance_standard": "每月提供服务期内的运营数据报告",
    },
    {
        "name": "每季度向决策人汇报",
        "unit": "次",
        "quantity": "4",
        "acceptance_standard": "每季度完成一次阶段性运营总结汇报",
    },
    {
        "name": "平台迭代更新同步",
        "unit": "项",
        "quantity": "1",
        "acceptance_standard": "迭代后提供更新说明并同步客户",
    },
]

# 运营侧预警阈值默认值（页面 6.7 可改）
OPS_RULE_DEFAULTS: list[dict] = [
    {
        "key": "ops_no_contact_days",
        "value": "30",
        "value_type": "int",
        "label": "长期未联系：超过多少天未联系",
        "group": "operation",
        "unit": "天",
        "description": " 已确认：运营超过 1 个月（30 天）未联系客户 → 预警",
        "sort_order": 110,
    },
    {
        "key": "acceptance_due_workdays",
        "value": "15",
        "value_type": "int",
        "label": "验收临近：到期前多少个工作日",
        "group": "operation",
        "unit": "工作日",
        "description": "SOP：服务期到期前 15 个工作日提交验收申请",
        "sort_order": 120,
    },
    {
        "key": "renewal_due_days",
        "value": "90",
        "value_type": "int",
        "label": "续费启动：到期前多少天",
        "group": "operation",
        "unit": "天",
        "description": " 已确认：到期前 90 天启动续费评估",
        "sort_order": 130,
    },
    {
        "key": "complaint_overdue_days",
        "value": "3",
        "value_type": "int",
        "label": "投诉未闭环：超过多少天",
        "group": "operation",
        "unit": "天",
        "description": "投诉登记后超过该天数仍未闭环 → 预警",
        "sort_order": 140,
    },
    {
        "key": "issue_overdue_days",
        "value": "7",
        "value_type": "int",
        "label": "产品问题久拖：派单后超过多少天未解决",
        "group": "operation",
        "unit": "天",
        "description": "派给产研/销售的问题超过该天数未解决 → 预警",
        "sort_order": 150,
    },
    {
        "key": "promise_due_warn_days",
        "value": "7",
        "value_type": "int",
        "label": "承诺未兑现：到期前几天开始提醒",
        "group": "operation",
        "unit": "天",
        "description": "销售承诺的兑现时间临近该天数仍未兑现 → 预警",
        "sort_order": 160,
    },
    {
        "key": "delivery_delay_enabled",
        "value": "0",
        "value_type": "int",
        "label": "交付延期预警开关",
        "group": "operation",
        "unit": "0/1",
        "description": (
            "阶段停留时长基准**本期未设**，故默认关闭；"
            "在 6.6 配好各阶段基准后把这里改成 1 即可启用"
        ),
        "sort_order": 170,
    },
]


# ══════════════ 工具函数 ══════════════


def options(mapping: dict[str, str]) -> list[dict[str, str]]:
    """把标签字典转成前端下拉需要的 [{value,label}]。"""
    return [{"value": k, "label": v} for k, v in mapping.items()]


def label_of(mapping: dict[str, str], value: str | None) -> str:
    if not value:
        return ""
    return mapping.get(value, value)


# 手机号脱敏（列表接口默认脱敏，查看明文需相应权限）
_PHONE_RE = re.compile(r"^(\d{3})\d{4}(\d{4})$")


def mask_phone(phone: str | None) -> str:
    if not phone:
        return ""
    if _PHONE_RE.match(phone):
        return _PHONE_RE.sub(r"\1****\2", phone)
    if len(phone) > 4:
        return phone[:3] + "****" + phone[-2:]
    return "****"
