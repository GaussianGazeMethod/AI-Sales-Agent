"""记录核对校验器：行动目标允许逐字承接旧记录目标，事实字段仍禁止复用。

本组用例覆盖同一类来源误拒绝：一次按计划执行
的新拜访（new_record=True），其行动目标本来就是上一次记录里规划的目的，模型
引用 P_action_goal 的原文，却被 allowed() 判成非法来源，重试耗尽后整轮 503。
本测试锁定正确边界：意图字段可承接，事实字段（客户反馈/达成）不得跨次复用。
"""

import json

from app.ai import assistant_record_review as review_mod
from app.ai.assistant_record_review import build_validator


async def _drive_review(monkeypatch, context, extracted, candidate):
    """用真实 build_validator 走一遍 review：fake run_structured 复现 Agently 原生
    “校验失败→重试耗尽即失败”的边界，证明宿主不会把不合规范候选当成功。"""

    async def fake_run_structured(**kwargs):
        validators = kwargs["validators"]
        for _ in range(3):
            results = [v(candidate, None) for v in validators]
            if all(r["ok"] for r in results):
                return review_mod.ReviewedRecord.model_validate(candidate).model_dump()
        raise RuntimeError("记录核对重试耗尽")

    monkeypatch.setattr(review_mod, "run_structured", fake_run_structured)
    return await review_mod.review(context, extracted)


ROUND08_CONTEXT = {
    "message": "学校资料已经用于方案设计和产品Demo设计。",
    "record": {
        "achievement": (
            "已与校长预约汇报时间，调整到10月23日上午8点到校汇报；"
            "初步设计建设方案已完成，产品Demo计划10月21日完成"
        ),
        "action_goal": "与校长预约到校汇报的具体时间",
    },
    "actions": [],
    "record_occurred_at": "2026-10-19T00:00:00",
}
ROUND08_EXTRACTED = {
    "new_record": False,
    "event_relation": "same_action_supplement",
    "occurred_at": "2026-10-19T00:00:00",
    "record": {"achievement": "学校资料已经用于方案设计和产品Demo设计。"},
}


async def test_review_surfaces_fabricated_source_for_exact_record_confirmation(monkeypatch):
    """宿主严格拒绝proof并保留诊断；由服务层要求确认展示版本后才保存业务记录。"""
    candidate = {
        "record": {
            "action_goal": None,
            "customer_feedback": None,
            "achievement": "已向校长完成最终汇报并签署合同。",  # 并非 M/P 逐字内容拼成的可核来源
            "next_action": None,
        },
        "proofs": [
            {
                "field": "achievement",
                "source_key": "M",
                "quote": "已签署建设合同",  # 不在本轮 M 中 -> 逐字来源校验拒绝
            }
        ],
        "corrections": [],
        "corrected_fields": [],
    }
    strict_result = build_validator(
        {
            "M": ROUND08_CONTEXT["message"],
            "P_achievement": ROUND08_CONTEXT["record"]["achievement"],
        },
        ROUND08_EXTRACTED["new_record"],
    )(candidate, None)
    assert strict_result["ok"] is False
    diagnostic = json.loads(strict_result["reason"])
    assert diagnostic["code"] == "proof_quote_not_verbatim"

    result = await _drive_review(monkeypatch, ROUND08_CONTEXT, ROUND08_EXTRACTED, candidate)
    assert result["record"] == candidate["record"]
    assert result["proof_validation"] == {
        "status": "rejected",
        "diagnostic": diagnostic,
    }


async def test_review_accepts_faithful_paraphrase_supplement(monkeypatch):
    """核对层真实链路（AC01 语义保留归模型、宿主不照抄）：模型忠实概括保留全部有效事实
    并给出逐字可核的 P/M 引文时，宿主接受该候选并原样返回——宿主不得因正文未逐字照抄而误拒。"""
    merged = (
        "与校长的预约已确认，10月23日上午8点到校汇报；建设方案初稿已完成，"
        "学校资料已经用于方案设计和产品Demo设计"
    )
    candidate = {
        "record": {
            "action_goal": None,
            "customer_feedback": None,
            "achievement": merged,
            "next_action": None,
        },
        "proofs": [
            {
                "field": "achievement",
                "source_key": "P_achievement",
                "quote": (
                    "已与校长预约汇报时间，调整到10月23日上午8点到校汇报；"
                    "初步设计建设方案已完成"
                ),
            },
            {
                "field": "achievement",
                "source_key": "M",
                "quote": "学校资料已经用于方案设计和产品Demo设计。",
            },
        ],
        "corrections": [],
        "retained_fields": ["achievement"],
        "corrected_fields": [],
    }
    result = await _drive_review(monkeypatch, ROUND08_CONTEXT, ROUND08_EXTRACTED, candidate)
    # 宿主原样执行模型解释后的候选；是否语义完整保留由 M 正反验收判定，非本函数职责
    assert result["record"]["achievement"] == merged


