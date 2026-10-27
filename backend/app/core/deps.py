"""FastAPI 依赖：当前用户、角色校验、数据范围。

权限口径（源自 spec/10 已确认的 U2 权限矩阵）：
- 市场专员：录入、编辑本人、跟进本人
- 市场部管理人员：分配 / 改派 / 回收 / 导入 / 设目标 / 全可见
- 管理层：只读
- 管理员：用户与字典、软删除
- 运营交付 / 产研 / 方案商务：闭环一不可见（各自域后续开放）
"""

from __future__ import annotations

import jwt as pyjwt
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AuthError, PermissionDeniedError
from app.core.security import decode_token
from app.database import get_db
from app.models.enums import DataScope, UserRole
from app.models.user import User

bearer_scheme = HTTPBearer(auto_error=False)

# 各角色的数据范围默认值（管理员建号时可覆盖）
ROLE_DEFAULT_SCOPE: dict[str, str] = {
    UserRole.ADMIN: DataScope.ALL,
    UserRole.MARKET_STAFF: DataScope.SELF,
    UserRole.MARKET_MANAGER: DataScope.ALL,
    UserRole.EXECUTIVE: DataScope.ALL,
    UserRole.DELIVERY_STAFF: DataScope.SELF,
    UserRole.DELIVERY_MANAGER: DataScope.ALL,
    UserRole.RND: DataScope.SELF,
    UserRole.BIZ_SUPPORT: DataScope.SELF,
}

# 闭环一（线索域）可见 / 可写角色
LEAD_VIEW_ROLES = {
    UserRole.ADMIN,
    UserRole.MARKET_STAFF,
    UserRole.MARKET_MANAGER,
    UserRole.EXECUTIVE,
}
LEAD_WRITE_ROLES = {UserRole.ADMIN, UserRole.MARKET_STAFF, UserRole.MARKET_MANAGER}
LEAD_ASSIGN_ROLES = {UserRole.ADMIN, UserRole.MARKET_MANAGER}
LEAD_TARGET_ROLES = {UserRole.ADMIN, UserRole.MARKET_MANAGER}


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> User:
    """解析 Bearer 令牌 → 当前用户。所有业务接口都要求登录。"""
    if credentials is None or not credentials.credentials:
        raise AuthError("未登录或登录已过期")
    try:
        payload = decode_token(credentials.credentials, expected_type="access")
    except pyjwt.ExpiredSignatureError as exc:
        raise AuthError("登录已过期，请重新登录") from exc
    except pyjwt.PyJWTError as exc:
        raise AuthError("登录状态无效，请重新登录") from exc

    user_id = payload.get("sub")
    user = (
        await db.execute(select(User).where(User.id == user_id, User.is_active.is_(True)))
    ).scalar_one_or_none()
    if user is None:
        raise AuthError("账号不存在或已停用")
    return user


def require_roles(*roles: str):
    """角色白名单依赖。"""

    async def _checker(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise PermissionDeniedError("当前角色无权访问该功能")
        return user

    return _checker


# 常用的角色依赖，直接 import 使用
require_lead_view = require_roles(*LEAD_VIEW_ROLES)
require_lead_write = require_roles(*LEAD_WRITE_ROLES)
require_lead_assign = require_roles(*LEAD_ASSIGN_ROLES)
require_lead_target = require_roles(*LEAD_TARGET_ROLES)
require_admin = require_roles(UserRole.ADMIN)


def is_manager(user: User) -> bool:
    """是否管理人员（可看团队全量）。"""
    return user.role in {UserRole.ADMIN, UserRole.MARKET_MANAGER, UserRole.EXECUTIVE}


def can_view_all_leads(user: User) -> bool:
    """闭环一数据范围：全部 / 仅本人。"""
    if user.role in {UserRole.ADMIN, UserRole.MARKET_MANAGER, UserRole.EXECUTIVE}:
        return True
    return user.data_scope == DataScope.ALL
