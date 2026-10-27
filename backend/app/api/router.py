"""API 路由汇总（统一前缀 /api/v1）。"""

from __future__ import annotations

from fastapi import APIRouter

from app.api import (
    ai,
    assistant,
    assistant_workspace,
    auth,
    customers,
    leads,
    opportunities,
    reports,
    risks,
    system,
    targets,
)

api_router = APIRouter(prefix="/api/v1")

#  顺序有意义：leads 里含大量静态路径（/leads/duplicates、/leads/load-view），
#    必须在 /leads/{lead_id} 之前注册；opportunities 里的 /opportunities/{lead_id}
#    与 leads 里的 /opportunities 列表不冲突（一个带路径参数、一个是集合）。
api_router.include_router(auth.router)
api_router.include_router(system.router)
api_router.include_router(customers.router)
api_router.include_router(leads.router)
api_router.include_router(opportunities.router)
api_router.include_router(targets.router)
api_router.include_router(risks.router)
api_router.include_router(reports.router)
api_router.include_router(ai.router)

api_router.include_router(assistant.router)
api_router.include_router(assistant_workspace.router)