async def test_review_accepts_retained_supplement_candidate(monkeypatch):
    """核对层真实链路：完整保留原有效事实并分别引用 P/M 的候选被接受并返回。"""
    merged = (
        "已与校长预约汇报时间，调整到10月23日上午8点到校汇报；"
        "初步设计建设方案已完成，学校资料已经用于方案设计和产品Demo设计，产品Demo计划10月21日完成"
    )
    candidate = {
        "record": {
            "action_goal": None,
            "customer_feedback": None,
            "achievement": merged,
            "next_action": None,
        },
        "proofs": [
            {
                "field": "achievement",
                "source_key": "P_achievement",
                "quote": (
                    "已与校长预约汇报时间，调整到10月23日上午8点到校汇报；"
                    "初步设计建设方案已完成"
                ),
            },
            {
                "field": "achievement",
                "source_key": "M",
                "quote": "学校资料已经用于方案设计和产品Demo设计。",
            },
        ],
        "corrections": ["同次补答保留预约与方案完成事实"],
        "retained_fields": ["achievement"],
        "corrected_fields": [],
    }
    result = await _drive_review(monkeypatch, ROUND08_CONTEXT, ROUND08_EXTRACTED, candidate)
    assert result["record"]["achievement"] == merged
    # review 把上一记录达成情况暴露为来源，供模型逐字核对保留
    assert result["proofs"][0]["source_key"] == "P_achievement"


SOURCES = {
    "M": "补录2025-11-25项目进展：我方到学校向校方高层汇报解决方案并演示平台，未达到预期",
    "P_action_goal": "与渠道联系人丁沟通，推动一起到校向校方高层汇报交流",
    "P_customer_feedback": "校长认可以往方案理念",
    "P_achievement": "已电话沟通并发送产品介绍",
    "P_next_action": "与渠道联系人丁一起到校向校方高层汇报",
    "A1": "与渠道联系人丁一起到校向校方高层汇报",
}


def _value(proofs, record):
    return {"record": record, "proofs": proofs, "corrections": []}


def test_new_record_action_goal_may_cite_previous_action_goal():
    validate = build_validator(SOURCES, is_new=True)
    value = _value(
        [
            {
                "field": "action_goal",
                "source_key": "P_action_goal",
                "quote": "与渠道联系人丁沟通，推动一起到校向校方高层汇报交流",
            },
            {
                "field": "achievement",
                "source_key": "M",
                "quote": "我方到学校向校方高层汇报解决方案并演示平台，未达到预期",
            },
        ],
        {"action_goal": "与渠道联系人丁一起到校汇报", "achievement": "到校汇报已执行"},
    )
    assert validate(value, None) == {"ok": True}


def test_new_record_must_not_reuse_previous_feedback_or_achievement():
    validate = build_validator(SOURCES, is_new=True)
    value = _value(
        [
            {
                "field": "customer_feedback",
                "source_key": "P_customer_feedback",
                "quote": "校长认可以往方案理念",
            }
        ],
        {"customer_feedback": "校长认可以往方案理念"},
    )
    result = validate(value, None)
    assert result["ok"] is False
    assert json.loads(result["reason"])["code"] == "proof_source_not_allowed"
    assert json.loads(result["reason"])["field"] == "customer_feedback"


def test_nonempty_field_without_proof_still_rejected():
    validate = build_validator(SOURCES, is_new=False)
    value = _value([], {"achievement": "完成到校汇报"})
    result = validate(value, None)
    assert result["ok"] is False
    assert "来源引用" in result["reason"]


