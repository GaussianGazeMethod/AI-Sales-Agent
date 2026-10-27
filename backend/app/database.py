"""数据库引擎与会话管理 — SQLAlchemy 2.0 异步风格。

约定：
- Base 统一放在 `app/models/base.py`，这里只引用
- SQLite 需 `check_same_thread=False` 以支持异步
- `create_all` 只建**新表**，对已存在的表**不会补列** → 额外做一次轻量「只加列」对齐
- 迁移策略：原型阶段用 create_all + 加列对齐；生产若要严格迁移再上 Alembic
"""

from __future__ import annotations

import logging

from sqlalchemy import bindparam, event, text
from sqlalchemy.dialects import sqlite as sqlite_dialect
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings

# Base 统一在 models/base.py，这里只引用（re-export 方便业务代码从 database 导入）
from app.models.base import Base  # noqa: F401

logger = logging.getLogger(__name__)

# domain 字段引入前，数据库中存在的预警种类只有这些销售侧类别。
# 交付侧类别与 domain 字段在同一历史版本中引入，不属于此回填范围。
_LEGACY_SALES_ALERT_KINDS = (
    "business_risk",
    "stuck",
    "total_duration",
    "rhythm_window",
    "conversion",
)

settings = get_settings()

_IS_SQLITE = "sqlite" in settings.DATABASE_URL

engine = create_async_engine(
    settings.DATABASE_URL,
    # SQL 回显由独立开关控制（DEBUG 下混杂 SQL 语句会淹没业务输出）
    echo=settings.SQL_ECHO,
    connect_args=(
        {
            # SQLite 需要 check_same_thread=False 以支持异步
            "check_same_thread": False,
            #  并发写保护：默认 5 秒太短，定时任务扫描与用户请求同时写会直接
            # `database is locked`；把等待时间放宽，让写操作排队而不是报错。
            "timeout": settings.SQLITE_BUSY_TIMEOUT_SECONDS,
        }
        if _IS_SQLITE
        else {}
    ),
)


@event.listens_for(engine.sync_engine, "connect")
def _sqlite_pragmas(dbapi_conn, _record) -> None:  # pragma: no cover - 驱动回调
    """每个新连接都打开 WAL 并设置 busy_timeout。

    - `journal_mode=WAL`：读不阻塞写、写不阻塞读（默认 DELETE 模式下并发写极易锁库）
    - `busy_timeout`：遇到锁时等待而不是立刻抛 `database is locked`
    - `synchronous=NORMAL`：WAL 下的常规取舍（掉电最多丢最后一个事务，性能显著更好）
    - `foreign_keys=ON`：SQLite 默认**不**校验外键，必须显式打开
    """
    if not _IS_SQLITE:
        return
    cursor = dbapi_conn.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute(f"PRAGMA busy_timeout={int(settings.SQLITE_BUSY_TIMEOUT_SECONDS * 1000)}")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA foreign_keys=ON")
    finally:
        cursor.close()


async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db() -> AsyncSession:  # type: ignore[misc]
    """FastAPI 依赖：每请求一个数据库会话。

     调用方必须写成 `Depends(get_db, scope="function")`。
    FastAPI 对 yield 依赖默认用 `scope="request"`——**清理发生在响应发出之后**，
    这会造成竞态：上一个请求的 commit 还没落盘，紧接着的下一个请求已经读到旧数据
    （实测表现：指派成功后立刻跟进，被判定为"只能编辑本人负责的线索"）。
    用 `scope="function"` 可让 commit 在响应发出**之前**完成。
    """
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def _column_ddl(column) -> str | None:  # type: ignore[no-untyped-def]
    """把一个 SQLAlchemy 列编译成 SQLite 的 ADD COLUMN 片段。

    刻意**不补 NOT NULL**：老表已有行，补 NOT NULL 会因空值失败。
    只做「加列」，不删不改，避免误伤数据。
    """
    try:
        type_sql = column.type.compile(dialect=sqlite_dialect.dialect())
    except Exception:  # noqa: BLE001 - 无法编译的类型直接跳过
        return None
    parts = [f'"{column.name}"', type_sql]
    if column.server_default is not None:
        arg = getattr(column.server_default, "arg", None)
        if arg is not None and not hasattr(arg, "text"):
            parts.append(f"DEFAULT {arg}")
    return " ".join(parts)


