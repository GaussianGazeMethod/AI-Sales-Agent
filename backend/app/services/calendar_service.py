"""工作日历服务：把 sys_holiday 表读成集合，供 core.workday 的纯函数使用。"""

from __future__ import annotations

import logging
from datetime import date, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.workday import add_working_days, count_working_days, is_workday
from app.models.enums import HolidayKind
from app.models.system import Holiday

logger = logging.getLogger(__name__)


async def load_calendar(db: AsyncSession) -> tuple[set[date], set[date]]:
    """返回 (法定节假日集合, 调休上班日集合)。"""
    rows = (await db.execute(select(Holiday.day, Holiday.kind))).all()
    holidays = {r[0] for r in rows if r[1] == HolidayKind.HOLIDAY}
    makeups = {r[0] for r in rows if r[1] == HolidayKind.MAKEUP_WORKDAY}
    return holidays, makeups


async def coverage(db: AsyncSession) -> dict:
    """日历覆盖范围（法定节假日与调休只有种子年份有数据）。

     缺口会影响 SLA：**覆盖范围之外的年份会退化成"只跳周末"**，
    于是元旦/春节等法定节假日会被当成工作日，截止时间被算早。
    这里把覆盖范围暴露出来，便于 6.x 页面与巡检发现"该补哪一年的日历了"
    （2027 年日历维护是已登记的待确认项 N5）。
    """
    years = sorted(
        {int(y) for (y,) in (await db.execute(select(func.strftime("%Y", Holiday.day)))).all() if y}
    )
    return {
        "years": years,
        "min_year": years[0] if years else None,
        "max_year": years[-1] if years else None,
        "day_count": int(
            (await db.execute(select(func.count()).select_from(Holiday))).scalar() or 0
        ),
    }


async def deadline_after_working_days(db: AsyncSession, start: datetime, days: int) -> datetime:
    """从 start 起顺延 days 个工作日，得到截止时刻（当天 23:59:59）。"""
    holidays, makeups = await load_calendar(db)
    deadline = add_working_days(start, days, holidays, makeups)

    # 覆盖范围之外的年份 → 明确告警，而不是静默算错
    years = {int(h.year) for h in holidays} or set()
    if years and deadline.year > max(years):
        logger.warning(
            "工作日日历只覆盖到 %s 年，%s 的法定节假日未维护 → 截止时间 %s 可能偏早（待确认项 N5）",
            max(years),
            deadline.year,
            deadline.date(),
        )
    return deadline


async def is_working_day(db: AsyncSession, day: date) -> bool:
    holidays, makeups = await load_calendar(db)
    return is_workday(day, holidays, makeups)


async def elapsed_working_days(
    db: AsyncSession, start: datetime, end: datetime | None = None
) -> int:
    holidays, makeups = await load_calendar(db)
    return count_working_days(start, end or datetime.now(), holidays, makeups)
