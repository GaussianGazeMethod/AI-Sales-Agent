"""共用业务提取契约：原生skill约束语义，宿主控制对象及写入。"""

import logging
import re
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from agently import Agently
from pydantic import BaseModel, Field, StrictBool, StrictInt, ValidationInfo, model_validator

from app.ai.assistant_router import skill_catalog
from app.ai.business_calendar import normalize_relative_due_dates
from app.ai.runtime import MODEL_DIALOGUE, MODEL_EXTRACT, run_structured
from app.core.errors import ValidationError


class QuotedFact(BaseModel):
    quote: str = Field(min_length=1, max_length=2000)


class TextFact(QuotedFact):
    key: Literal[
        "name",
        "source",
        "contact_name",
        "contact_position",
        "intent",
        "need",
        "scenario",
        "scope",
        "success_expectation",
        "implementation_progress",
        "funding_source",
        "budget_range",
        "budget_plan",
        "project_amount_basis",
        "contract_plan",
        "payment_plan",
        "decision_chain",
        "decision_roles",
        "approval_path",
        "procurement_step",
        "next_node",
        "next_node_time",
        "trust_obstacle",
        "product_obstacle",
    ]
    value: str = Field(min_length=1, max_length=2000)
    update_mode: Literal["replace", "extend"] = Field(
        default="replace",
        description=(
            "新增需求要点用extend；客户明确替换原要求才replace。"
            "两者的value都必须是该字段更新后的完整当前文本：保留仍适用的要点，"
            "已被最新事实取代的状态不得再写进去。宿主不拼接旧值，"
            "旧值连同原话与时点由宿主另行留在历史。"
        ),
    )
    time_known: StrictBool = Field(
        default=True,
        description=(
            "仅next_node_time使用：客户给出日期、暂定时间或时间区间时为true；"
            "明确时间未定、待确认且没有时间范围时为false。其他文本字段填true。"
        ),
    )


class BooleanFact(QuotedFact):
    key: Literal[
        "info_verified", "rejected", "contacted", "funding_available", "contract_signed"
    ]
    value: StrictBool


class MoneyFact(QuotedFact):
    key: Literal[
        "proposal_amount_fen",
        "budget_amount_fen",
        "award_amount_fen",
        "contract_amount_fen",
    ]
    value: Annotated[StrictInt, Field(ge=0)]


Fact = Annotated[TextFact | BooleanFact | MoneyFact, Field(discriminator="key")]


class ActionChange(BaseModel):
    operation: Literal["create", "update", "complete"] = Field(
        description=(
            "根据行动目标选择：已有动作实际做完用complete，完成效果另写effect；"
            "同一未来动作的日期或目标补充用update；新的独立动作才create。"
            "已确认下次汇报时间意味着确认时间待办complete，不把它改成未来汇报待办。"
        )
    )
    action_key: str | None
    content: str | None = Field(
        max_length=500,
        description=(
            "未来行动做什么及目标；销售补充同一未来行动目标时，更新原A键content，"
            "不能只改record.next_action而遗漏待办。保留原有效目标，不重复创建。"
        ),
    )
    due_at: str | None = Field(
        description=(
            "结构化业务日期只记录自然日，所有due_at都用YYYY-MM-DDT00:00:00；"
            "原话若含时分也不得写入小时或分钟，原话完整保留在quote/time_expression/time_source_quote。"
            "明确或暂定日期都转换为日级ISO值；相对日期以已确立的业务发生日计算。"
            "销售给出某行动的预计或暂定日期时，即使具体时刻还要再确认，也必须把该日期"
            "写入这一行动待办自身的due_at：只给到日就用当日零点，不编造时分，"
            "暂定性质同时保留在content。另一句“改天再确认具体时间”的日期属于确认动作，"
            "不能替代本待办日期，两个日期归属不得互换；确认待办有日期而本待办留空即为漏承接。"
            "只有本轮与原待办都没有任何日期线索时才算时间待定而null，"
            "不因未确认具体时分而丢弃已有日期。"
            "更新已有A键但本轮没有重新提出日期时必须为null；宿主按该A键继承已保存日期和依据，"
            "不是清空。只有销售本轮明确提出新增日期或改期时才填写日期。"
        )
    )
    # 2026-10-20 日期裁定：由模型逐动作交付**有类型的时间交接**，宿主只做
    # 结构/来源校验与日历计算。三个字段都可省略（有默认值），因此不会像 conditions 那样
    # 被 Agently 的自动 ensure 强制成必填路径。
    time_expression: str = Field(
        default="",
        description=(
            "该动作日期在原文中的日期表达，逐字引用，可保留同句出现的时分原话；"
            "该字段只用于来源与归属核对，due_at仍只写自然日00:00；没有日期时留空，不改写或补算"
        ),
    )
    time_source_quote: str = Field(
        default="",
        description=(
            "仅填写本轮新增或变更日期所在的逐字来源片段；历史来源不可重发或借用。"
            "更新已有待办但本轮没重新提出日期时留空，由宿主按A键继承原日期和date_basis"
        ),
    )
    time_anchor: Literal["business_date", "today", "previous", "explicit", "unknown"] = Field(
        default="unknown",
        description=(
            "该时间表达以哪个业务日为锚点："
            "business_date=本动作所属这次业务发生的日期（本轮 occurred_at）；"
            "today=销售“今天/刚刚”新约定，锚点是录入当天；"
            "previous=回答/补充上一件事，锚点是上一条记录的业务日期；"
            "explicit=表达里自带完整年月日，无需锚点；没有年份的月日仍需已知业务日期确定年份；"
            "unknown=无法判断（宿主不猜、不静默改写）；更新旧A键且本轮未重提日期时应为unknown"
        ),
    )
    customer_confirmed: bool
    effect: str | None = Field(max_length=1000)
    quote: str = Field(min_length=1, max_length=2000)


