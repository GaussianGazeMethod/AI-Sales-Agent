"""pytest 夹具：独立测试库 + 应用内 HTTP 客户端。

要点：
- **必须在导入 app 之前**改写 DATABASE_URL，否则会污染开发库（settings 是 lru_cache）
- 不使用 lifespan（httpx 的 ASGITransport 不跑 lifespan），改为手工 init_db + run_seed
- 每个 pytest 进程使用独立临时目录，避免并行测试互删数据库
"""

from __future__ import annotations

import os
import pathlib
import random
import shutil
import tempfile

#  顺序敏感：先设环境变量，再导入 app
_TEST_DB_DIR = tempfile.TemporaryDirectory(prefix="market-assistant-pytest-")
_TEST_DB = pathlib.Path(_TEST_DB_DIR.name) / "test_app.db"
# 播种完成后的库快照：每个用例开始前恢复到这里（）。
_SNAPSHOT_DB = pathlib.Path(_TEST_DB_DIR.name) / "snapshot.db"
for suffix in ("", "-wal", "-shm"):
    candidate = pathlib.Path(str(_TEST_DB) + suffix)
    if candidate.exists():
        candidate.unlink()

os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TEST_DB}"
os.environ["SECRET_KEY"] = "test-secret-key-" + "x" * 40
# 默认不配密钥：确定性测试据此验证 **fail-closed**（AI 不可用即 503，不做规则降级）。
# 本回归集不加载运行密钥；模型语义需要另行验证。
os.environ["DEEPSEEK_API_KEY"] = ""

import httpx  # noqa: E402
import pytest  # noqa: E402
from httpx import ASGITransport  # noqa: E402

from app.database import dispose_db, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.seed import run_seed  # noqa: E402


@pytest.fixture
def record_review_stub(monkeypatch):
    """Host tests use a semantic double; live replay validates the real node."""
    from app.ai import assistant_record_review

    async def review(_context, extracted):
        return {"record": extracted["record"], "proofs": [], "corrections": []}

    monkeypatch.setattr(assistant_record_review, "review", review)
    from app.ai import assistant_facts

    async def continue_dialogue(_context, extracted, _stage_result):
        # 明确模型替身：只让Host回归继续消费既有候选，不证明语义质量。
        question = extracted.get("question")
        return {
            "acknowledgement": extracted.get("acknowledgement") or "",
            "question": question,
            "question_topic": (
                (extracted.get("interaction") or {}).get("question_topic") or "next_action"
            )
            if question
            else "none",
        }

    monkeypatch.setattr(assistant_facts, "continue_followup", continue_dialogue)


@pytest.fixture
def project_attribution_stub(monkeypatch):
    """非归属专项用例显式隔离新增加的归属节点，不替代归属专项测试。"""
    from app.ai import assistant_project_attribution

    async def review(_context, updates, *, record=None, actions=None, pending_candidates=None):
        return {
            "updates": updates,
            "record": dict(record or {}),
            "actions": list(actions or []),
            "pending_candidates": list(pending_candidates or []),
            "clarifications": [],
            "assessment": None,
        }

    monkeypatch.setattr(assistant_project_attribution, "review", review)


