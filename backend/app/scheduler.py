"""进程内定时任务（随 FastAPI lifespan 启停）。

三类任务，全部幂等，失败不影响下一轮：
1. **SLA 扫描**（每 SCHEDULER_INTERVAL_SECONDS）：24 小时提醒 / 7 个工作日自动回收
2. **预警扫描**（每 30 分钟）：卡单 / 总时长 / 节奏窗口落库 + 转化率命中通知管理人员
3. **周报生成**（每周五）：为市场专员/市场部管理各生成一份周报草稿（已存在则跳过）

刻意不引 APScheduler：采用进程内调度，用 asyncio 周期任务；
判定逻辑全部在 services 层，调度与逻辑分离，将来换 Celery Beat 只改本文件。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime

from app.config import get_settings
from app.database import async_session_factory

logger = logging.getLogger(__name__)
settings = get_settings()

RISK_SCAN_INTERVAL_SECONDS = 30 * 60  # 预警扫描最快 30 分钟一次


async def _run_sla_scan() -> dict:
    from app.services import sla_service

    async with async_session_factory() as session:
        result = await sla_service.scan(session)
        await session.commit()
    return result


async def _run_risk_scan() -> dict:
    """商机侧（闭环二）+ 运营侧（闭环三）预警扫描。"""
    from app.services import risk_service

    async with async_session_factory() as session:
        result = await risk_service.scan(session)
        delivery = await risk_service.scan_delivery(session)
        analysis = await risk_service.conversion_analysis(session)
        notified = await risk_service.notify_conversion_alerts(session, analysis)
        await session.commit()
    return {**result, "conversion_notified": notified, "delivery": delivery}


async def _run_weekly_reports(period: str) -> dict:
    """周五为市场部人员生成周报草稿（已存在则跳过，不会覆盖手写内容）。"""
    from sqlalchemy import select

    from app.models.enums import UserRole
    from app.models.report import WeeklyReport
    from app.models.user import User
    from app.services import report_service

    created = 0
    async with async_session_factory() as session:
        staff = (
            (
                await session.execute(
                    select(User).where(
                        User.role.in_([UserRole.MARKET_STAFF, UserRole.MARKET_MANAGER]),
                        User.is_active.is_(True),
                    )
                )
            )
            .scalars()
            .all()
        )

        existing = {
            r.user_id
            for r in (
                await session.execute(select(WeeklyReport).where(WeeklyReport.period == period))
            )
            .scalars()
            .all()
        }
        for person in staff:
            if person.id in existing:
                continue
            try:
                await report_service.generate(
                    session, operator=person, period=period, user_id=person.id
                )
                created += 1
                await session.commit()
            except Exception as exc:  # noqa: BLE001 - 单人失败不影响其他人
                await session.rollback()
                logger.warning("生成 %s 的周报失败：%s", person.name, exc)
    return {"period": period, "created": created}


async def _loop() -> None:
    interval = max(settings.SCHEDULER_INTERVAL_SECONDS, 30)
    last_risk_scan = 0.0
    last_weekly_date: date | None = None

    while True:
        try:
            # ── 1) SLA 扫描 ──
            result = await _run_sla_scan()
            if result["created"] or result["recycled"]:
                logger.info(
                    "SLA 扫描：生成待办 %s 条，自动回收 %s 条（扫描 %s 条）",
                    result["created"],
                    result["recycled"],
                    result["scanned"],
                )

            now_ts = datetime.now().timestamp()

            # ── 2) 预警扫描（限频） ──
            if now_ts - last_risk_scan >= RISK_SCAN_INTERVAL_SECONDS:
                risk_result = await _run_risk_scan()
                last_risk_scan = now_ts
                delivery_created = risk_result["delivery"]["total_created"]
                if (
                    risk_result["total_created"]
                    or delivery_created
                    or risk_result["conversion_notified"]
                ):
                    logger.info(
                        "预警扫描：商机侧新增 %s 条（卡单 %s / 总时长 %s / 节奏 %s），"
                        "运营侧新增 %s 条（未联系 %s / 验收 %s / 投诉 %s / 问题 %s / 承诺 %s），"
                        "转化率通知 %s 条",
                        risk_result["total_created"],
                        risk_result["created"]["stuck"],
                        risk_result["created"]["total_duration"],
                        risk_result["created"]["rhythm_unclear"],
                        delivery_created,
                        risk_result["delivery"]["created"]["long_no_contact"],
                        risk_result["delivery"]["created"]["acceptance_due"],
                        risk_result["delivery"]["created"]["complaint_open"],
                        risk_result["delivery"]["created"]["issue_overdue"],
                        risk_result["delivery"]["created"]["promise_unmet"],
                        risk_result["conversion_notified"],
                    )

            # ── 3) 周五生成周报 ──
            today = date.today()
            if today.weekday() == 4 and last_weekly_date != today:
                from app.services import report_service as rs

                weekly = await _run_weekly_reports(rs.current_period(today))
                last_weekly_date = today
                logger.info("周报生成：%s，新建 %s 份", weekly["period"], weekly["created"])

        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - 定时任务不得因单次失败退出
            logger.warning("定时任务执行失败：%s", exc)

        await asyncio.sleep(interval)


def start_scheduler() -> asyncio.Task:
    task = asyncio.create_task(_loop(), name="scheduler")
    logger.info("定时任务已启动，间隔 %s 秒", settings.SCHEDULER_INTERVAL_SECONDS)
    return task


async def stop_scheduler(task: asyncio.Task | None) -> None:
    if task is None:
        return
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):  # noqa: BLE001
        pass