class ProgressRecord(BaseModel):
    action_goal: str | None = Field(
        description="本次已发生行动的目标；不把未来行动目标覆盖到本次"
    )
    customer_feedback: str | None = Field(
        description="仅客户需求、评价、顾虑、承诺；我方准备进度不属于客户反馈。同次没有新客户反馈时null，由系统保留旧值"
    )
    achievement: str | None = Field(
        description="本次实际进度或目标达成情况；计划不是完成，做了不等于达到预期"
    )
    next_action: str | None = Field(
        description="销售明确的下一步和时间；没有新变化时null，由系统保留原行动"
    )


class BusinessExtraction(BaseModel):
    mode: Literal["live", "history", "simulation"]
    occurred_at: str | None
    new_record: bool
    text_updates: list[TextFact] = Field(max_length=24)
    flag_updates: list[BooleanFact] = Field(
        max_length=5,
        description=(
            "本轮明确的核实、联系、拒绝、经费可用及双方签约事实；"
            "正式采购批准也是funding_available的依据，送审不是；未提及则不输出"
        ),
    )
    amount_updates: list[MoneyFact] = Field(max_length=4)
    actions: list[ActionChange] = Field(max_length=5)
    record: ProgressRecord
    question: str | None = Field(max_length=300)
    acknowledgement: str | None = Field(default=None, max_length=150)


class ContractCompletionAssessment(BaseModel):
    state: Literal["completed", "planned", "in_progress", "unclear"]
    quote: str | None = Field(max_length=2000)
    reason: str = Field(min_length=1, max_length=300)
    question: str | None = Field(max_length=200)


class FollowupInteraction(BaseModel):
    decision: Literal["ask", "close"]
    need_object_quote: str | None = Field(
        max_length=100,
        description="客户明确的被评价/服务/处理的具体业务对象名词原话；采购机构、决策人、产品名称及功能兴趣不算业务对象，未说明则null。只引用message或known.need/scenario，不用项目标题推断",
    )
    need_work_quote: str | None = Field(
        max_length=100,
        description="客户明确希望做的业务工作原话；业务名称本身就足够，不要求另行解释为什么或更细的使用活动。未知null，只引用message或known.need/scenario",
    )
    need_maturity: Literal[
        "not_discussed", "direction_only", "specific", "explicitly_unknown"
    ] = Field(
        description="综合本轮与已知客户事实；项目名称和我方介绍不算客户确认的需求"
    )
    feedback_status: Literal["missing", "provided", "explicitly_unknown"]
    next_action_status: Literal[
        "missing", "incomplete", "provided", "explicitly_pending"
    ]
    next_action_time_status: Literal[
        "not_applicable", "missing", "provided", "explicitly_pending"
    ] = Field(
        description="下一步无行动时not_applicable；有动作未提时间是missing；已有日期/暂定范围是provided；销售明确时间未定或待确认才是explicitly_pending，不能替销售默认待定"
    )
    contract_arrangement_status: Literal[
        "not_discussed", "single", "multiple", "unclear", "explicitly_pending"
    ] = Field(
        description=(
            "只有分阶段建设而未说明一份整体合同还是分期签约安排时unclear；"
            "明确一份整体合同为single；客户已认可分期/多份合同安排时为multiple，"
            "即使具体期数、每期金额或付款节点仍待商议也不能判为未确定；"
            "只有整体合同还是分期签约安排本身被明确说尚未决定时才为explicitly_pending；"
            "未涉及该区别为not_discussed。"
        )
    )
    question_topic: Literal[
        "customer_feedback",
        "customer_need",
        "project_attribution",
        "action_goal",
        "achievement",
        "next_action",
        "financial_fact",
        "contract_arrangement",
        "none",
    ]
    record_confirmation_action: Literal[
        "none", "confirm_current_version", "edit_record", "unclear", "cancel"
    ] = Field(
        default="none",
        description=(
            "仅当context.pending_record_confirmation存在时判断销售是否明确确认当前展示版本；"
            "确认必须回填其中的version_id。修改内容用edit_record，含糊/未确认用unclear，"
            "不能把普通业务认可误作结构化记录确认。"
        ),
    )
    record_confirmation_version: str | None = Field(
        default=None,
        max_length=40,
        description="仅confirm_current_version时逐字返回context中当前待确认版本ID；其他情况为null",
    )
    reason: str = Field(min_length=1, max_length=150)