@pytest.fixture(scope="session", autouse=True)
async def _prepare_database():
    """建表 + 播种 + 首次 SLA 扫描（整个测试会话一次）。

    生产环境由 app.scheduler 在启动后立刻扫一次；测试不走 lifespan，
    所以这里手工触发一次，否则"超时未跟进"类待办不会存在。
    """
    await init_db()
    await run_seed()

    from app.database import async_session_factory
    from app.services import risk_service, sla_service

    async with async_session_factory() as session:
        await sla_service.scan(session)
        # 生产环境由 app.scheduler 周期性扫描；测试里先跑一次，保证卡单/总时长/节奏预警存在
        await risk_service.scan(session)
        # 闭环三运营侧六类预警（验收临近/承诺未兑现等）
        await risk_service.scan_delivery(session)
        await session.commit()

    # ：把「建表 + 播种 + 首轮扫描」之后的库另存为基线快照。
    # 先把 WAL 合并回主库，再复制，保证快照文件本身是完整状态。
    from sqlalchemy import text

    async with async_session_factory() as session:
        await session.execute(text("PRAGMA wal_checkpoint(TRUNCATE)"))
        await session.commit()
    await dispose_db()
    shutil.copyfile(_TEST_DB, _SNAPSHOT_DB)

    yield
    await dispose_db()
    for suffix in ("", "-wal", "-shm"):
        candidate = pathlib.Path(str(_TEST_DB) + suffix)
        if candidate.exists():
            candidate.unlink()
    _TEST_DB_DIR.cleanup()


@pytest.fixture
async def db_session():
    """直连测试库的会话（用于按"宿主事实"准备数据，例如模拟的 AI 草稿）。"""
    from app.database import async_session_factory

    async with async_session_factory() as session:
        yield session
        await session.commit()


@pytest.fixture
async def client():
    """应用内 HTTP 客户端（不经过网络）。"""
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def login(client: httpx.AsyncClient, username: str, password: str = "123456") -> dict:
    resp = await client.post(
        "/api/v1/auth/login", json={"username": username, "password": password}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def auth_headers(client: httpx.AsyncClient, username: str) -> dict:
    data = await login(client, username)
    return {"Authorization": f"Bearer {data['access_token']}"}




async def restore_baseline_snapshot() -> None:
    """把测试库恢复到「建表 + 播种 + 首轮扫描」后的快照（）。

    供 autouse fixture 与隔离契约测试共用：先关闭连接池，否则旧连接持有旧 inode，
    写回去会把数据"复活"；再用快照覆盖主库并丢弃 `-wal`/`-shm`。
    """
    from app.database import dispose_db

    await dispose_db()
    shutil.copyfile(_SNAPSHOT_DB, _TEST_DB)
    for suffix in ("-wal", "-shm"):
        stale = pathlib.Path(str(_TEST_DB) + suffix)
        if stale.exists():
            stale.unlink()


@pytest.fixture(autouse=True)
async def _isolated_database():
    """每个用例开始前把测试库恢复到播种基线（）。

    为什么必须在**文件层**恢复而不是外层事务：测试走真实 HTTP，请求由应用自己的
    会话提交，外层事务回滚看不见这些已提交的数据。这里先 `dispose_db()` 关掉所有
    连接（否则旧连接仍持有旧 inode，写回去会把数据"复活"），再用快照覆盖主库并
    丢弃 `-wal`/`-shm`，之后新建的连接看到的就是干净基线。

    副作用：任何用例都不能再依赖"上一个用例留下的数据"——这正是本单要求，
    依赖种子数据的用例改为自带必要前置（见各用例内的说明）。
    """
    await restore_baseline_snapshot()
    yield


def pytest_collection_modifyitems(items):
    """收集顺序：默认保持原「按文件名」顺序，可用 TEST_ORDER 换序以验证独立性。

    - 默认（不设或 `filename`）：历史沿用顺序，作为 AC04「原顺序」口径；
    - `TEST_ORDER=reverse`：整体倒序（AC05）；
    - `TEST_ORDER=random42`：固定随机种子 42 洗牌（AC05）。

    换序只用于**验证**隔离是否真的成立，不用来藏依赖：任何顺序都必须同样通过。
    """
    mode = os.environ.get("TEST_ORDER", "filename")
    if mode == "reverse":
        items.reverse()
        return
    if mode.startswith("random"):
        seed = int(mode[len("random"):] or "42")
        random.Random(seed).shuffle(items)
        return
    # 默认：保留治理前的文件顺序（稳定排序，模块内函数顺序不变）。
    items.sort(key=lambda item: item.path.name)
