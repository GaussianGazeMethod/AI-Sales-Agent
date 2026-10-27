"""宿主侧「选择键」的签发与校验。

依据 Agently-Skills：

- `skills/agently/references/project-framework.md`
  "When a model selects records, expose one host-issued trusted selection key plus
   task-relevant facts. Validate the returned key against the offered set and
   reconstruct canonical ids host-side. **Do not make the model copy UUIDs**"
- `skills/agently-request/references/output-control.md`（Host-Resolved Selection Outputs）
  模型只回「宿主下发的选择键」；规范标识的拼接与授权一律由宿主完成。
- `skills/agently-request/SKILL.md`
  "Give the model every non-sensitive satisfiable validator rule before the first
   attempt… A validator's bounded retry reason is correction feedback for an
   already-declared contract" → 因此成员资格校验写成 Agently 原生 validate_handler，
  由 Agently 带纠正反馈重试，而不是我们自己写重试循环。

本模块只做**非语义**的事：编号、映射、集合成员校验。语义判断（选哪个客户/命中哪条标准）
永远属于模型。
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Sequence

from app.core import dicts

#: 单次录入下发给模型的候选客户上界（按更新时间倒序取）。
#: 这只是"有界"，不是语义过滤；超出时由 `candidate_scope` 如实告知模型与用户。
CANDIDATE_LIMIT = 80


def candidate_key(index: int) -> str:
    """第 index 个候选（从 1 开始）的宿主下发键。

     只允许通过本函数生成候选键：签发端与取数端各自编号会漂移
    （实测踩过：签发按 1..n 给 `C1`，取数按 `lead.id` 存表，结果下发给模型的标准清单恒为空）。
    """
    return f"C{index}"


def issue_candidate_keys(leads: Sequence[Any]) -> tuple[list[dict], dict[str, str]]:
    """线索列表 → (模型可见候选 `C1..Cn`, 宿主侧 key→lead_id)。"""
    visible: list[dict] = []
    mapping: dict[str, str] = {}
    for index, lead in enumerate(leads, start=1):
        key = candidate_key(index)
        mapping[key] = lead.id
        visible.append(
            {
                "key": key,
                "name": lead.name,
                "city": lead.city or "",
                "status_label": dicts.label_of(dicts.LEAD_STATUS_LABELS, lead.status),
                "owner_name": lead.owner_name or "",
            }
        )
    return visible, mapping


def issue_standard_keys(standards: Sequence[Any]) -> tuple[list[dict], dict[str, str]]:
    """完成标准 → (模型可见 `S1..Sn`, 宿主侧 key→standard_id)。"""
    visible: list[dict] = []
    mapping: dict[str, str] = {}
    for index, standard in enumerate(standards, start=1):
        key = f"S{index}"
        mapping[key] = standard.id
        visible.append(
            {
                "key": key,
                "content": standard.content,
                "is_hard_gate": bool(standard.is_hard_gate),
                "evidence_hint": standard.evidence_hint or "",
            }
        )
    return visible, mapping


def _of(value: Any, path: str) -> Any:
    """按点路径取值（仅用于宿主校验，不做任何语义判断）。"""
    current = value
    for part in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def offered_key_validator(
    *,
    allowed: Iterable[str],
    field: str,
    label: str,
    allow_null: bool = False,
):
    """校验「单值键」必须落在本次下发的集合内。"""
    allowed_set = {str(item) for item in allowed}

    def validate(value: dict, context: Any) -> dict:
        raw = _of(value, field)
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            if allow_null:
                return {"ok": True}
            return {"ok": False, "reason": f"{field} 不能为空（{label}）"}
        text = str(raw).strip()
        if text in allowed_set:
            return {"ok": True}
        options = "、".join(sorted(allowed_set)) or "（本次没有可选项）"
        return {
            "ok": False,
            "reason": (
                f"{field}={text!r} 不在本次下发的 {label} 范围内。"
                f"只能使用：{options}；确实没有匹配项时返回 null。"
            ),
        }

    return validate


def offered_key_list_validator(
    *,
    allowed: Iterable[str],
    field: str,
    label: str,
):
    """校验「键数组」每一项都落在本次下发的集合内。"""
    allowed_set = {str(item) for item in allowed}

    def validate(value: dict, context: Any) -> dict:
        raw = _of(value, field)
        if raw is None:
            return {"ok": True}
        if isinstance(raw, str):
            raw = [raw]
        if not isinstance(raw, list):
            return {"ok": False, "reason": f"{field} 必须是数组"}
        bad = [str(item) for item in raw if str(item) not in allowed_set]
        if bad:
            options = "、".join(sorted(allowed_set)) or "（本次没有可选项）"
            return {
                "ok": False,
                "reason": (
                    f"{field} 含本次未下发的项 {bad}。只能使用：{options}；没有命中就给空数组 []。"
                ),
            }
        return {"ok": True}

    return validate


def enum_validator(
    *,
    allowed: Iterable[str],
    field: str,
    label: str,
):
    """校验枚举字段（如跟进方式、风险代码）。"""
    allowed_set = {str(item) for item in allowed}
    options = "、".join(sorted(allowed_set))

    def validate(value: dict, context: Any) -> dict:
        raw = _of(value, field)
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            return {"ok": False, "reason": f"{field} 不能为空（{label}），只能取：{options}"}
        text = str(raw).strip()
        if text in allowed_set:
            return {"ok": True}
        return {
            "ok": False,
            "reason": f"{field}={text!r} 不是合法取值（{label}），只能取：{options}",
        }

    return validate


def object_list_key_validator(
    *,
    list_field: str,
    key_field: str,
    allowed: Iterable[str],
    label: str,
):
    """校验「对象数组里的某个键字段」都落在下发集合内（如 missing_actions[].standard_key）。"""
    allowed_set = {str(item) for item in allowed}

    def validate(value: dict, context: Any) -> dict:
        raw = _of(value, list_field)
        if raw is None:
            return {"ok": True}
        if not isinstance(raw, list):
            return {"ok": False, "reason": f"{list_field} 必须是数组"}
        options = "、".join(sorted(allowed_set)) or "（本次没有可选项）"
        for index, item in enumerate(raw):
            if not isinstance(item, dict):
                return {"ok": False, "reason": f"{list_field}[{index}] 必须是对象"}
            text = str(item.get(key_field) or "").strip()
            if not text:
                return {"ok": False, "reason": f"{list_field}[{index}].{key_field} 不能为空"}
            if text not in allowed_set:
                return {
                    "ok": False,
                    "reason": (
                        f"{list_field}[{index}].{key_field}={text!r} "
                        f"不在本次下发的 {label} 范围内。只能使用：{options}。"
                    ),
                }
        return {"ok": True}

    return validate


def object_list_optional_key_validator(
    *,
    list_field: str,
    key_field: str,
    allowed: Iterable[str],
    label: str,
):
    """校验「对象数组里**可以为空**的键字段」都落在下发集合内。

    用于 N5 的 `followup_key`：标准未达成时该字段合法地是 null，但一旦给了值就必须是
    本次下发的跟进键（不允许引用宿主没有下发的记录，也不允许模型自己编键）。
    """
    allowed_set = {str(item) for item in allowed}

    def validate(value: dict, context: Any) -> dict:
        raw = _of(value, list_field)
        if raw is None:
            return {"ok": True}
        if not isinstance(raw, list):
            return {"ok": False, "reason": f"{list_field} 必须是数组"}
        options = "、".join(sorted(allowed_set)) or "（本次没有可选项）"
        for index, item in enumerate(raw):
            if not isinstance(item, dict):
                return {"ok": False, "reason": f"{list_field}[{index}] 必须是对象"}
            text = str(item.get(key_field) or "").strip()
            if not text:
                continue  # 允许 null / 空串：未达成时本来就没有可引用的跟进
            if text not in allowed_set:
                return {
                    "ok": False,
                    "reason": (
                        f"{list_field}[{index}].{key_field}={text!r} "
                        f"不在本次下发的 {label} 范围内。只能使用：{options}；没有就返回 null。"
                    ),
                }
        return {"ok": True}

    return validate


def object_list_covers_all_validator(
    *,
    list_field: str,
    key_field: str,
    required: Iterable[str],
    label: str,
):
    """校验「对象数组必须覆盖全部必答项」——每条标准都要有且只有一个判定。

    这是 N5 的核心完备性约束：漏判一条标准会让人以为"AI 说它没问题"，
    因此必须在**派发前的契约里**说明白，由 Agently 原生校验带纠正反馈重试。
    """
    required_list = [str(item) for item in required]

    def validate(value: dict, context: Any) -> dict:
        raw = _of(value, list_field)
        if raw is None:
            raw = []
        if not isinstance(raw, list):
            return {"ok": False, "reason": f"{list_field} 必须是数组"}
        seen = [
            str((item or {}).get(key_field) or "").strip() for item in raw if isinstance(item, dict)
        ]
        missing = [key for key in required_list if key not in seen]
        if missing:
            joined = "、".join(missing)
            return {
                "ok": False,
                "reason": (
                    f"{list_field} 漏了这些 {label}：{joined}。"
                    "每一条都必须给出一个判定项（未达成就 satisfied=false 并说明缺什么）。"
                ),
            }
        return {"ok": True}

    return validate


def object_list_enum_validator(
    *,
    list_field: str,
    key_field: str,
    allowed: Iterable[str],
    label: str,
):
    """校验「对象数组里的某个枚举字段」都合法（如 risk_findings[].risk_type）。"""
    allowed_set = {str(item) for item in allowed}
    options = "、".join(sorted(allowed_set))

    def validate(value: dict, context: Any) -> dict:
        raw = _of(value, list_field)
        if raw is None:
            return {"ok": True}
        if not isinstance(raw, list):
            return {"ok": False, "reason": f"{list_field} 必须是数组"}
        for index, item in enumerate(raw):
            if not isinstance(item, dict):
                return {"ok": False, "reason": f"{list_field}[{index}] 必须是对象"}
            text = str(item.get(key_field) or "").strip()
            if text not in allowed_set:
                return {
                    "ok": False,
                    "reason": (
                        f"{list_field}[{index}].{key_field}={text!r} 不是合法取值（{label}），"
                        f"只能取：{options}"
                    ),
                }
        return {"ok": True}

    return validate


def enum_list_validator(
    *,
    allowed: Iterable[str],
    field: str,
    label: str,
):
    """校验枚举数组（如 risk_types），非法项报错并给出合法取值。"""
    allowed_set = {str(item) for item in allowed}
    options = "、".join(sorted(allowed_set))

    def validate(value: dict, context: Any) -> dict:
        raw = _of(value, field)
        if raw is None:
            return {"ok": True}
        if isinstance(raw, str):
            raw = [raw]
        if not isinstance(raw, list):
            return {"ok": False, "reason": f"{field} 必须是数组"}
        bad = [str(item) for item in raw if str(item) not in allowed_set]
        if bad:
            return {
                "ok": False,
                "reason": f"{field} 含非法项 {bad}（{label}），只能取：{options}；没有就给 []",
            }
        return {"ok": True}

    return validate


#: 明确的「绝对日期字面量」：只认 YYYY-MM-DD / YYYY/M/D / YYYY.M.D / YYYY年M月D日。
#:  刻意**不**匹配「下周三」「10 月中旬」「月底」这类需要语义理解的说法——
#: 那些必须由模型判断；这里只是把"消息里已经写了明确日期"这一**事实**回传给模型做纠正。
_ABS_DATE_RE = re.compile(
    r"(?<!\d)((?:19|20)\d{2})\s*[-/.年]\s*(\d{1,2})\s*[-/.月]\s*(\d{1,2})\s*日?(?!\d)"
)


def explicit_date_literals(text: str) -> list[str]:
    """抽取消息中**明确的绝对日期**（去重、保序，最多返回 3 个）。"""
    found: list[str] = []
    for year, month, day in _ABS_DATE_RE.findall(text or ""):
        literal = f"{int(year):04d}-{int(month):02d}-{int(day):02d}"
        if literal not in found:
            found.append(literal)
        if len(found) >= 3:
            break
    return found