class ProjectTextFact(TextFact):
    key: Literal[
        "need",
        "scenario",
        "scope",
        "success_expectation",
        "implementation_progress",
        "funding_source",
        "budget_range",
        "budget_plan",
        "project_amount_basis",
        "contract_plan",
        "payment_plan",
        "decision_chain",
        "decision_roles",
        "approval_path",
        "procurement_step",
        "next_node",
        "next_node_time",
        "trust_obstacle",
        "product_obstacle",
    ] = Field(
        description="need客户需求；scenario对象与用途；scope客户确认的实际建设产品/交付内容，禁止填写准备方案、制作Demo、汇报等销售行动；implementation_progress保存方案材料的准备进度"
    )
    knowledge_state: Literal["confirmed", "explicitly_unknown"] = Field(
        description=(
            "本轮明确更新的文本事实状态：客户/销售给出有来源的肯定事实为confirmed；"
            "原话明确表示该事实未知、尚未确定或待确认时为explicitly_unknown。"
            "未提及的字段不要更新；不能仅凭字段缺失或旧值判断为未知。"
        )
    )


class ProjectFlagFact(QuotedFact):
    key: Literal["funding_available", "contract_signed"]
    value: StrictBool


class ProjectFollowupExtraction(BusinessExtraction):
    event_relation: Literal[
        "new_completed_action", "same_action_supplement", "next_action_plan"
    ] = Field(
        description="只有另一次已经发生的拜访/联系/汇报等是new_completed_action；回答追问、补充需求/结果属于same_action_supplement；尚未执行的准备、预约计划或未来目标是next_action_plan。后两者合并当前跟进，不新建记录"
    )
    text_updates: list[ProjectTextFact] = Field(max_length=20)
    flag_updates: list[ProjectFlagFact] = Field(max_length=2)
    interaction: FollowupInteraction
    has_financial_change: StrictBool = Field(
        description="本轮是否新增或更正金额、经费、资金来源、合同或付款安排；用于按需财务核对"
    )

    @model_validator(mode="after")
    def dialogue_is_coherent(self, info: ValidationInfo):
        decision = self.interaction
        if self.new_record != (self.event_relation == "new_completed_action"):
            raise ValueError(
                "只有新的已发生行动才new_record=true；补答及下一步计划必须合并当前记录"
            )
        if decision.need_maturity == "specific" and not (
            decision.need_object_quote and decision.need_work_quote
        ):
            raise ValueError(
                "具体需求须有明确业务对象与用途依据，只有功能兴趣或标杆期待应为direction_only"
            )
        # Agently first builds its Pydantic result object without validation
        # context. Context-dependent financial coherence is enforced in the
        # host validator and in the context-aware validation below.
        if info.context is not None:
            known = info.context.get("known", {})
            if not self.has_financial_change:
                # A repeated statement that reproduces the exact saved contract
                # plan is an idempotent no-op, not a financial change.
                self.text_updates = [
                    update
                    for update in self.text_updates
                    if not (
                        update.key == "contract_plan"
                        and known.get("contract_plan") == update.value
                    )
                ]
        if decision.decision == "ask" and (
            not self.question or decision.question_topic == "none"
        ):
            raise ValueError("ask必须给一个必要问题及对应主题")
        if decision.decision == "close" and (
            self.question or decision.question_topic != "none"
        ):
            raise ValueError("close不能带追问，question=null且question_topic=none")
        return self


def parse_time(value):
    if value is None:
        return None
    try:
        result = datetime.fromisoformat(value)
    except (ValueError, TypeError) as exc:
        raise ValidationError("时间格式无法确认，请说明日期") from exc
    if result.tzinfo is not None:
        from zoneinfo import ZoneInfo

        result = result.astimezone(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)
    return result


def amount_values(quote: str) -> list[int]:
    return [
        int(Decimal(match.group(1)) * (1_000_000 if match.group(2) == "万" else 100))
        for match in re.finditer(r"(?<!\d)(\d+(?:\.\d+)?)\s*(?:多)?(万)?(?:元)?", quote)
        if match.group(2) or quote[match.end() - 1 : match.end()] == "元"
    ]


def literal_source_quote(message: str, quote: str | None) -> str | None:
    """只归一化 Markdown 加粗及空白，并映射回真正的原文片段。"""
    if not quote:
        return None
    if quote in message:
        return quote

    def normalize(value: str):
        cleaned: list[str] = []
        positions: list[int] = []
        index = 0
        while index < len(value):
            if value.startswith("**", index):
                index += 2
                continue
            char = value[index]
            if char.isspace():
                if cleaned and cleaned[-1] != " ":
                    cleaned.append(" ")
                    positions.append(index)
            else:
                cleaned.append(char)
                positions.append(index)
            index += 1
        return "".join(cleaned), positions

    source, positions = normalize(message)
    target, _ = normalize(quote)
    target = target.strip()
    if not target:
        return None
    offset = source.find(target)
    if offset < 0:
        return None
    return message[positions[offset] : positions[offset + len(target) - 1] + 1]


