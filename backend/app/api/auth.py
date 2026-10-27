"""认证接口：登录 / 刷新 / 当前用户 / 改密码。"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user
from app.core.errors import AuthError, ValidationError
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.database import get_db
from app.models.user import User
from app.schemas import ChangePasswordRequest, LoginRequest, RefreshRequest
from app.serializers import user_out
from app.services import audit_service

router = APIRouter(prefix="/auth", tags=["认证"])


@router.post("/login", summary="账号密码登录")
async def login(
    payload: LoginRequest, db: AsyncSession = Depends(get_db, scope="function")
) -> dict:
    user = (
        await db.execute(select(User).where(User.username == payload.username))
    ).scalar_one_or_none()
    # 统一失败提示，避免暴露账号是否存在
    if user is None or not verify_password(payload.password, user.password_hash):
        raise AuthError("账号或密码错误")
    if not user.is_active:
        raise AuthError("账号已停用，请联系管理员")

    await audit_service.write_log(
        db,
        operator=user,
        action="login",
        target_type="user",
        target_id=user.id,
        target_name=user.name,
    )
    return {
        "access_token": create_access_token(user.id, user.role),
        "refresh_token": create_refresh_token(user.id, user.role),
        "token_type": "bearer",
        "user": user_out(user),
    }


@router.post("/refresh", summary="用 refresh token 换取新的 access token")
async def refresh(
    payload: RefreshRequest, db: AsyncSession = Depends(get_db, scope="function")
) -> dict:
    try:
        data = decode_token(payload.refresh_token, expected_type="refresh")
    except Exception as exc:  # noqa: BLE001
        raise AuthError("刷新令牌无效或已过期") from exc

    user = (
        await db.execute(select(User).where(User.id == data.get("sub"), User.is_active.is_(True)))
    ).scalar_one_or_none()
    if user is None:
        raise AuthError("账号不存在或已停用")
    return {
        "access_token": create_access_token(user.id, user.role),
        "token_type": "bearer",
        "user": user_out(user),
    }


@router.get("/me", summary="当前登录用户")
async def me(user: User = Depends(get_current_user)) -> dict:
    return user_out(user)


@router.post("/change-password", summary="修改本人密码")
async def change_password(
    payload: ChangePasswordRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    if not verify_password(payload.old_password, user.password_hash):
        raise ValidationError("原密码不正确")
    user.password_hash = hash_password(payload.new_password)
    await audit_service.write_log(
        db, operator=user, action="change_password", target_type="user", target_id=user.id
    )
    return {"ok": True}