async def _relax_not_null_columns(conn) -> None:  # type: ignore[no-untyped-def]
    """把"模型已改成可空、但旧库仍是 NOT NULL"的列修好。

    背景：SQLite **不支持** `ALTER TABLE ... ALTER COLUMN`，所以只能"重建表"：
    建新表 → 拷数据 → 删旧表 → 改名 → 重建索引。

    首个用到它的场景：`biz_follow_up.lead_id` 改为可空（客户级跟进没有项目），
    否则 `lead_id = NULL` 会直接报 `NOT NULL constraint failed`。
    """
    from sqlalchemy.dialects import sqlite as sqlite_dialect
    from sqlalchemy.schema import CreateIndex, CreateTable

    if conn.dialect.name != "sqlite":
        return

    for table in Base.metadata.sorted_tables:
        rows = (await conn.execute(text(f'PRAGMA table_info("{table.name}")'))).fetchall()
        if not rows:
            continue
        notnull = {row[1]: bool(row[3]) for row in rows}
        stale = [
            column.name
            for column in table.columns
            if column.nullable and notnull.get(column.name, False)
        ]
        if not stale:
            continue

        logger.warning(
            "检测到 %s 的列 %s 仍是 NOT NULL（模型已改为可空）→ 重建表迁移", table.name, stale
        )
        tmp = f"{table.name}__rebuild"
        ddl = str(CreateTable(table).compile(dialect=sqlite_dialect.dialect()))
        ddl = ddl.replace(f"CREATE TABLE {table.name}", f"CREATE TABLE {tmp}", 1)
        await conn.execute(text(ddl))
        cols = ", ".join(f'"{row[1]}"' for row in rows)
        await conn.execute(text(f'INSERT INTO "{tmp}" ({cols}) SELECT {cols} FROM "{table.name}"'))
        await conn.execute(text(f'DROP TABLE "{table.name}"'))
        await conn.execute(text(f'ALTER TABLE "{tmp}" RENAME TO "{table.name}"'))
        # 索引随旧表一起被删掉了，按模型重建
        for index in table.indexes:
            await conn.execute(CreateIndex(index))
        logger.info("重建完成：%s（%s 列已可空）", table.name, stale)


async def _align_missing_columns(conn) -> None:  # type: ignore[no-untyped-def]
    """为已存在的表补齐缺失的列（只加列，不删不改）。

    开发阶段频繁加字段，若不补列，老库会在查询时报 `no such column`。
    """
    for table in Base.metadata.sorted_tables:
        rows = (await conn.execute(text(f'PRAGMA table_info("{table.name}")'))).fetchall()
        if not rows:
            continue  # 表不存在（create_all 刚建的新表不会走到这）
        existing = {row[1] for row in rows}
        for column in table.columns:
            if column.name in existing:
                continue
            ddl = _column_ddl(column)
            if not ddl:
                continue
            try:
                await conn.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN {ddl}'))
                logger.info("已补列：%s.%s", table.name, column.name)
            except Exception as exc:  # noqa: BLE001 - 补列失败不阻断启动
                logger.warning("补列失败 %s.%s：%s", table.name, column.name, exc)


async def _backfill_legacy_sales_risk_domains(conn) -> None:  # type: ignore[no-untyped-def]
    """只把有历史依据的旧销售预警补为 sales，未知域保持空值并显式告警。"""
    if conn.dialect.name != "sqlite":
        return

    columns = (
        await conn.execute(text('PRAGMA table_info("biz_risk_alert")'))
    ).fetchall()
    if not columns or "domain" not in {row[1] for row in columns}:
        return

    await conn.execute(
        text(
            "UPDATE biz_risk_alert SET domain = 'sales' "
            "WHERE domain IS NULL AND kind IN :legacy_sales_kinds"
        ).bindparams(bindparam("legacy_sales_kinds", expanding=True)),
        {"legacy_sales_kinds": _LEGACY_SALES_ALERT_KINDS},
    )
    unresolved = (
        await conn.execute(
            text("SELECT COUNT(*) FROM biz_risk_alert WHERE domain IS NULL")
        )
    ).scalar_one()
    if unresolved:
        logger.warning(
            "%s 条 RiskAlert.domain 无法依据已知历史类别判定，保留NULL待人工核实",
            unresolved,
        )


async def init_db() -> None:
    """创建所有表 + 补齐缺失列（原型阶段使用，生产用 Alembic 迁移）。"""
    # 确保所有模型已注册到 Base.metadata
    from app import models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await _align_missing_columns(conn)
        await _backfill_legacy_sales_risk_domains(conn)
        await _relax_not_null_columns(conn)

    logger.info("数据库初始化完成：%s", settings.DATABASE_URL)


async def dispose_db() -> None:
    """关闭连接池（应用退出时调用）。"""
    await engine.dispose()