def explicit_amount_sum(quote: str) -> int | None:
    """只计算原话显式加号连接的金额，不推断不同项目数字应相加。"""
    sections = re.split(r"[；;]", quote)
    if len(sections) > 1:
        # The model selects the complete monetary quote. Each selected section
        # must independently be an explicit equation; never count a subtotal
        # again alongside its constituent amounts.
        if not all(re.search(r"[=≈]", section) for section in sections):
            return None
        subtotals = [explicit_amount_sum(section) for section in sections]
        return sum(subtotals) if all(v is not None for v in subtotals) else None
    parts = quote.replace("＋", "+").split("+")
    if len(parts) < 2:
        return None
    groups = [amount_values(part) for part in parts]
    if any(len(g) != 1 for g in groups[:-1]) or len(groups[-1]) not in {1, 2}:
        return None
    total = sum(g[0] for g in groups)
    if len(groups[-1]) == 2 and groups[-1][1] != total:
        return None  # 原文明示的合计与分项不一致，不能悄悄改价。
    return total


def amount_supported(item: dict, context: dict) -> bool:
    """金额必须与原话数字（或明示两期之和）一致，避免模型把万换算错十倍。"""
    numbers = amount_values(str(item.get("quote") or ""))
    value = item.get("value")
    if type(value) is not int:
        return False
    if explicit_amount_sum(str(item.get("quote") or "")) == value:
        return True
    calculation = item.get("calculation") or {}
    if (
        item.get("key") == "project_amount_fen"
        and calculation.get("operation") == "add"
    ):
        known = context.get("known") or context.get("facts") or {}
        base = known.get("project_amount_fen", context.get("project_amount_fen"))
        return (
            type(base) is int
            and base == calculation.get("base_fen")
            and calculation.get("delta_fen") in numbers
            and value == base + calculation["delta_fen"]
        )
    if value in numbers or any(
        value == a + b for i, a in enumerate(numbers) for b in numbers[i + 1 :]
    ):
        return True
    known = context.get("facts") or context.get("known") or {}
    return not numbers and value in [v for v in known.values() if type(v) is int]


def normalize_single_amount(item: dict) -> dict:
    """金额性质由模型选择；原话唯一明确金额的单位换算由宿主负责。"""
    if str(item.get("key", "")).endswith("_fen"):
        numbers = set(amount_values(str(item.get("quote") or "")))
        if len(numbers) == 1:
            return {**item, "value": next(iter(numbers))}
    return item


def _guard_unresolved_contract_amount_conflict(updates: list[dict], finance: dict):
    """Keep an ambiguous signed-contract amount from entering another fact writer."""
    if (finance or {}).get("contract_change_type") != "unclear":
        return updates, False
    protected = {
        "contract_plan",
        "contract_amount_fen",
        "project_amount_fen",
        "project_amount_basis",
    }
    return [item for item in updates if item.get("key") not in protected], True


def _time_source_is_valid_for_action(action: dict, context: dict) -> bool:
    """Accept current wording or an exact date source carried by the same offered action.

    Historical provenance is valid only for an update to that same action, with its
    prior date and customer-confirmation state unchanged. A quote from another action
    or an unbound part of history cannot authorize a new date.
    """
    source_quote = str(action.get("time_source_quote") or "")
    if source_quote and source_quote in str(context.get("message") or ""):
        return True
    if not source_quote or action.get("operation") != "update":
        return False

    action_key = action.get("action_key")
    previous = next(
        (
            item
            for item in context.get("actions") or []
            if item.get("key") == action_key
        ),
        None,
    )
    if previous is None:
        return False
    basis = previous.get("date_basis") or {}
    if source_quote != basis.get("time_source_quote"):
        return False
    expression = str(action.get("time_expression") or "")
    prior_expression = str(basis.get("time_expression") or "")
    if not expression or expression not in source_quote:
        return False
    if prior_expression and expression != prior_expression:
        return False

    previous_due = parse_time(previous.get("due_at"))
    candidate_due = parse_time(action.get("due_at"))
    if candidate_due and (not previous_due or candidate_due.date() != previous_due.date()):
        return False
    if action.get("customer_confirmed") is not previous.get("customer_confirmed"):
        return False
    return previous_due is not None


def _skill_revision_for(skill_id: str, requested_revision: str | None) -> str:
    """Resolve a persisted task revision without silently falling forward."""
    current = skill_catalog()[skill_id]
    revision_ref = requested_revision or current.revision_ref
    prefix = f"skill:{current.skill_id}@sha256:"
    if not isinstance(revision_ref, str) or not revision_ref.startswith(prefix):
        raise ValidationError("会话绑定的业务Skill修订与当前能力不匹配")
    try:
        Agently.skill_library.read_resource(revision_ref, "SKILL.md")
    except Exception as exc:
        raise ValidationError("会话绑定的业务Skill修订不可用，未静默切换版本") from exc
    return revision_ref


