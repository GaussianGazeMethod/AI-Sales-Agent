"""单请求族的 AI 能力：项目诊断（N3）、标准自动判定（N5）与周报正文（N4）。

依据 Agently-Skills `skills/agently-request/SKILL.md`：
"Use agently-triggerflow when a later semantic step needs a tool result, system lookup,
approval, artifact readback, or host computation produced after the first request" ——
诊断、标准判定与周报正文都**没有**这种分发后事实，数据由宿主一次取齐，因此用直接请求链，
不为了目录整齐硬造 TriggerFlow（Occam：已有 Agently 面能承载就不加新实体）。

"""

from __future__ import annotations

from typing import Any

from app.ai import keys as key_utils
from app.ai.contracts import AutoJudgeOutput, DiagnosisOutput
from app.ai.runtime import MODEL_COMPOSE, MODEL_DIAGNOSE, run_structured, run_text
from app.core import dicts
from app.core.sales_rules import condition_prompt_block, normalize_condition_entries


async def diagnose_project(facts: dict[str, Any]) -> dict[str, Any]:
    """N3：返回结构化诊断（宿主随后组装 Markdown 并落库）。"""
    standards = list(facts.get("standards") or [])
    allowed = {item["key"] for item in standards}
    risk_codes = [str(code) for code in (facts.get("risk_catalog") or {})]

    structured = await run_structured(
        alias=MODEL_DIAGNOSE,
        agent_name="ai-diagnose-project",
        prompt_file="diagnose_project.yaml",
        mappings={
            "customer": facts.get("customer") or {},
            "progress": facts.get("progress") or {},
            "followups": facts.get("followups") or [],
            "risks": facts.get("risks") or [],
            "question": facts.get("question") or "",
            "standards": standards,
            "risk_catalog": facts.get("risk_catalog") or {},
            "stage_catalog": facts.get("stage_catalog") or {},
        },
        output_model=DiagnosisOutput,
        validators=[
            key_utils.object_list_key_validator(
                list_field="missing_actions",
                key_field="standard_key",
                allowed=allowed,
                label="完成标准键",
            ),
            key_utils.object_list_enum_validator(
                list_field="risk_findings",
                key_field="risk_type",
                allowed=risk_codes,
                label="风险代码",
            ),
        ],
    )

    # 宿主复核：丢掉越界的引用（真实结果里不合规的引用不得进入落库内容）
    warnings: list[str] = []
    missing: list[dict] = []
    for item in structured.get("missing_actions") or []:
        key = str((item or {}).get("standard_key") or "")
        if key not in allowed:
            warnings.append(f"诊断引用了未下发的标准键 {key!r}，已剔除")
            continue
        missing.append({"standard_key": key, "why": (item or {}).get("why") or ""})

    findings: list[dict] = []
    for item in structured.get("risk_findings") or []:
        code = str((item or {}).get("risk_type") or "")
        if code not in set(risk_codes):
            warnings.append(f"诊断引用了未定义的风险代码 {code!r}，已剔除")
            continue
        findings.append({"risk_type": code, "evidence": (item or {}).get("evidence") or ""})

    return {
        "stage_diagnosis": structured.get("stage_diagnosis") or "",
        "missing_actions": missing,
        "risk_findings": findings,
        "suggested_actions": [str(x) for x in (structured.get("suggested_actions") or [])],
        "talk_tracks": [str(x) for x in (structured.get("talk_tracks") or [])],
        "next_week_plan": [str(x) for x in (structured.get("next_week_plan") or [])],
        "warnings": warnings,
    }


