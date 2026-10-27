"""业务日历：按业务发生日锚点计算自然日，保留原话与审计时间。

模型识别动作及日期表达，宿主执行日历算术；缺少锚点或日期有歧义时请求澄清。"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta

WEEKDAY_CN = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}
WEEK_PREFIX = {"下下": 2, "下": 1, "本": 0, "这": 0}

_WEEKDAY_RE = re.compile(r"(下下|下|本|这)周([一二三四五六日天])")
_EXPLICIT_FULL_RE = re.compile(r"(\d{4})年(\d{1,2})月(\d{1,2})[日号]")
_MONTH_DAY_RE = re.compile(r"(?<!\d)(\d{1,2})月(\d{1,2})[日号]")
_DAY_OFFSETS = (("大后天", 3), ("后天", 2), ("明天", 1), ("今天", 0))
_CLOCK_CN_RE = re.compile(
    r"(上午|下午|中午|晚上)?\s*(\d{1,2})\s*[点時时]\s*(?:(\d{1,2})\s*分|半|(\d{2})\s*分)?"
)
_CLOCK_ISO_RE = re.compile(r"(?<!\d)(\d{1,2})[:：](\d{2})(?!\d)")
# 原话出现这些，说明"有时分但当前不支持完整解析"——不能当成"只有日期"写零点。
_CLOCK_HINT_RE = re.compile(r"\d{1,2}\s*[点時时]|\d{1,2}[:：]\d{2}|半点|一刻|三刻")


def parse_time(value) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def parse_expressions(text: str) -> list[dict]:
    """只解析**时间表达本身**（日历短语 + 显式年月日 + 时分），不判断它属于谁的业务含义。

    R02：显式 `YYYY年M月D日` 必须带年（不再靠 180 天阈值猜年份）；`上午8点` 这类
    时分单独记在表达里，供宿主构造 due_at 时保留精度。
    """
    text = text or ""
    found: list[dict] = []
    for match in _EXPLICIT_FULL_RE.finditer(text):
        found.append({"phrase": match.group(0), "kind": "explicit_date",
                      "year": int(match.group(1)), "month": int(match.group(2)),
                      "day": int(match.group(3))})
    explicit_spans = [(m.start(), m.end()) for m in _EXPLICIT_FULL_RE.finditer(text)]
    for match in _WEEKDAY_RE.finditer(text):
        found.append({"phrase": match.group(0), "kind": "weekday",
                      "week_offset": WEEK_PREFIX[match.group(1)],
                      "weekday": WEEKDAY_CN[match.group(2)]})
    for match in _MONTH_DAY_RE.finditer(text):
        if any(start <= match.start() < end for start, end in explicit_spans):
            continue  # 已作为显式年月日解析，不重复算成“月日”
        found.append({"phrase": match.group(0), "kind": "month_day",
                      "month": int(match.group(1)), "day": int(match.group(2))})
    for phrase, offset in _DAY_OFFSETS:
        if phrase in text:
            found.append({"phrase": phrase, "kind": "day_offset", "offset": offset})
    overall_clock, _complete = _clock_from(text)
    for expression in found:
        # 只有单一时间表达时才把整体时分归给它，避免多个表达共享同一个时分。
        expression["clock"] = overall_clock if len(found) == 1 else None
    return found


_CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8,
           "九": 9, "十": 10, "十一": 11, "十二": 12}
_CN_QUARTER = {"一": 15, "二": 30, "三": 45}
_CLOCK_TOKEN_RE = re.compile(
    r"(上午|下午|中午|晚上)?\s*([0-9]{1,2}|[一二三四五六七八九十]{1,3})\s*[点時时]\s*"
    r"(?:([0-9]{1,2}|[一二三四五六七八九十]{1,2})\s*分|(半)|([一二三])刻|([0-9]{2})|[：:]([0-9]{2}))?"
)


def _num(value: str) -> int | None:
    value = (value or "").strip()
    if value.isdigit():
        return int(value)
    return _CN_NUM.get(value)


def _clock_candidates(text: str) -> tuple[list[tuple[int, int]], bool]:
    """收集**所有**时分候选；返回 (候选列表, 每个成分是否都解析成功)。

    不"命中一个就返回"：多候选、解析失败都要暴露出来，由调用方决定不支持/冲突。
    """
    candidates: list[tuple[int, int]] = []
    ok = True
    for match in _CLOCK_ISO_RE.finditer(text or ""):
        hour, minute = int(match.group(1)), int(match.group(2))
        if hour <= 23 and minute <= 59:
            candidates.append((hour, minute))
        else:
            ok = False
    for match in _CLOCK_TOKEN_RE.finditer(text or ""):
        hour = _num(match.group(2))
        if hour is None:
            ok = False
            continue
        if match.group(3) is not None:
            minute = _num(match.group(3))
            if minute is None:
                ok = False
                continue
        elif match.group(4):
            minute = 30
        elif match.group(5):
            minute = _CN_QUARTER[match.group(5)]
        elif match.group(6):
            minute = int(match.group(6))
        elif match.group(7):
            minute = int(match.group(7))
        else:
            minute = 0
        period = match.group(1) or ""
        if period in ("下午", "晚上") and hour < 12:
            hour += 12
        if period == "中午" and hour < 11:
            hour += 12
        if hour > 23 or minute > 59:
            ok = False
            continue
        candidates.append((hour, minute))
    return candidates, ok


def _clock_from(text: str) -> tuple[str | None, str]:
    """返回 (时分, 状态)：full / absent / unsupported / conflict。

    - full：全部时间成分都被解析且只有一个候选；
    - absent：原话确实没有时分线索（此时写 00:00 才合法）；
    - unsupported / conflict：无法证明完整或唯一 ⇒ 调用方不得覆盖为另一个确定时间。
    """
    candidates, ok = _clock_candidates(text or "")
    if not candidates:
        return None, "absent" if ok else "unsupported"
    unique = set(candidates)
    if len(unique) > 1:
        return None, "conflict"
    if not ok:
        return None, "unsupported"
    hour, minute = unique.pop()
    return f"{hour:02d}:{minute:02d}:00", "full"


def resolve(anchor: date, expression: dict) -> date | None:
    """按自然周（周一为首）与日历规则推算；不猜。"""
    kind = expression.get("kind")
    if kind == "explicit_date":
        try:
            return date(expression["year"], expression["month"], expression["day"])
        except ValueError:
            return None
    if kind == "weekday":
        week_start = anchor - timedelta(days=anchor.weekday())
        return week_start + timedelta(days=7 * expression["week_offset"] + expression["weekday"])
    if kind == "day_offset":
        return anchor + timedelta(days=int(expression["offset"]))
    if kind == "month_day":
        # 不带年的“M月D日”只有在锚点当年尚未过去时才可确定；可能指明年的不猜。
        try:
            candidate = date(anchor.year, expression["month"], expression["day"])
        except ValueError:
            return None
        return candidate if candidate >= anchor else None
    return None


def _date_of(value) -> date | None:
    parsed = parse_time(value)
    return parsed.date() if parsed else None


def action_anchor(result: dict, context: dict, action: dict) -> tuple[date | None, str]:
    """**按动作**确定锚点：只认模型交付的 `time_anchor`，不看消息里的关键词。

    - business_date / today / previous / explicit 由模型逐动作判断并给出；
    - `new_record` 是记录级确定语义（本轮是新事件的业务日），可在缺字段时作为
      唯一回退——它不是从消息文本猜出来的；
    - 其它缺字段情形一律 unknown：宿主不猜、不改写、不借用别的动作/项目日期。
    """
    declared = str(action.get("time_anchor") or "").strip()
    occur = _date_of(result.get("occurred_at"))
    previous = _date_of((context.get("previous_followup") or {}).get("occurred_at"))
    today = _date_of(context.get("now"))
    if declared == "explicit":
        return None, "explicit"
    if declared == "business_date" and occur:
        return occur, "business_date"
    if declared == "today" and today:
        return today, "today"
    if declared == "previous" and previous:
        return previous, "previous"
    if declared and declared != "unknown":
        return None, "anchor_unknown"
    if not declared and result.get("new_record") and occur:
        return occur, "business_date(inferred_new_record)"
    return None, "anchor_unknown"


def inherited_action_due(action: dict, context: dict) -> datetime | None:
    """Return the existing date when this update reuses the same A-key date source.

    A relative phrase already bound to an action is provenance for that stored date,
    not a new relative-date instruction to apply to the current follow-up date. If
    the declared source is present in this turn's message, it is a current source,
    even when its wording and candidate date happen to equal the stored values.
    """
    if action.get("operation") != "update":
        return None
    action_key = action.get("action_key") or action.get("key")
    previous = next(
        (item for item in context.get("actions") or [] if item.get("key") == action_key),
        None,
    )
    if previous is None:
        return None
    basis = previous.get("date_basis") or {}
    source_quote = str(action.get("time_source_quote") or "")
    expression = str(action.get("time_expression") or "")
    if source_quote and source_quote in str(context.get("message") or ""):
        return None
    if not source_quote or source_quote != basis.get("time_source_quote"):
        return None
    if not expression or expression != basis.get("time_expression"):
        return None
    if action.get("customer_confirmed") is not previous.get("customer_confirmed"):
        return None
    old_due = parse_time(previous.get("due_at"))
    candidate_due = parse_time(action.get("due_at"))
    if old_due is None or candidate_due is None or old_due.date() != candidate_due.date():
        return None
    return old_due


def normalize_relative_due_dates(result: dict, context: dict) -> list[dict]:
    """按**每个动作自己的**业务锚点复核待办日期（只到日）。

    - 日级：规范化结果一律 `YYYY-MM-DDT00:00:00`，不解析、不校验、不改写时分；
    - 多日期表达 / 缺锚点：不改写、可见标记（不编造确定日期）；
    - 显式年月日不依赖锚点。
    """
    corrections: list[dict] = []
    for action in result.get("actions") or []:
        if not isinstance(action, dict):
            continue
        inherited_due = inherited_action_due(action, context)
        if inherited_due is not None:
            inherited_day = f"{inherited_due.date().isoformat()}T00:00:00"
            if action.get("due_at") != inherited_day:
                action["due_at_model_candidate"] = action.get("due_at")
                action["due_at"] = inherited_day
            action["due_at_basis"] = "inherited_same_action_date"
            continue
        expression_text = str(action.get("time_expression") or "")
        source_text = str(action.get("time_source_quote") or "")
        quote_text = str(action.get("quote") or "")
        expressions = parse_expressions(expression_text or source_text or quote_text)
        if not expressions:
            # A model-supplied date without a host-recognized exact day is not
            # enough to create a due date (for example, "2026年11月"). Keep
            # the candidate for audit, but leave the action's date unresolved.
            if action.get("due_at") is not None:
                action["due_at_model_candidate"] = action.get("due_at")
                action["due_at"] = None
                action["due_at_pending"] = True
                action["due_at_basis"] = "date_unresolvable"
            continue
        anchor, anchor_source = action_anchor(result, context, action)
        explicit = [e for e in expressions if e["kind"] == "explicit_date"]
        yearless_month_day = any(e["kind"] == "month_day" for e in expressions)
        if (
            not explicit
            and anchor is None
            and yearless_month_day
            and str(action.get("time_anchor") or "") == "explicit"
        ):
            # `explicit` means the model treats the date expression as self-contained;
            # a month/day without a year is not. Resolve only its year from the already
            # established business occurrence date, never from the entry clock.
            anchor = _date_of(result.get("occurred_at"))
            if anchor is not None:
                anchor_source = "business_date_for_year"
        if not explicit and anchor is None:
            action["due_at_model_candidate"] = action.get("due_at")
            action["due_at"] = None
            action["due_at_pending"] = True
            action["due_at_basis"] = (
                "date_year_unknown"
                if yearless_month_day and action.get("time_anchor") == "explicit"
                else "anchor_unknown"
            )
            continue
        resolved = set()
        for expression in explicit or expressions:
            if expression["kind"] == "explicit_date":
                resolved.add(resolve(date(1900, 1, 1), expression))
            else:
                resolved.add(resolve(anchor, expression))
        resolved.discard(None)
        if len(resolved) != 1:
            action["due_at_model_candidate"] = action.get("due_at")
            action["due_at"] = None
            action["due_at_pending"] = True
            action["due_at_basis"] = "dates_ambiguous" if len(resolved) > 1 else "date_unresolvable"
            continue
        expected_date = resolved.pop()
        expected = f"{expected_date.isoformat()}T00:00:00"
        current = parse_time(action.get("due_at"))
        phrase = (explicit or expressions)[0]["phrase"]
        if current is not None and current.date() == expected_date:
            if action.get("due_at") != expected:
                # 日级：日期已对但结构化值仍含时分 ⇒ 统一为 T00:00:00（原话不截断）
                action["due_at_model_candidate"] = action.get("due_at")
                action["due_at"] = expected
                action["due_at_basis"] = f"day_level:{phrase}@{anchor_source}"
            else:
                action["due_at_basis"] = f"relative_ok:{phrase}@{anchor_source}"
            continue
        action["due_at_model_candidate"] = action.get("due_at")
        action["due_at"] = expected
        action["due_at_basis"] = f"relative:{phrase}@{anchor_source}"
        corrections.append({"action_key": action.get("key"), "phrase": phrase,
                            "anchor": anchor.isoformat() if anchor else "explicit",
                            "anchor_source": anchor_source,
                            "from": action["due_at_model_candidate"], "to": expected})
    return corrections