def test_quote_must_be_verbatim_from_cited_source():
    validate = build_validator(SOURCES, is_new=True)
    value = _value(
        [
            {
                "field": "achievement",
                "source_key": "M",
                "quote": "我方到学校汇报并演示平台成功获得好评",
            }
        ],
        {"achievement": "到校汇报完成"},
    )
    result = validate(value, None)
    assert result["ok"] is False


def test_quote_cannot_be_fabricated_by_joining_separate_message_fragments():
    message = "本项目9月准备二审材料；Demo属于另一个项目"
    validate = build_validator({"M": message}, is_new=False)
    value = _value(
        [
            {
                "field": "achievement",
                "source_key": "M",
                "quote": "9月准备二审材料Demo属于另一个项目",
            }
        ],
        {"achievement": "9月准备二审材料，Demo属于另一个项目"},
    )

    result = validate(value, None)

    assert result["ok"] is False
    assert json.loads(result["reason"])["code"] == "proof_quote_not_verbatim"


# ---  同次补答：宿主=合法来源验证 + 结构化契约；模型=语义保留/更正 ---
# round-01 曾错误地让 build_validator 用“正文必须逐字包含 P 引文”守卫替模型判语义，
# 结果既误拒忠实概括（来源反例一），又能被无意义短引绕过（来源反例二）。
# 本单正确的职责划分：
#   宿主（D）：逐字来源合法性、非空字段须有 proof、新行动不复用旧事实、更正须落本轮 M；
#   模型（M）：是否把仍有效的旧事实并入新值 / 是否属于明确更正——由提示词约束，
# 边界说明见 来源校验契约：保留来源反例、不改业务期待，只把执行者由
# Python 子串改回模型，并以真实 M 背书。

# 合成同次补答来源：M=本轮补答；P_achievement=上一记录仍有效的达成情况
RETENTION_SOURCES = {
    "M": "学校资料已经用于方案设计和产品Demo设计。",
    "P_achievement": (
        "已与校长预约汇报时间，调整到10月23日上午8点到校汇报；"
        "初步设计建设方案已完成，产品Demo计划10月21日完成"
    ),
    "P_action_goal": "与校长预约到校汇报的具体时间",
    "P_next_action": "10月21日完成产品Demo",
    "P_customer_feedback": "校长要求补充学生评价维度",
}


def test_host_accepts_faithful_paraphrase_of_retained_facts():
    """来源反例一：忠实概括保留全部有效事实、逐字 P/M 引文均可核时，宿主必须接受，
    不得因正文未逐字照抄来源而误拒（语义保留归模型）。"""
    validate = build_validator(RETENTION_SOURCES, is_new=False)
    value = {
        "record": {
            "action_goal": None,
            "customer_feedback": None,
            "achievement": (
                "与校长的预约已确认，10月23日上午8点到校汇报；建设方案初稿已完成，"
                "学校资料已经用于方案设计和产品Demo设计"
            ),
            "next_action": None,
        },
        "proofs": [
            {
                "field": "achievement",
                "source_key": "P_achievement",
                "quote": (
                    "已与校长预约汇报时间，调整到10月23日上午8点到校汇报；"
                    "初步设计建设方案已完成"
                ),
            },
            {
                "field": "achievement",
                "source_key": "M",
                "quote": "学校资料已经用于方案设计和产品Demo设计。",
            },
        ],
        "corrections": [],
        "retained_fields": ["achievement"],
        "corrected_fields": [],
    }
    assert validate(value, None) == {"ok": True}


def test_host_rejects_nonverbatim_source_quote():
    """逐字来源校验：宿主拒绝引文并非逐字来自其声明来源（改写/伪造来源），
    不因‘完整性’放松引文边界；这是合法来源验证，不是语义判断。"""
    validate = build_validator(RETENTION_SOURCES, is_new=False)
    value = {
        "record": {
            "action_goal": None,
            "customer_feedback": None,
            "achievement": "已与校长预约汇报；学校资料已经用于方案设计和产品Demo设计。",
            "next_action": None,
        },
        "proofs": [
            {
                "field": "achievement",
                "source_key": "P_achievement",
                "quote": "已与校长预约汇报，方案已获董事会批准",  # 并非 P_achievement 逐字内容
            },
            {
                "field": "achievement",
                "source_key": "M",
                "quote": "学校资料已经用于方案设计和产品Demo设计。",
            },
        ],
        "corrections": [],
        "corrected_fields": [],
    }
    result = validate(value, None)
    assert result["ok"] is False