async def extract(skill_id: str, context: dict) -> dict:
    revision_ref = _skill_revision_for(skill_id, context.get("skill_revision"))
    # 此节点只读取摘要和本轮事实；完整历史与阶段清单由独立判定节点消费。
    fact_context = {
        key: context[key]
        for key in (
            "message",
            "known",
            "customer",
            "project",
            "organization_type",
            "related_projects",
            "now",
            "record",
            "record_occurred_at",
            "actions",
            "pending_questions",
            "mode",
            "simulation_facts",
            "pending_record_confirmation",
            "pending_project_candidates",
        )
        if key in context
    }
    if skill_id == "sales-project-followup":
        from app.ai.assistant_context import actions, facts

        fact_context["known"] = facts(context.get("known") or context.get("facts"))
        fact_context["actions"] = actions(context.get("actions"))
        fact_context["previous_followup"] = {
            "occurred_at": fact_context.pop("record_occurred_at", None),
            "record": fact_context.pop("record", {}),
        }
        fact_context["asked_topics"] = context.get("asked_topics", [])

    def validate_evidence(value, _context):
        """让 Agently 原生校验重试纠正无引文、重复字段和非法日期。"""

        def fail(reason):
            logging.getLogger(__name__).warning("业务提取证据校验：%s", reason)
            return {"ok": False, "reason": reason}

        try:
            candidate = dict(value)
            interaction = candidate.get("interaction") or {}
            if (
                skill_id == "sales-project-followup"
                and candidate.get("mode") != "simulation"
                and interaction.get("next_action_status") == "provided"
                and (candidate.get("record") or {}).get("next_action")
                and not any(
                    action.get("operation") in {"create", "update"}
                    for action in candidate.get("actions", [])
                )
            ):
                return fail(
                    "明确的下一步必须同时交付待办create/update操作；"
                    "不能只写record.next_action"
                )
            if skill_id == "sales-project-followup":
                # 追问是主节点的语义决策，不因缺字段或主题不符拒绝事实。
                # 这里仅核对下面的来源、格式和操作约束。
                known_need = "\n".join(
                    str(fact_context.get("known", {}).get(key) or "")
                    for key in ("need", "scenario")
                )
                for key in ("need_object_quote", "need_work_quote"):
                    quote = interaction.get(key)
                    if (
                        quote
                        and quote not in context["message"]
                        and quote not in known_need
                    ):
                        return fail(
                            "需求对象与用途依据只能逐字来自本轮message或known.need/scenario，不能从标题或推测得出"
                        )
            updates = [
                *candidate.get("text_updates", []),
                *candidate.get("flag_updates", []),
                *candidate.get("amount_updates", []),
            ]
            for fact in updates:
                item = dict(fact)
                source_quote = literal_source_quote(
                    context["message"], item.get("quote")
                )
                if source_quote is None:
                    logging.getLogger(__name__).warning(
                        "无法定位原文引文 key=%s quote=%r",
                        item.get("key"),
                        str(item.get("quote"))[:120],
                    )
                    return fail(
                        "每项quote必须逐字截取当前message；请缩短引文并核对标点"
                    )
                item["quote"] = source_quote
                item = normalize_single_amount(item)
                if (
                    str(item.get("key", "")).endswith("_fen")
                    and amount_values(item["quote"])
                    and not amount_supported(item, context)
                ):
                    numbers = amount_values(item["quote"])
                    return fail(
                        f"金额字段{item['key']}的value={item.get('value')}与本项quote不符；"
                        f"quote中明示的整数分为{numbers}。1万元=1000000分；"
                        "请修正value或扩展quote覆盖完整依据；合计只能来自明示分期之和。"
                    )
            for action in candidate.get("actions", []):
                action = dict(action)
                offered_actions = {a["key"] for a in context.get("actions", [])}
                if (
                    action.get("operation") in {"update", "complete"}
                    and action.get("action_key") not in offered_actions
                ):
                    return fail(
                        "更新/完成只允许已提供行动键："
                        + ",".join(sorted(offered_actions))
                        + "。没有此前待办时，已做动作只写record，不虚构A键或补建再完成。"
                    )
                if (
                    literal_source_quote(context["message"], action.get("quote"))
                    is None
                ):
                    return fail(
                        "行动quote不可定位："
                        + str(action.get("quote"))[:160]
                        + "。quote只截message中的短连续原话，不拼接、不补主语；content可以概括。"
                        "若只是分析建议而非销售明确的下一步，移除该行动并保留record。"
                    )
                source_quote = str(action.get("time_source_quote") or "")
                expression = str(action.get("time_expression") or "")
                if source_quote and source_quote not in str(context.get("message") or ""):
                    return fail(
                        "不得重发历史日期来源；更新同一A键且本轮未提出日期时，请将日期字段留空，由宿主继承"
                    )
                if source_quote and not _time_source_is_valid_for_action(action, context):
                    return fail(
                        "新日期来源须逐字来自本轮，并绑定正确行动；不能跨待办引用或借旧来源改日期"
                    )
                if expression and not source_quote:
                    return fail("给出日期表达时须提供本轮或同一A键已保存的原文来源")
                if expression and expression not in source_quote:
                    return fail("日期表达必须逐字位于其来源片段中")
                parse_time(action.get("due_at"))
            parse_time(candidate.get("occurred_at"))
            if candidate.get("mode") == "history" and not candidate.get("occurred_at"):
                if candidate.get("new_record") or not context.get("record_occurred_at"):
                    return fail(
                        "新的历史补录必须有业务日期；同次补答可沿用record_occurred_at"
                    )
        except (TypeError, ValueError, ValidationError):
            return fail("事实或行动格式无效，请按契约和原文重新输出")
        return {"ok": True}

    result = await run_structured(
        alias=MODEL_EXTRACT,
        agent_name="assistant-business-extract",
        prompt_file=(
            "assistant_project_followup.yaml"
            if skill_id == "sales-project-followup"
            else "assistant_business_extract.yaml"
        ),
        mappings={"context": fact_context},
        output_model=(
            ProjectFollowupExtraction
            if skill_id == "sales-project-followup"
            else BusinessExtraction
        ),
        validators=[validate_evidence],
        required_skills=[revision_ref],
        validation_context={"known": fact_context.get("known", {})},
    )
    raw_updates = [
        *result.get("text_updates", []),
        *result.get("flag_updates", []),
        *result.get("amount_updates", []),
    ]
    if not result.get("new_record") and context.get("record_occurred_at"):
        model_occurred_at = result.get("occurred_at")
        result["occurred_at"] = context["record_occurred_at"]
        if model_occurred_at != result["occurred_at"]:
            result["_occurred_at_model_candidate"] = model_occurred_at
            result["_occurred_at_source"] = "record_occurred_at"
    elif not result.get("new_record") and not result.get("occurred_at"):
        result["occurred_at"] = context.get("record_occurred_at")
    for item in raw_updates:
        item["quote"] = (
            literal_source_quote(context["message"], item["quote"]) or item["quote"]
        )
    normalizations = [
        {
            "key": item["key"],
            "model_value": item["value"],
            "source_value": normalize_single_amount(item)["value"],
            "quote": item["quote"],
        }
        for item in raw_updates
        if normalize_single_amount(item).get("value") != item.get("value")
    ]
    raw_updates = [normalize_single_amount(item) for item in raw_updates]
    for item in raw_updates:
        if item["key"] == "next_node_time" and item.get("time_known") is False:
            item["value"] = None
    result["_amount_normalizations"] = normalizations
    for item in result["actions"]:
        item["quote"] = (
            literal_source_quote(context["message"], item["quote"]) or item["quote"]
        )
    raw_updates = [
        item
        for item in raw_updates
        if not (
            item["key"].endswith("_fen")
            and not amount_values(item["quote"])
            and not amount_supported(item, context)
        )
    ]
    message = context["message"]
    result["updates"] = raw_updates
    if result["mode"] != "simulation":
        from app.ai import assistant_finance, assistant_project_attribution

        nonfinancial = [
            u
            for u in result["updates"]
            if u["key"] not in assistant_finance.FINANCIAL_KEYS
        ]
        attributed = await assistant_project_attribution.review(
            context,
            nonfinancial,
            record=result.get("record"),
            actions=result.get("actions"),
            pending_candidates=context.get("pending_project_candidates"),
        )

        financial_candidate_present = any(
            update["key"] in assistant_finance.FINANCIAL_REVIEW_CANDIDATE_KEYS
            for update in result["updates"]
        )
        financial = (
            await assistant_finance.review(context)
            if result.get("has_financial_change", True)
            or financial_candidate_present
            else {"updates": [], "assessment": {"not_needed": True}}
        )
        result["updates"] = attributed["updates"] + financial["updates"]
        result["record"] = attributed.get("record", result.get("record"))
        result["actions"] = attributed.get("actions", result.get("actions", []))
        result["_project_attribution"] = attributed["assessment"]
        result["_pending_project_candidates"] = attributed.get(
            "pending_candidates", []
        )
        clarifications = attributed.get("clarifications", [])
        if clarifications and "project_attribution" not in context.get("asked_topics", []):
            unresolved = clarifications[0]
            value = str(unresolved.get("value") or "这项内容")[:80]
            project = str(context.get("project") or "当前项目")
            result["question"] = (
                f"“{value}”应归入当前项目“{project}”，还是同客户的其他项目？"
            )
            interaction = result.get("interaction") or {}
            interaction.update(
                decision="ask",
                question_topic="project_attribution",
                reason="结构化候选的项目归属无法确定，需销售澄清。",
            )
            result["interaction"] = interaction
        result["_finance_review"] = financial["assessment"]
        result["updates"], result["_contract_amount_conflict"] = (
            _guard_unresolved_contract_amount_conflict(
                result["updates"], result["_finance_review"]
            )
        )
    updates_by_key = {}
    for item in result["updates"]:
        previous = updates_by_key.get(item["key"])
        if (
            previous
            and isinstance(previous["value"], str)
            and isinstance(item["value"], str)
        ):
            if item["value"] not in previous["value"]:
                start = min(
                    message.index(previous["quote"]), message.index(item["quote"])
                )
                end = max(
                    message.index(previous["quote"]) + len(previous["quote"]),
                    message.index(item["quote"]) + len(item["quote"]),
                )
                updates_by_key[item["key"]] = {
                    **item,
                    "value": previous["value"] + "；" + item["value"],
                    "quote": message[start:end],
                }
        else:
            updates_by_key[item["key"]] = item
    result["updates"] = list(updates_by_key.values())
    proposed_signature = next(
        (
            u
            for u in result["updates"]
            if u["key"] == "contract_signed" and u["value"] is True
        ),
        None,
    )
    if proposed_signature and result["mode"] != "simulation":
        # 关闭项目后不能继续普通跟进；候选签约事实由独立的小契约核对原话。
        # 该语义决定仍由原生模型完成，不用关键词或本地打分替代。
        assessment = await run_structured(
            alias=MODEL_EXTRACT,
            agent_name="assistant-contract-completion",
            prompt_file="assistant_contract_completion.yaml",
            mappings={"message": context["message"]},
            output_model=ContractCompletionAssessment,
        )
        if assessment["quote"] and assessment["quote"] not in context["message"]:
            raise ValidationError("签约完成核对缺少本轮原文依据")
        if assessment["state"] == "completed":
            if not assessment["quote"]:
                raise ValidationError("签约完成必须有明确原文依据")
            proposed_signature["quote"] = assessment["quote"]
        elif assessment["state"] in {"planned", "in_progress"}:
            # 计划/流转不是一个可覆盖既有已签事实的否定事实。
            result["updates"].remove(proposed_signature)
        else:
            result["updates"].remove(proposed_signature)
            result["question"] = (
                assessment["question"] or "这次已完成双方签约，还是确认后续签约安排？"
            )
        result["_contract_completion"] = assessment
    known = context.get("known") or {}
    active_risks = {
        key
        for key in ("trust_obstacle", "product_obstacle")
        if known.get(key) and not str(known[key]).startswith("已解除：")
    }
    if active_risks and result["mode"] != "simulation":
        from app.ai import assistant_risks

        reviewed = await assistant_risks.review(
            message=message,
            known=known,
            updates=result["updates"],
        )
        # 独立复核拥有已有障碍的状态；unchanged保留旧事实，不被提取草稿覆盖。
        result["updates"] = [
            u for u in result["updates"] if u["key"] not in active_risks
        ] + reviewed
    for item in result["updates"]:
        if item["quote"] not in message:
            raise ValidationError("事实必须有本轮原文依据")
        key, value = item["key"], item["value"]
        if key in {
            "info_verified",
            "rejected",
            "contacted",
            "funding_available",
            "contract_signed",
        }:
            if type(value) is not bool:
                raise ValidationError("明确确认字段必须是布尔值")
        elif key.endswith("_fen"):
            if type(value) is not int or value < 0:
                raise ValidationError("金额必须是非负整数分")
            if not amount_supported(item, context):
                raise ValidationError("金额与原文不一致")
        elif key == "next_node_time" and value is None:
            pass  # 模型明确该节点时间未知；null不会被风险判定误作已知承诺。
        elif not isinstance(value, str) or not value.strip():
            raise ValidationError("事实文本不能为空")
    for action in result["actions"]:
        if action["quote"] not in message:
            raise ValidationError("行动必须来自本轮原文")
        source_quote = str(action.get("time_source_quote") or "")
        expression = str(action.get("time_expression") or "")
        if source_quote and source_quote not in message:
            raise ValidationError("日期来源必须来自本轮原话；继承日期由宿主按原A键读取")
        if source_quote and not _time_source_is_valid_for_action(action, context):
            raise ValidationError("日期来源未能绑定到本轮原话和对应行动")
        if expression and not source_quote:
            raise ValidationError("给了时间表达就必须同时给出它的原文来源片段")
        if expression and source_quote and expression not in source_quote:
            raise ValidationError("时间表达必须逐字出现在它自己的来源片段里")
        parse_time(action["due_at"])
    # 相对日期必须按**业务发生日**锚点复核（G3 双时钟案例）：模型给的是绝对日期，
    # 宿主用日历算法重算；不一致按同一规则规范化并保留原候选，归属不明则不改。
    normalize_relative_due_dates(result, context)
    parse_time(result["occurred_at"])
    if result["mode"] == "history" and not result["occurred_at"]:
        raise ValidationError("历史补录需要明确业务发生日期")
    return result


