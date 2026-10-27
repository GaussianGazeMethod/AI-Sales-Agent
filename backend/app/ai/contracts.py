"""AI 输出的类型契约（Agently 结构化输出的**唯一权威**）。

依据 Agently-Skills `skills/agently-request/references/output-control.md`：
- "for Agently 4.1.4.3+, a Pydantic v2 `BaseModel` class may be passed directly to
  `.output(ModelClass, format=...)`, including models with nested `BaseModel` fields and
  lists. Agently projects requiredness, nullability, aliases, enum/literal values,
  length constraints… recursively into the model-facing field requirements"
- "The original class remains the typed acceptance authority"
- 实测教训：把**嵌套对象**用提示词 YAML 的 `$type:` 手写表达时，模型返回的 JSON 完全合法，
  但 Agently 的解析器拿不到目标数据（"No target data in response"），
  连续重试也无法满足 ensure。改用 Pydantic 契约后正常。**嵌套结构一律走本模块。**

为什么这里只放**静态**约束：
- 静态枚举（跟进方式、风险代码 R1–R6）用 `Literal` 表达 → Agently 会投影到模型可见要求并校验；
- **每请求动态**的集合（宿主本次下发的 `C*` / `S*` 选择键）无法用类声明表达，
  由 `app/ai/keys.py` 的 validate_handler 在 Agently 原生重试里校验，宿主随后再复核一次。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

#: 与 app/models/enums.py 的 FollowMethod / RiskType 保持一致
FollowMethodLiteral = Literal["visit", "phone", "wechat", "online_meeting"]
RiskTypeLiteral = Literal[
    "r1_budget_unclear",
    "r2_trust_missing",
    "r3_decision_chain",
    "r4_demand_unclear",
    "r5_project_rhythm",
    # R6：产品价值无法匹配客户需求
    "r6_product_value_mismatch",
]


# ══════════════ N1 客户定位 ══════════════


class IntakeResolve(BaseModel):
    """N1 输出：在宿主下发的候选键中选择。"""

    matched_key: str | None = Field(
        default=None, description="匹配到的候选 key（形如 C3）；无匹配时返回 null"
    )
    reason: str = Field(description="判断依据，一句话，引用 message 中出现的线索")


# ══════════════ N2 更新项抽取 ══════════════


class FollowupDraft(BaseModel):
    """一条跟进记录草稿（模型读消息得到，人工确认后才落库）。"""

    followed_at: str | None = Field(
        default=None,
        description="本次跟进实际发生日期，YYYY-MM-DD；消息未说明则null，不取合同或未来安排日期",
    )
    method: FollowMethodLiteral = Field(description="跟进方式代码，取 follow_methods 中的 code")
    content: str = Field(min_length=1, description="跟进内容摘要，只写 message 中的真实事实")


class IntakeExtract(BaseModel):
    """N2 输出：结构化更新项。"""

    followup: FollowupDraft = Field(description="本次跟进记录")
    matched_standard_keys: list[str] = Field(
        default_factory=list,
        description="命中的完成标准 key 列表，只能来自 standards；无命中给 []",
    )
    amount_yuan: float | None = Field(
        default=None, description="message 中出现的合同金额或预算（单位：元）；没有则为 null"
    )
    amount_reason: str = Field(
        default="", description="金额判断依据，引用 message 原文；无金额时给空字符串"
    )
    risk_types: list[RiskTypeLiteral] = Field(
        default_factory=list, description="命中的风险代码列表，只能来自 risk_catalog；无风险给 []"
    )
    evidence: str | None = Field(
        default=None,
        description=(
            "本次要勾选的标准的**事实依据**：引用或概括 message 中支撑该标准达成的具体事实"
            "（谁、何时、做了什么、有无确认人），控制在 60 字内。"
            " 不要复述整条消息、不要写「客户有意向」这类结论；没有可引用的事实时返回 null"
            "（完成标准必须附证据，宁可保持未完成）"
        ),
    )
    next_node: str | None = Field(
        default=None, description="客户确认的下一关键节点；如汇报、上会，或先与客户确认汇报时间。"
    )
    next_node_time: str | None = Field(
        default=None,
        description="下一节点的预计时间原意；允许暂定、月份或区间，不凑具体日，不强求签约日期。",
    )
    advance_stage: bool = Field(
        default=False, description="是否可进入下一环节（仅明确完成时为 true）"
    )
    summary: str = Field(min_length=1, description="一句话回报：更新了什么、有什么风险、请确认")


# ══════════════ N5 环节标准自动判定 ══════════════
#
#  业务规则：环节完成标准由 AI 从**市场专员已提交的结构化跟进信息**里
# 抽取判定，专员**不再手工登记一遍证据**（原则「简化事、赋能人」）。
# 模型只产出**判定 + 原文引文**；`evidence_quote` 是否真的出现在该条跟进里由宿主逐字核对
# （见 app/services/stage_service.py 的 quote_in_text），核对不过一律 satisfied=false。


class ConditionJudgement(BaseModel):
    """固定双条件之一的判定（语义归模型；条件集合由 `app/core/sales_rules.py` 唯一定义）。

    模型**不得**自行增加、合并、重命名或省略条件；条件 ID 与三态由宿主按规则来源校验，
    非法/重复条目被丢弃后按缺条件处理（不达成），不参与任何关键词语义判断。
    """

    # 2026-10-20 输出契约一致性：这两个叶子改为**有默认值**，使 Agently 的
    # 自动 ensure 不再把 `items[*].conditions[*].condition_id/state` 当成必填路径
    # （原生 4.1.4.8 按字段 requiredness 投影；无默认值即生成 ensure，省略 conditions 的
    # 标准就会在解析后整体失败）。省略列表仍合法；一旦给了条目，下面的 validator 强制
    # 必须给出合法 ID 与三态——**校验没有放宽，只是从"路径必存"改成"条目必须自洽"**。
    condition_id: str = Field(
        default="",
        description=(
            "固定条件 ID，只能取共享规则下发的条件（proposal_completion / quote_completion），"
            "不得自造、改名或合并"
        ),
    )
    state: str = Field(
        default="",
        description="该条件状态：completed / not_completed / unknown（未提及只能 unknown）",
    )
    quote: str = Field(
        default="",
        description=(
            "支撑该状态的**来源原文连续片段**：逐字复制，不得改写、概括、拼接或补字。"
            "completed 必须给出该条件自己的逐字原话；not_completed 有原话时也给出；"
            "unknown 可以留空"
        ),
    )
    source_key: str = Field(
        default="",
        description="该原话所在的来源键（助手取 evidence 的 E 键，PC 取 followups 的 F 键）",
    )

    @model_validator(mode="after")
    def _condition_entry_must_be_self_consistent(self):
        """条目存在就必须自洽：ID 必填、状态限三态。

        这样"省略 conditions"与"给空对象 {}"被明确区分开：前者合法（不适用），
        后者非法（给了条件却没说是什么），不会因为默认值而被悄悄当成 unknown。
        """
        if not self.condition_id.strip():
            raise ValueError(
                "条件条目必须给出 condition_id（不适用条件请省略整个 conditions 列表）"
            )
        if self.state not in {"completed", "not_completed", "unknown"}:
            raise ValueError(f"条件状态必须是三态之一：{self.state!r}")
        return self


class StandardJudgement(BaseModel):
    """对**一条**环节完成标准的判定（模型负责语义，宿主负责引文核对）。"""

    standard_key: str = Field(description="标准 key，只能取 standards 中的 key（形如 S1）")
    satisfied: bool = Field(
        description="该标准是否已由跟进信息支撑达成；没有可引用的原文时必须为 false"
    )
    confidence: float = Field(ge=0, le=1, description="对本次判定的把握度，0–1 之间")
    evidence_quote: str | None = Field(
        default=None,
        description=(
            "支撑该判定的**跟进原文连续片段**：逐字复制 followups 某一条 content 里的原话，"
            "不得改写、概括、拼接或补字。satisfied=true 时必填；没有原文可引用时返回 null"
        ),
    )
    followup_key: str | None = Field(
        default=None,
        description="evidence_quote 出自哪条跟进的 key（形如 F1）；没有引文时返回 null",
    )
    reason: str = Field(
        min_length=1,
        description="判定理由：引用跟进中的事实说明为什么达成，或说明缺什么所以未达成",
    )
    conditions: list[ConditionJudgement] = Field(
        default_factory=list,
        max_length=2,
        description=(
            "仅共享组合规则要求拆分的标准填写：两个固定条件各自的 state 与各自逐字原话/来源键；"
            "其它标准留空。整项达成由宿主按规则来源的 AND 计算，不采信模型自报的 satisfied 总值"
        ),
    )


class AutoJudgeOutput(BaseModel):
    """N5 输出：对本次下发的**每条**标准各给一个判定。"""

    items: list[StandardJudgement] = Field(
        default_factory=list,
        description="standards 中每条标准都要出现一次，一条都不能漏；顺序按 standards 给出",
    )


# ══════════════ N3 项目诊断 ══════════════


class MissingAction(BaseModel):
    """诊断出的一个缺失动作。"""

    standard_key: str = Field(description="standards 里的标准 key")
    why: str = Field(description="为什么这个动作现在关键，依据哪条已给事实")


class RiskFinding(BaseModel):
    """诊断识别到的一条风险。"""

    risk_type: RiskTypeLiteral = Field(description="risk_catalog 中的风险代码")
    evidence: str = Field(description="判断依据，引用已给事实")


class DiagnosisOutput(BaseModel):
    """N3 输出：结构化诊断（宿主随后组装 Markdown）。"""

    stage_diagnosis: str = Field(
        min_length=1, description="当前环节卡在哪里的整体判断，3–5 句，先给结论"
    )
    missing_actions: list[MissingAction] = Field(
        default_factory=list, description="当前环节还缺的关键动作"
    )
    risk_findings: list[RiskFinding] = Field(default_factory=list, description="识别到的风险")
    suggested_actions: list[str] = Field(
        default_factory=list, description="给销售的动作建议，按优先级排序"
    )
    talk_tracks: list[str] = Field(default_factory=list, description="可直接对校方说的话术")
    next_week_plan: list[str] = Field(default_factory=list, description="下周 2–4 条可执行安排")