def test_host_does_not_adjudicate_semantic_retention():
    """边界记录（对应 来源反例二）：仅凭语法无法区分‘忠实概括保留’与‘丢弃旧事实’。
    故一个逐字来源合法的候选（哪怕正文丢弃了旧达成）由宿主放行——语义保留不是 Python 子串
    的职责，改由提示词约束 + round-02 真实模型正反语义验收证明。此断言固定‘宿主不越权判语义’。"""
    validate = build_validator(RETENTION_SOURCES, is_new=False)
    value = {
        "record": {
            "action_goal": None,
            "customer_feedback": None,
            # 丢弃旧达成，但引文本轮 M 合法：宿主只验来源合法，不判语义（见 ORACLE-ISSUE）
            "achievement": "学校资料已经用于方案设计和产品Demo设计。",
            "next_action": None,
        },
        "proofs": [
            {
                "field": "achievement",
                "source_key": "M",
                "quote": "学校资料已经用于方案设计和产品Demo设计。",
            }
        ],
        "corrections": [],
        "corrected_fields": [],
    }
    # 宿主只保证来源合法；是否“该保留却没保留”属语义，交由 M 验收，宿主不据此拒绝
    assert validate(value, None) == {"ok": True}


def test_same_action_supplement_achievement_retains_prior_facts_accepted():
    """同次补答完整合并仍有效的原达成事实，并分别引用 P_achievement 与 M，必须被接受。"""
    validate = build_validator(RETENTION_SOURCES, is_new=False)
    merged = (
        "已与校长预约汇报时间，调整到10月23日上午8点到校汇报；"
        "初步设计建设方案已完成，学校资料已经用于方案设计和产品Demo设计，"
        "产品Demo计划10月21日完成"
    )
    value = {
        "record": {
            "action_goal": None,
            "customer_feedback": None,
            "achievement": merged,
            "next_action": None,
        },
        "proofs": [
            {
                "field": "achievement",
                "source_key": "P_achievement",
                "quote": (
                    "已与校长预约汇报时间，调整到10月23日上午8点到校汇报；"
                    "初步设计建设方案已完成"
                ),
            },
            {
                "field": "achievement",
                "source_key": "M",
                "quote": "学校资料已经用于方案设计和产品Demo设计。",
            },
        ],
        "corrections": ["同次补答新增资料使用情况，原预约与方案完成事实继续保留"],
        "retained_fields": ["achievement"],
        "corrected_fields": [],
    }
    assert validate(value, None) == {"ok": True}


def test_explicit_correction_must_be_grounded_in_current_turn():
    """结构化契约：corrected_fields 声明更正的字段，其新值必须逐字引用本轮 M；
    只沿用旧记录来源声明更正 → 宿主拒绝（来源合法性，不是语义判断）。"""
    sources = {
        **RETENTION_SOURCES,
        "M": "学校资料已经用于方案设计和产品Demo设计。Demo尚未完成，此前记录有误。",
    }
    validate = build_validator(sources, is_new=False)
    value = {
        "record": {
            "action_goal": None,
            "customer_feedback": None,
            "achievement": "Demo尚未完成，此前记录有误",
            "next_action": None,
        },
        "proofs": [
            {
                "field": "achievement",
                "source_key": "M",
                "quote": "Demo尚未完成，此前记录有误",  # 逐字来自本轮 M
            }
        ],
        "corrections": ["销售明确更正：Demo未完成，此前记录有误"],
        "corrected_fields": ["achievement"],
    }
    assert validate(value, None) == {"ok": True}
    # 声明更正却只引旧 P_achievement、不引本轮 M -> 拒（更正须落在本轮消息上）
    value_bad = {
        "record": {
            "action_goal": None,
            "customer_feedback": None,
            "achievement": "Demo尚未完成，此前记录有误",
            "next_action": None,
        },
        "proofs": [
            {
                "field": "achievement",
                "source_key": "P_achievement",
                "quote": "初步设计建设方案已完成",
            }
        ],
        "corrections": ["销售更正"],
        "corrected_fields": ["achievement"],
    }
    result = validate(value_bad, None)
    assert result["ok"] is False
    assert "本轮 M" in result["reason"]


