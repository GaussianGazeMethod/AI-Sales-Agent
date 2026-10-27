"""Agently 运行时装配：provider 设置加载、模型别名解析、Agent 工厂。

依据 Agently-Skills（catalog v3 / agently 4.1.4.8）：

- `skills/agently-request/references/model-setup.md`
  · 文件设置用 `Agently.load_settings("yaml_file", path, auto_load_env=True)`
  · provider 配置写在**插件真正读取的命名空间**（`plugins.ModelRequester.OpenAICompatible.*`）
  · "if the app must fail fast, validate required env names in the integration layer
    before calling Agently" → 本模块先校验环境变量名，再让 Agently 参与
  · 加载后用 `resolve_model_profile` 校验生效的 provider / model / base_url / auth
- `skills/agently/SKILL.md` 反模式
  · 禁止"自定义 provider HTTP / 自写解析 / 自写重试"替代 Agently 原生能力
  · 禁止用 mock 或手写本地替代品替换模型负责的行为 → 失败即抛 `AiUnavailableError`

 业务代码只使用**模型别名**（extract / diagnose / compose），不直接写模型名与 URL。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import uuid
from pathlib import Path
from typing import Any

from agently import Agently
from agently.utils import resolve_model_profile

from app.config import get_settings
from app.core.errors import AiUnavailableError

logger = logging.getLogger(__name__)

#: backend/ 目录（SETTINGS.yaml、prompts/、workflows/ 都在这一层）
PROJECT_ROOT = Path(__file__).resolve().parents[2]
SETTINGS_PATH = PROJECT_ROOT / "SETTINGS.yaml"
PROMPTS_DIR = PROJECT_ROOT / "prompts"

#: 模型别名（与 SETTINGS.yaml 的 model_pool 一致）；业务代码只用这些常量
MODEL_EXTRACT = "extract"
MODEL_DIAGNOSE = "diagnose"
MODEL_COMPOSE = "compose"
MODEL_DIALOGUE = "dialogue"

_loaded = False


def load_agently_settings() -> None:
    """加载 SETTINGS.yaml（幂等）。应用启动时调用一次。"""
    global _loaded
    if _loaded:
        return
    if not SETTINGS_PATH.exists():
        raise AiUnavailableError(f"缺少 Agently 配置文件：{SETTINGS_PATH}")
    Agently.load_settings("yaml_file", str(SETTINGS_PATH), auto_load_env=True)
    if os.environ.get("ASSISTANT_AUDIT_DIR"):
        directory = Path(os.environ["ASSISTANT_AUDIT_DIR"])
        directory.mkdir(parents=True, exist_ok=True)

        def observe(event):
            from collections.abc import Mapping

            payload = event.payload if isinstance(event.payload, Mapping) else {}
            telemetry = payload.get("model_request_telemetry") or {}
            run = event.run
            record = {
                "event_type": event.event_type,
                "timestamp": event.timestamp,
                "run_id": getattr(run, "run_id", None),
                "parent_run_id": getattr(run, "parent_run_id", None),
                "agent_name": telemetry.get("agent_name") or getattr(run, "agent_name", None),
                "usage_summary": telemetry.get("usage_summary"),
                "status": payload.get("status"),
                "attempt_index": payload.get("attempt_index") or telemetry.get("attempt_index"),
                "retry": payload.get("retry"),
                "raw_text": payload.get("raw_text")
                if event.event_type == "model.completed"
                else None,
            }
            with (directory / "usage.jsonl").open("a") as output:
                output.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")

        Agently.event_center.register_hook(
            observe,
            event_types=["model.request_started", "model.completed", "model.meta", "model.status"],
            hook_name="isolated-acceptance-usage",
        )
    _loaded = True
    logger.info("Agently 设置已加载：%s", SETTINGS_PATH.name)


def assert_ai_ready() -> None:
    """集成层的 fail-fast：确认密钥存在、别名可解析、provider 已激活。

    这里**只**做"能不能用"的前置校验，不碰任何业务语义。
    """
    settings = get_settings()
    if not settings.llm_configured:
        raise AiUnavailableError(
            "AI 功能未启用：缺少 DEEPSEEK_API_KEY。请在 backend/.env 配置后重启后端"
            "（本系统不做规则降级解析，未配置时 AI 功能直接不可用）"
        )
    load_agently_settings()


def describe_ai() -> dict[str, Any]:
    """给 `/ai/status` 与 6.10 适配配置页用的只读描述（非密的 provider 投影，不含密钥）。

     别名解析只依赖 `SETTINGS.yaml`，**不需要密钥**：即使没配 key 也要把
    "本该用哪个模型 / 哪个端点 / 密钥是否生效" 如实投影出来，便于排查配置问题。
    """
    settings = get_settings()
    info: dict[str, Any] = {
        "enabled": settings.llm_configured,
        "provider": "OpenAICompatible",
        "settings_file": SETTINGS_PATH.name,
        "parse_mode": "agently",
        "degrade_policy": "fail_closed",
    }

    load_agently_settings()
    profiles: dict[str, Any] = {}
    for alias in (MODEL_EXTRACT, MODEL_DIAGNOSE, MODEL_COMPOSE, MODEL_DIALOGUE):
        resolution = resolve_model_profile(alias, Agently.settings)
        profiles[alias] = {
            "model": resolution.get("model"),
            "base_url": resolution.get("base_url"),
            "auth_present": resolution.get("auth_present"),
        }
    info["profiles"] = profiles
    info["model"] = profiles[MODEL_EXTRACT]["model"]

    if not settings.llm_configured:
        info["note"] = "未配置 DEEPSEEK_API_KEY：AI 功能不可用（不做规则降级）"
    elif not all(p["auth_present"] for p in profiles.values()):
        info["note"] = "provider 密钥未生效，请检查 backend/.env 与 SETTINGS.yaml 的 ${ENV.*} 占位"
    return info


def create_agent(name: str, model_alias: str):
    """建一个绑定到指定模型别名的 Agent。

    别名不在 `model_pool` 中时，Agently 会在构造请求前直接报错（不静默回退到默认模型）。
    """
    assert_ai_ready()
    return Agently.create_agent(name=name).activate_model(model_alias)


def load_prompt(agent, prompt_filename: str, *, mappings: dict[str, Any]):
    """加载 prompts/<filename> 并把 `${...}` 占位替换为本次请求的事实。"""
    path = PROMPTS_DIR / prompt_filename
    if not path.exists():
        raise AiUnavailableError(f"缺少提示词文件：{path}")
    return agent.use_task_workspace(PROMPTS_DIR).load_yaml_prompt(path, mappings=mappings)


# ══════════════ 请求执行（两种形态）══════════════


def build_structured_execution(
    *,
    alias: str,
    agent_name: str,
    prompt_file: str,
    mappings: dict[str, Any],
    output_model: Any,
):
    """装配一次结构化请求的执行草稿（不派发），便于用 `get_prompt_text()` 审计。

     **顺序约束（实测）**：`.output(Model)` 必须放在 `load_yaml_prompt(...)` **之后**。
    反过来写（先 output 再 load）会让 INPUT / INFO / INSTRUCT 三个槽位丢失——
    渲染出的提示词只剩输出契约，模型只能凭空编内容（实测：返回与输入完全无关的跟进内容）。
    `tests/test_ai_agently.py::test_structured_request_renders_input_and_contract` 守住这条。
    """
    agent = load_prompt(create_agent(agent_name, alias), prompt_file, mappings=mappings)
    return (
        agent.output(output_model)
        .create_execution(
            options={
                "context_budget": {
                    "max_chars": 16000,
                    "max_blocks": 16,
                    "max_block_chars": 12000,
                    "optional_selection": "none",
                }
            }
        )
        .strategy("direct")
    )


async def run_structured(
    *,
    alias: str,
    agent_name: str,
    prompt_file: str,
    mappings: dict[str, Any],
    output_model: Any,
    validators: list[Any] | None = None,
    required_skills: list[Any] | None = None,
    validation_context: dict[str, Any] | None = None,
    request_provenance: dict[str, Any] | None = None,
):
    """跑一个**结构化输出**请求：Pydantic 契约（app/ai/contracts.py）+ 宿主校验器。

    - 输出契约用 Pydantic 模型：Agently 会把 requiredness / Literal 枚举 / 长度等
      递归投影到模型可见的字段要求（实测渲染文本里带 `allowed values` 与 `length`）。
    - `validate_handler` + `max_retries` 是 Agently 原生的"校验失败 → 带纠正反馈重试"，
      不是我们自己写的重试循环（Skills 反模式明令禁止自写 retry manager）。
    - 返回值再走一次 `model_validate`：**类型化验收权威**仍在宿主手里。
    """
    from app.ai.assistant_context import package_input

    mappings, context_package = await package_input(agent_name, mappings)
    prompt_source_path = PROMPTS_DIR / prompt_file
    prompt_source_before = (
        prompt_source_path.read_bytes() if request_provenance is not None else None
    )
    execution = build_structured_execution(
        alias=alias,
        agent_name=agent_name,
        prompt_file=prompt_file,
        mappings=mappings,
        output_model=output_model,
    )
    if required_skills:
        # 宿主已完成路由，当前 Skill 直接执行这一轮业务交互。
        execution = execution.require_skills(required_skills).strategy("direct")

    # Capture what this exact execution assembled before dispatch. Provenance stays
    # host-side; neither the binding nor its digest is inserted into model mappings.
    if request_provenance is not None:
        rendered_prompt = execution.get_prompt_text()
        prompt_source_after = prompt_source_path.read_bytes()
        if prompt_source_before != prompt_source_after:
            raise AiUnavailableError("报告Prompt在本次请求装配期间发生变化，未派发")
        request_provenance.update(
            prompt_file=prompt_file,
            prompt_sha256=hashlib.sha256(prompt_source_after).hexdigest(),
            rendered_prompt_sha256=hashlib.sha256(
                rendered_prompt.encode("utf-8")
            ).hexdigest(),
            bound_skill_revisions=list(required_skills or []),
        )

    # Enabled only by isolated acceptance services. Never record provider
    # settings, credentials or unrelated business data.
    audit_path = None
    audit = None
    if os.environ.get("ASSISTANT_AUDIT_DIR"):
        directory = Path(os.environ["ASSISTANT_AUDIT_DIR"])
        directory.mkdir(parents=True, exist_ok=True)
        audit_path = directory / (uuid.uuid4().hex + ".json")
        audit = {
            "agent": agent_name,
            "model_alias": alias,
            "prompt_file": prompt_file,
            "input_mappings": mappings,
            "context_delivery": context_package,
            "output_schema": output_model.model_json_schema(),
            "required_skills": required_skills or [],
            "rendered_before_dispatch": execution.get_prompt_text(),
            "bound_skill_resources": [
                {
                    "revision": ref,
                    "main_resource": Agently.skill_library.read_resource(
                        ref, "SKILL.md"
                    ).data.decode("utf-8"),
                }
                for ref in (required_skills or [])
            ],
            "state": "dispatching",
        }
        audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2, default=str))

    def validate_schema(value, context):
        from pydantic import ValidationError

        try:
            output_model.model_validate(
                dict(value), context=validation_context
            )
        except ValidationError as exc:
            errors = [
                ".".join(str(p) for p in e["loc"]) + ":" + e["msg"][:200] for e in exc.errors()[:5]
            ]
            if audit is not None:
                audit.setdefault("validation_feedback", []).append(errors)
            return {"ok": False, "reason": "输出不符合已声明schema：" + "; ".join(errors)}
        return {"ok": True}

    def audit_validator(validator):
        def checked(value, context):
            verdict = validator(value, context)
            if audit is not None and isinstance(verdict, dict) and not verdict.get("ok"):
                audit.setdefault("validator_feedback", []).append(
                    {
                        "validator": getattr(validator, "__name__", "validator"),
                        "reason": str(verdict.get("reason") or "rejected")[:300],
                    }
                )
                audit_path.write_text(
                    json.dumps(audit, ensure_ascii=False, indent=2, default=str)
                )
            return verdict

        return checked

    try:
        result = await execution.async_start(
            validate_handler=[
                validate_schema,
                *(audit_validator(validator) for validator in (validators or [])),
            ],
            max_retries=3,
            raise_ensure_failure=True,
        )
        value = output_model.model_validate(
            dict(result), context=validation_context
        ).model_dump()
    except BaseException as exc:
        if audit is not None:
            audit.update(state="failed", error_type=type(exc).__name__)
            audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2, default=str))
        raise
    if audit is not None:
        audit.update(
            state="completed",
            output=value,
            rendered_after_execution=execution.get_prompt_text(),
            resolved_request_prompt=execution.request.prompt.to_serializable_prompt_data(),
            execution_context_packages=[p.to_dict() for p in execution.context_packages],
        )
        audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2, default=str))
    return value


async def run_text(
    *,
    alias: str,
    agent_name: str,
    prompt_file: str,
    mappings: dict[str, Any],
) -> str:
    """跑一个**自由文本**请求（单一自由产物，不声明 output schema，用普通文本交付）。"""
    execution = load_prompt(
        create_agent(agent_name, alias), prompt_file, mappings=mappings
    ).create_execution()
    result = await execution.async_start()
    if isinstance(result, str):
        return result.strip()
    text_getter = getattr(result, "get_text", None)
    if callable(text_getter):
        return str(await text_getter()).strip()
    return str(result).strip()


def model_name(alias: str) -> str | None:
    """别名当前解析到的模型名（用于落库留痕，不含密钥）。"""
    load_agently_settings()
    return resolve_model_profile(alias, Agently.settings).get("model")