class FollowupDialogue(BaseModel):
    """主节点继续执行：只决定对话，不生成第二套业务事实。"""

    pending_question_answered: StrictBool | None = Field(
        default=None,
        description=(
            "先判断本轮message是否回答了pending_questions中的具体缺口，已答true、未答false、"
            "没有待答null。比如旧问客户反馈，本轮说校长认可理念则true，可继续问新的需求对象；"
            "旧问汇报日期，本轮只说学校提供标准则false，不能换措辞再问日期。"
            "明确未知、未定或下周再确认也是已答，不强迫补齐。"
        ),
    )
    pending_question_index: Annotated[StrictInt, Field(ge=0)] | None = Field(
        default=None,
        description=(
            "仅在旧问题仍未回答且仍是同一缺口时填其0起始位置；已回答必须null。"
            "换措辞也算同一缺口；销售只补其他事实时可question=null收尾并保留该引用。"
            "确有新的不同歧义才null并提出新问题；无待答或新行动时必须null。"
        ),
    )
    decision_basis: str = Field(
        min_length=1, max_length=150,
        description="本轮选择追问或收尾的简短依据：指出当前哪项客户理解仍不清楚或已经足够，结合本轮原话和已答状态；不是思维链，不复述所有缺字段，不将理由写为客户事实。",
    )
    question_topic: Literal[
        "customer_feedback",
        "customer_need",
        "project_attribution",
        "action_goal",
        "achievement",
        "next_action",
        "financial_fact",
        "contract_arrangement",
        "stage_evidence",
        "none",
    ] = Field(
        description=(
            "先选择本轮仍缺的主题，再写question。客户已给反馈则不选customer_feedback；"
            "需求只有方向且未明确对象/业务工作时优先customer_need，不跳到next_action；"
            "对象和业务工作已答则不再问customer_need；已问未答又补其他事实时可none收尾。"
        )
    )
    question: str | None = Field(default=None, max_length=300)
    acknowledgement: str = Field(
        max_length=150,
        description="简短确认本轮新事实及实际执行；证据不足不是现实未完成，不添加无来源的肯定或否定事实。",
    )

    @model_validator(mode="after")
    def question_shape(self):
        if self.pending_question_answered is True and self.pending_question_index is not None:
            raise ValueError("已回答的旧问题不能再引用为未答缺口；新问题使用null引用")
        if bool(self.question) != (self.question_topic != "none"):
            raise ValueError("有问题须给主题；无问题主题为none")
        return self