def test_quote_must_be_verbatim_from_prior_source_on_supplement():
    """保留引用仍需逐字来自 P_ 来源；伪造 P 片段被拒，不为‘完整性’放松引文边界（AC06）。"""
    validate = build_validator(RETENTION_SOURCES, is_new=False)
    merged = (
        "已与校长预约汇报时间，方案初稿完成，产品Demo计划10月21日完成；"
        "学校资料已经用于方案设计和产品Demo设计。"
    )
    value = {
        "record": {"achievement": merged},
        "proofs": [
            {
                "field": "achievement",
                "source_key": "P_achievement",
                "quote": "已与校长预约汇报时间，但方案已经取消",  # 不逐字存在于来源
            },
            {
                "field": "achievement",
                "source_key": "M",
                "quote": "学校资料已经用于方案设计和产品Demo设计。",
            },
        ],
        "corrections": [],
        "corrected_fields": [],
    }
    result = validate(value, None)
    assert result["ok"] is False


def test_customer_feedback_supplement_may_merge_or_be_source_legitimate():
    """客户反馈：合并旧反馈+新反馈并分别逐字引用 P/M 被接受；伪造来源被拒（宿主只验来源合法）。"""
    sources = {**RETENTION_SOURCES, "M": "客户希望周五再看一次"}
    validate = build_validator(sources, is_new=False)
    merged = "校长要求补充学生评价维度；客户希望周五再看一次"
    value_ok = {
        "record": {"customer_feedback": merged},
        "proofs": [
            {
                "field": "customer_feedback",
                "source_key": "P_customer_feedback",
                "quote": "校长要求补充学生评价维度",
            },
            {
                "field": "customer_feedback",
                "source_key": "M",
                "quote": "客户希望周五再看一次",
            },
        ],
        "corrections": [],
        "retained_fields": ["customer_feedback"],
        "corrected_fields": [],
    }
    assert validate(value_ok, None) == {"ok": True}
    # 伪造 P 片段（不逐字来自旧反馈）-> 拒
    value_bad = {
        "record": {"customer_feedback": "客户已签约"},
        "proofs": [
            {
                "field": "customer_feedback",
                "source_key": "P_customer_feedback",
                "quote": "客户已签约并付款",  # 不在旧反馈来源中
            }
        ],
        "corrections": [],
        "corrected_fields": [],
    }
    assert validate(value_bad, None)["ok"] is False


def test_next_action_supplement_retains_original_arrangement():
    """下一步行动合并：保留原有效安排并纳入新目标，分别引用 P_next_action 与 M。"""
    sources = {
        **RETENTION_SOURCES,
        "M": "还要沟通项目预算",
    }
    validate = build_validator(sources, is_new=False)
    merged = "10月21日完成产品Demo；准备建设方案；10月23日沟通预算"
    value = {
        "record": {"next_action": merged},
        "proofs": [
            {
                "field": "next_action",
                "source_key": "P_next_action",
                "quote": "10月21日完成产品Demo",
            },
            {
                "field": "next_action",
                "source_key": "M",
                "quote": "还要沟通项目预算",
            },
        ],
        "corrections": ["同次补答新增预算沟通，原Demo安排继续有效"],
        "retained_fields": ["next_action"],
        "corrected_fields": [],
    }
    assert validate(value, None) == {"ok": True}


def test_new_completed_action_still_cannot_reuse_prior_achievement():
    """新一次已发生行动不得复制旧达成结果：is_new=true 时引 P_achievement 必须被拒。"""
    validate = build_validator(RETENTION_SOURCES, is_new=True)
    value = {
        "record": {"achievement": "已预约10月23日到校汇报"},
        "proofs": [
            {
                "field": "achievement",
                "source_key": "P_achievement",
                "quote": "已与校长预约汇报时间，调整到10月23日上午8点到校汇报",
            }
        ],
        "corrections": [],
        "corrected_fields": [],
    }
    result = validate(value, None)
    assert result["ok"] is False
    assert json.loads(result["reason"])["code"] == "proof_source_not_allowed"
    assert json.loads(result["reason"])["field"] == "achievement"
