"""市场助手 · FastAPI 应用入口。

启动流程：
1. 建表 + 补列（原型阶段用 create_all，生产可上 Alembic）
2. 首次启动自动播种：管理员/专员/经理账号 + 7 环节标准卡 + 法定节假日 + 演示数据
3. 启动进程内定时任务（SLA 扫描：24h 提醒 / 7 工作日自动回收）
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import ValidationError as PydanticValidationError

from app.api.router import api_router
from app.config import get_settings
from app.core.errors import BizError
from app.database import dispose_db, init_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("app")
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()

    # 播种（幂等）：账号 / 标准卡 / 节假日 / 演示数据
    from app.seed import run_seed

    await run_seed()

    # AI 层：加载 Agently 设置并校验 provider 生效（缺密钥只告警，不阻止启动；
    # 真正调用 AI 的接口在缺密钥时返回 503。）
    from app.ai.runtime import assert_ai_ready
    from app.core.errors import AiUnavailableError

    try:
        assert_ai_ready()
        logger.info("AI 能力就绪：Agently provider 已激活（别名 extract / diagnose / compose）")
    except AiUnavailableError as exc:
        logger.warning("AI 能力不可用：%s", exc.message)

    # 进程内定时任务
    from app.scheduler import start_scheduler, stop_scheduler

    task = start_scheduler()
    logger.info("应用启动完成：%s（TZ=Asia/Shanghai）", settings.APP_NAME)
    try:
        yield
    finally:
        await stop_scheduler(task)
        await dispose_db()


app = FastAPI(
    title="市场助手 API",
    description="AI销售助手：线索获取与验证 → 商机跟进与签单",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs",
    openapi_url="/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(BizError)
async def biz_error_handler(request: Request, exc: BizError) -> JSONResponse:
    """业务异常统一转 JSON，前端只需读 detail。"""
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.message, "code": exc.code},
    )


@app.exception_handler(PydanticValidationError)
async def pydantic_error_handler(request: Request, exc: PydanticValidationError) -> JSONResponse:
    """手工构造 pydantic 模型时抛出的校验错误，也统一转成 422（否则会变成 500）。"""
    first = exc.errors()[0] if exc.errors() else {}
    message = first.get("msg", "参数校验未通过")
    return JSONResponse(status_code=422, content={"detail": message, "code": "validation_error"})


@app.exception_handler(Exception)
async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("未处理异常：%s", exc)
    return JSONResponse(
        status_code=500, content={"detail": "服务器内部错误", "code": "internal_error"}
    )


@app.get("/health", tags=["系统"], summary="健康检查")
async def health() -> dict:
    return {"status": "ok", "app": settings.APP_NAME, "tz": "Asia/Shanghai"}


app.include_router(api_router)
