"""环节标准 AI 判定：跟进保存后异步判断，并由负责人采纳生效。

模型失败不回滚已经保存的跟进，也不写入成功判定。"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import async_session_factory
from app.models.activity import FollowUp
from app.models.lead import Lead
from app.models.stage import StandardJudgement
from app.models.user import User

logger = logging.getLogger(__name__)

#: 后台任务的强引用（asyncio 只持弱引用，不保存可能被 GC 掉）
_pending_tasks: set[asyncio.Task] = set()

#: 等「刚写的这条跟进」可见的重试参数（0.2s × 25 ≈ 最多 5 秒）
VISIBLE_ATTEMPTS = 25
VISIBLE_INTERVAL_SECONDS = 0.2

#: 判定状态
STATUS_PENDING = "pending"
STATUS_ACCEPTED = "accepted"
STATUS_IGNORED = "ignored"


# ══════════════ 可用性：没配密钥就不排队 ══════════════


def auto_judge_available() -> bool:
    """自动判定能不能跑：只做**廉价**的「密钥是否配置」判断。

    更完整的就绪校验（SETTINGS.yaml 可解析、provider 已激活）由任务内的
    `ai_service.auto_judge_standards → assert_ai_ready()` 负责；
    那里失败同样只记日志。这样每次写跟进都只花一次属性读取，
    不会把集成层的初始化动作塞进跟进接口的关键路径。
    """
    try:
        return bool(get_settings().llm_configured)
    except Exception as exc:  # noqa: BLE001 - 配置读不出来也绝不阻断跟进
        logger.warning("读取 AI 配置失败 → 跳过自动判定：%s", exc)
        return False


# ══════════════ 自动触发 ══════════════


def schedule_auto_judgement(*, lead_id: str, user_id: str, followup_id: str | None = None) -> bool:
    """在**当前事件循环**排一次后台判定（同步返回，不阻塞跟进接口）。

    返回是否真的排上了（排不上只记日志，调用方无需处理）。
    """
    if not auto_judge_available():
        logger.info("AI 未配置（缺 DEEPSEEK_API_KEY）→ 跳过环节标准自动判定：lead=%s", lead_id)
        return False
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # 同步上下文（脚本/定时任务直调服务）没有事件循环：明确跳过，不假装判过
        logger.warning("当前上下文没有事件循环 → 跳过环节标准自动判定：lead=%s", lead_id)
        return False

    task = loop.create_task(_judge_job(lead_id=lead_id, user_id=user_id, followup_id=followup_id))
    _pending_tasks.add(task)
    task.add_done_callback(_pending_tasks.discard)
    return True


async def _judge_job(*, lead_id: str, user_id: str, followup_id: str | None) -> None:
    """后台任务体：独立会话跑一次判定并落库。异常一律吞掉。"""
    try:
        if followup_id and not await _wait_followup_visible(followup_id):
            logger.warning(
                "环节标准自动判定放弃：跟进 %s 在等待窗口内仍不可见（请求事务可能已回滚）",
                followup_id,
            )
            return

        async with async_session_factory() as db:
            lead = (await db.execute(select(Lead).where(Lead.id == lead_id))).scalar_one_or_none()
            if lead is None or not lead.current_stage:
                # 线索被删 / 还没进入闭环二：没有「当前环节标准」可判，正常跳过
                return
            # 助手已在同事务保存基准判定和固定依据，避免旧草稿再次启动一套判定。
            from app.services import dossier_service

            if await dossier_service.get(db, lead_id) is not None:
                return
            user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
            if user is None:
                logger.warning(
                    "环节标准自动判定跳过：操作人 %s 不存在（lead=%s）", user_id, lead_id
                )
                return

            # 延迟导入：ai_service 会 import followup_service，模块级导入会成环
            from app.services import ai_service

            result = await ai_service.auto_judge_standards(db, user=user, lead=lead)
            await save_judgement(db, lead=lead, result=result)
            await db.commit()
            logger.info(
                "环节标准自动判定完成：lead=%s stage=%s 判定 %d 条（待人工确认）",
                lead_id,
                result.get("stage"),
                len(result.get("items") or []),
            )
    except Exception as exc:  # noqa: BLE001 - 判定失败绝不能影响跟进写入
        logger.warning(
            "环节标准自动判定失败（已忽略，不影响跟进写入）：lead=%s %s: %s",
            lead_id,
            type(exc).__name__,
            exc,
        )


async def _wait_followup_visible(followup_id: str) -> bool:
    """等目标跟进在**独立会话**里可见（见模块文档「竞态」）。"""
    for _ in range(VISIBLE_ATTEMPTS):
        async with async_session_factory() as db:
            found = (
                await db.execute(select(FollowUp.id).where(FollowUp.id == followup_id))
            ).scalar_one_or_none()
        if found:
            return True
        await asyncio.sleep(VISIBLE_INTERVAL_SECONDS)
    return False


async def wait_pending_judgements(timeout_seconds: float = 30.0) -> None:
    """等当前循环里已排的判定任务跑完（**测试与脚本**用；接口不调用它）。

    只等本循环里的任务；跨循环的残留任务（上一个测试的事件循环已关）直接跳过，
    避免把测试之间的状态搅在一起。
    """
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while True:
        tasks = [t for t in list(_pending_tasks) if not t.done()]
        if not tasks:
            return
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            logger.warning("等待自动判定任务超时：仍有 %d 个任务未结束", len(tasks))
            return
        try:
            await asyncio.wait(tasks, timeout=remaining)
        except (RuntimeError, ValueError):
            # 任务属于别的事件循环：无法等待，直接返回（不代表本次判定失败）
            return


# ══════════════ 落库（覆盖式：一个商机只留最新一条） ══════════════


async def save_judgement(db: AsyncSession, *, lead: Lead, result: dict) -> StandardJudgement:
    """把判定结果落成「待确认草稿」；同一 lead 的旧判定先删掉（只留最新一条）。

     这里**只写 `biz_standard_judgement`**，不碰 `biz_stage_completion`：
    勾选完成标准必须由项目负责人点「采纳」（auto-apply）才发生。
    """
    await db.execute(delete(StandardJudgement).where(StandardJudgement.lead_id == lead.id))
    row = StandardJudgement(
        lead_id=lead.id,
        stage=result.get("stage") or lead.current_stage or "",
        judged_at=_parse_dt(result.get("judged_at")) or datetime.now(),
        model_used=result.get("model_used"),
        parse_mode=str(result.get("parse_mode")) if result.get("parse_mode") else None,
        items=list(result.get("items") or []),
        already_done=list(result.get("already_done") or []),
        hard_gate_passed=bool(result.get("hard_gate_passed")),
        missing_hard_gates=list(result.get("missing_hard_gates") or []),
        basis_followup_at=await latest_followup_at(db, lead.id),
        status=STATUS_PENDING,
    )
    db.add(row)
    await db.flush()
    return row


async def mark_accepted(db: AsyncSession, *, lead_id: str) -> StandardJudgement | None:
    """采纳成功 → 对应判定置 accepted（草稿箱清空）。没有记录时返回 None。"""
    row = await current_judgement(db, lead_id)
    if row is not None:
        row.status = STATUS_ACCEPTED
    return row


# ══════════════ 只读查询 ══════════════


async def current_judgement(db: AsyncSession, lead_id: str) -> StandardJudgement | None:
    return (
        await db.execute(
            select(StandardJudgement)
            .where(StandardJudgement.lead_id == lead_id)
            .order_by(StandardJudgement.judged_at.desc(), StandardJudgement.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def latest_followup_at(db: AsyncSession, lead_id: str) -> datetime | None:
    """该商机最新一条跟进的时间（判断「判定是否已过期」的基准）。"""
    return (
        await db.execute(select(func.max(FollowUp.followed_at)).where(FollowUp.lead_id == lead_id))
    ).scalar()


async def judgement_state(db: AsyncSession, *, lead: Lead) -> dict:
    """`GET …/standards/judgement` 的载荷：最新判定 + 是否已过期。"""
    row = await current_judgement(db, lead.id)
    latest = await latest_followup_at(db, lead.id)

    stale = False
    if row is not None:
        if row.stage != (lead.current_stage or ""):
            # 环节已经变了（人推进/回退），这条判定属于旧环节 → 需要重判
            stale = True
        elif latest is not None and (
            row.basis_followup_at is None or latest > row.basis_followup_at
        ):
            # 判定依据早于最新跟进：有新跟进还没被判定过
            stale = True

    return {
        "judgement": judgement_out(row) if row is not None else None,
        "stale": stale,
        "latest_followup_at": latest.isoformat() if latest else None,
    }


def judgement_out(row: StandardJudgement) -> dict:
    """判定记录 → 接口载荷（字段与 auto-judge 的返回对齐，前端可直接复用渲染）。"""
    from app.core import dicts

    return {
        "id": row.id,
        "lead_id": row.lead_id,
        "stage": row.stage,
        "stage_label": dicts.label_of(dicts.STAGE_LABELS, row.stage),
        "judged_at": row.judged_at.isoformat() if row.judged_at else None,
        "model_used": row.model_used,
        "parse_mode": row.parse_mode,
        "items": list(row.items or []),
        "already_done": list(row.already_done or []),
        "hard_gate_passed": bool(row.hard_gate_passed),
        "missing_hard_gates": list(row.missing_hard_gates or []),
        "basis_followup_at": row.basis_followup_at.isoformat() if row.basis_followup_at else None,
        "status": row.status,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def _parse_dt(value) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


__all__ = [
    "auto_judge_available",
    "current_judgement",
    "judgement_out",
    "judgement_state",
    "latest_followup_at",
    "mark_accepted",
    "save_judgement",
    "schedule_auto_judgement",
    "wait_pending_judgements",
]