async def judge_stage_standards(facts: dict[str, Any]) -> dict[str, Any]:
    """N5：读该商机的真实跟进 → 逐条判定当前环节完成标准（**只出判定，不落库**）。

    模型给的是「判定 + 原文引文 + 引文出处」；引文是否真的存在**由宿主逐字核对**
    （`app/services/stage_service.py::quote_in_text`），这里只做键的成员/完备性校验。

    别名刻意用 `diagnose`（思考型、预算大）：判断"跟进里到底写没写这件事"需要推理，
    与抽取式请求不同；模型名与 URL 仍来自别名解析，业务代码不写死厂商。
    """
    standards = list(facts.get("standards") or [])
    followups = list(facts.get("followups") or [])
    allowed_standards = {item["key"] for item in standards}
    allowed_followups = {item["key"] for item in followups}

    structured = await run_structured(
        alias=MODEL_DIAGNOSE,
        agent_name="ai-standard-auto-judge",
        prompt_file="judge_stage_standards.yaml",
        mappings={
            "customer": facts.get("customer") or {},
            "stage": facts.get("stage") or {},
            "followups": followups,
            "today": facts.get("today") or "",
            "standards": standards,
            "condition_rule": condition_prompt_block(),
        },
        output_model=AutoJudgeOutput,
        validators=[
            key_utils.object_list_key_validator(
                list_field="items",
                key_field="standard_key",
                allowed=allowed_standards,
                label="完成标准键",
            ),
            key_utils.object_list_optional_key_validator(
                list_field="items",
                key_field="followup_key",
                allowed=allowed_followups,
                label="跟进键",
            ),
            key_utils.object_list_covers_all_validator(
                list_field="items",
                key_field="standard_key",
                required=allowed_standards,
                label="完成标准",
            ),
        ],
    )

    # 宿主复核：越界的引用一律剔除（不得让模型编造的标准键/跟进键流到服务层）
    warnings: list[str] = []
    items: list[dict] = []
    for index, item in enumerate(structured.get("items") or []):
        if not isinstance(item, dict):
            warnings.append(f"items[{index}] 不是对象，已剔除")
            continue
        standard_key = str(item.get("standard_key") or "").strip()
        if standard_key not in allowed_standards:
            warnings.append(f"判定引用了未下发的标准键 {standard_key!r}，已剔除")
            continue
        followup_key = str(item.get("followup_key") or "").strip()
        if followup_key and followup_key not in allowed_followups:
            warnings.append(
                f"判定引用了未下发的跟进键 {followup_key!r}（标准 {standard_key}），已置空"
            )
            followup_key = ""
        quote = item.get("evidence_quote")
        # 固定双条件：只保留共享规则定义的条件的结构（非法/重复条件丢弃，
        # 越界跟进键置空）；条件是否语义完成仍归模型，宿主不猜关键词。
        entries = normalize_condition_entries(item.get("conditions"))
        for entry in entries:
            if entry["source_key"] and entry["source_key"] not in allowed_followups:
                warnings.append(
                    f"条件 {entry['condition_id']} 引用了未下发的跟进键 "
                    f"{entry['source_key']!r}（标准 {standard_key}），已置空"
                )
                entry["source_key"] = ""
        items.append(
            {
                "standard_key": standard_key,
                "satisfied": bool(item.get("satisfied")),
                "confidence": float(item.get("confidence") or 0.0),
                "evidence_quote": str(quote) if isinstance(quote, str) and quote.strip() else None,
                "followup_key": followup_key or None,
                "reason": str(item.get("reason") or "").strip(),
                "conditions": entries,
            }
        )

    return {"items": items, "warnings": warnings}


def render_diagnosis_markdown(
    structured: dict[str, Any],
    *,
    lead_name: str,
    stage_label: str,
    stay_days: int | None,
    base_days: int,
    standards: list[dict],
    followups: list[dict],
    risks: list[dict],
) -> str:
    """H2：把结构化诊断组装成 Markdown（保持既有前端契约）。"""
    by_key = {item["key"]: item for item in standards}
    lines: list[str] = []
    lines.append(f"## {lead_name} · {stage_label or '未进入商机环节'}")

    stay_text = "停留天数未统计"
    if stay_days is not None:
        stay_text = f"已停留 {stay_days} 天" + (
            f"（基准 {base_days} 天）" if base_days else "（基准未设）"
        )
    lines.append(f"**当前状态**：{stay_text}")
    lines.append("")

    diagnosis = structured.get("stage_diagnosis") or "（模型未给出整体判断）"
    lines.append("### 一、卡在哪里")
    lines.append(diagnosis)
    lines.append("")

    lines.append("### 二、还缺的关键动作")
    missing = structured.get("missing_actions") or []
    if missing:
        for item in missing:
            standard = by_key.get(item["standard_key"]) or {}
            gate = "【硬门槛】" if standard.get("is_hard_gate") else ""
            content = standard.get("content") or item["standard_key"]
            lines.append(f"- {gate}**{content}**：{item['why']}")
    else:
        lines.append("- 当前环节没有明显缺失的标准动作")
    lines.append("")

    lines.append("### 三、风险")
    findings = structured.get("risk_findings") or []
    if findings:
        for item in findings:
            label = dicts.label_of(dicts.RISK_TYPE_LABELS, item["risk_type"])
            lines.append(f"- **{label}**：{item['evidence']}")
    elif risks:
        joined = "、".join(str(r.get("description") or "") for r in risks[:3])
        lines.append(f"- 在挂预警：{joined}")
    else:
        lines.append("- 暂无明显风险信号")
    lines.append("")

    lines.append("### 四、建议动作（按优先级）")
    actions = structured.get("suggested_actions") or []
    numbered = [f"{index}. {text}" for index, text in enumerate(actions, start=1)]
    lines.extend(numbered or ["- （无）"])
    lines.append("")

    lines.append("### 五、话术参考")
    tracks = structured.get("talk_tracks") or []
    lines.extend([f"- 「{text}」" for text in tracks] or ["- （无）"])
    lines.append("")

    lines.append("### 六、下周安排")
    plan = structured.get("next_week_plan") or []
    lines.extend([f"- {text}" for text in plan] or ["- （无）"])

    if followups:
        lines.append("")
        lines.append("### 附：最近跟进")
        for item in followups:
            method = dicts.label_of(dicts.FOLLOW_METHOD_LABELS, item.get("method"))
            lines.append(f"- {item.get('followed_at') or ''} {method}：{item.get('content') or ''}")

    if structured.get("warnings"):
        lines.append("")
        lines.append(">  " + "；".join(structured["warnings"]))

    return "\n".join(lines)


async def compose_weekly_body(facts: dict[str, Any]) -> str:
    """N4：周报正文（自由文本）。"""
    return await run_text(
        alias=MODEL_COMPOSE,
        agent_name="ai-weekly-report-body",
        prompt_file="weekly_report_body.yaml",
        mappings={
            "user_name": facts.get("user_name") or "",
            "period": facts.get("period") or "",
            "facts": facts.get("facts") or {},
            "tone_guide": facts.get("tone_guide") or "",
        },
    )
