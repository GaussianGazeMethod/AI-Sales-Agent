"""工作日计算（闭环一 SLA 的地基）。

业务规则（已确认）：
- 首次跟进时限 = **1 个工作日**
- 超期未跟进自动回收 = **7 个工作日**
- **只算工作日**：周末不算，法定节假日不算，调休上班日（周末但上班）算
- 寒暑假**不排除**

 依赖容器 TZ=Asia/Shanghai（Dockerfile 已固定）。若时区错 8 小时，这里全部偏一天。
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta


def is_workday(day: date, holidays: set[date], makeup_workdays: set[date]) -> bool:
    """判断某一天是否工作日。调休上班日优先于周末规则。"""
    if day in makeup_workdays:
        return True
    if day in holidays:
        return False
    return day.weekday() < 5  # 0=周一 … 4=周五


def next_workday_start(day: date, holidays: set[date], makeup_workdays: set[date]) -> date:
    """返回 day 当天或之后最近的一个工作日。"""
    cursor = day
    for _ in range(400):  # 防御性上限，避免日历异常时死循环
        if is_workday(cursor, holidays, makeup_workdays):
            return cursor
        cursor += timedelta(days=1)
    return cursor


def add_working_days(
    start: datetime,
    days: int,
    holidays: set[date],
    makeup_workdays: set[date],
    end_of_day: bool = True,
) -> datetime:
    """从 start 起顺延 days 个工作日，返回截止时刻。

    days=0 表示"当天内"，也返回当天（或下一个工作日）的截止时刻。
    截止时刻取当天 23:59:59，符合"1 个工作日（24 小时）内跟进"的业务口径。
    """
    cursor = next_workday_start(start.date(), holidays, makeup_workdays)
    remaining = max(days, 0)
    while remaining > 0:
        cursor += timedelta(days=1)
        if is_workday(cursor, holidays, makeup_workdays):
            remaining -= 1
    if end_of_day:
        return datetime.combine(cursor, time(23, 59, 59))
    return datetime.combine(cursor, start.time())


def count_working_days(
    start: datetime,
    end: datetime,
    holidays: set[date],
    makeup_workdays: set[date],
) -> int:
    """统计 (start, end] 之间的工作日天数（用于"已超期几个工作日"）。"""
    if end <= start:
        return 0
    days = 0
    cursor = start.date()
    last = end.date()
    while cursor < last:
        cursor += timedelta(days=1)
        if is_workday(cursor, holidays, makeup_workdays):
            days += 1
    return days


def hours_between(start: datetime, end: datetime) -> float:
    """自然小时差（跟进时效统计用）。"""
    return round((end - start).total_seconds() / 3600, 2)