async def continue_followup(context, extracted, stage_result):
    """同一对话主节点消费后到的判断；权限、写入与成功状态仍属Host。"""
    from app.ai.assistant_context import actions, facts
    from app.core.dicts import STAGE_LABELS

    receipt = dict(context.get("execution_result") or {})
    # Full evaluator explanations remain in the audit/API receipt, not as customer facts.
    stage_receipt = {
        k: v for k, v in (receipt.get("stage") or {}).items()
        if k in {"before", "after", "status", "gaps"}
    }
    current_stage_key = stage_receipt.get("after") or context.get("stage")
    for key in ("before", "after"):
        if key in stage_receipt:
            stage_receipt[key] = STAGE_LABELS.get(stage_receipt[key], stage_receipt[key])
    receipt["stage"] = stage_receipt
    standards = {s["key"]: s for s in context.get("standards", [])}
    assessments = [
        {
            "standard": standards.get(item["standard_key"], {}).get("content"),
            "stage": STAGE_LABELS.get(standards.get(item["standard_key"], {}).get("stage")),
            "achieved": item["achieved"],
            "evidence_status": "sufficient" if item["achieved"] else "insufficient",
            "quote": item.get("quote"),
        }
        for item in stage_result.get("items", [])
        if standards.get(item["standard_key"], {}).get("stage") == current_stage_key
    ]
    record = {} if extracted.get("new_record") else dict(context.get("record") or {})
    record.update({k: v for k, v in extracted.get("record", {}).items() if v is not None})
    assessment = extracted.get("interaction") or {}
    saved_record = receipt.pop("record", None) or record
    saved_actions = receipt.pop("actions", context.get("actions", []))
    dialogue_context = {
        "message": context["message"],
        "project": context.get("project"),
        "current_stage": stage_receipt.get("after", STAGE_LABELS.get(context.get("stage"))),
        "known_facts": facts(context.get("known", {})),
        "record": saved_record,
        "occurred_at": extracted.get("occurred_at") or context.get("record_occurred_at"),
        "asked_topics": [] if extracted.get("new_record") else context.get("asked_topics", []),
        "pending_questions": []
        if extracted.get("new_record")
        else list(context.get("pending_questions") or []),
        "actions": actions(saved_actions),
        "stage_assessments": assessments,
        "contract_assessment": extracted.get("_contract_completion"),
        "save_status": "saved",
        "execution_result": receipt,
        "requirements_assessment": context.get("requirements_assessment"),
        "understanding": {
            "need": {
                k: assessment[source]
                for k, source in {
                    "maturity": "need_maturity", "object_quote": "need_object_quote",
                    "work_quote": "need_work_quote",
                }.items() if source in assessment
            },
            "feedback_status": assessment.get("feedback_status"),
        },
        "action_readiness": {
            k: assessment[k] for k in {
                "next_action_status", "next_action_time_status", "contract_arrangement_status",
            } if k in assessment
        },
    }

    def validate_pending_reference(value, _):
        index = value.get("pending_question_index")
        if (
            not dialogue_context["pending_questions"]
            and value.get("pending_question_answered") is not None
        ):
            return {"ok": False, "reason": "本轮没有待答问题，pending_question_answered必须null"}
        if index is not None and not 0 <= index < len(dialogue_context["pending_questions"]):
            return {
                "ok": False,
                "reason": "待答缺口引用越界；只能引用本轮pending_questions位置，无待答时null",
            }
        return {"ok": True}

    return await run_structured(
        alias=MODEL_DIALOGUE,
        agent_name="assistant-business-extract",
        prompt_file="assistant_followup_continue.yaml",
        mappings={"context": dialogue_context},
        output_model=FollowupDialogue,
        validators=[validate_pending_reference],
        required_skills=[
            _skill_revision_for(
                "sales-project-followup", context.get("skill_revision")
            )
        ],
    )
