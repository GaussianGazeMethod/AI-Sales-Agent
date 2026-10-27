"""密码哈希与 JWT 签发/校验。

说明：
- 直接用 `bcrypt` 而不是 passlib —— 基座镜像里 bcrypt 是 5.x，passlib 1.7.4 读取
  `bcrypt.__about__.__version__` 会告警甚至报错，直接用官方库更稳。
- JWT 用 PyJWT；payload 里带 `sub`（用户 id）、`role`、`type`（access/refresh）、`exp`。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
import jwt

from app.config import get_settings

settings = get_settings()

# bcrypt 只处理前 72 字节，超出部分会报错，这里主动截断
_BCRYPT_MAX_BYTES = 72


def hash_password(plain: str) -> str:
    """生成密码哈希。"""
    raw = plain.encode("utf-8")[:_BCRYPT_MAX_BYTES]
    return bcrypt.hashpw(raw, bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """校验密码；哈希为空或格式异常时返回 False（不抛异常，登录失败统一提示）。"""
    if not hashed:
        return False
    try:
        raw = plain.encode("utf-8")[:_BCRYPT_MAX_BYTES]
        return bcrypt.checkpw(raw, hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def _create_token(user_id: str, role: str, token_type: str, expires: timedelta) -> str:
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": user_id,
        "role": role,
        "type": token_type,
        "iat": int(now.timestamp()),
        "exp": int((now + expires).timestamp()),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def create_access_token(user_id: str, role: str) -> str:
    return _create_token(user_id, role, "access", timedelta(minutes=settings.JWT_EXPIRE_MINUTES))


def create_refresh_token(user_id: str, role: str) -> str:
    """刷新令牌：7 天，仅用于换取新的 access token。"""
    return _create_token(user_id, role, "refresh", timedelta(days=7))


def decode_token(token: str, expected_type: str = "access") -> dict[str, Any]:
    """解码并校验令牌；失败抛 jwt 异常，由调用方转成 401。"""
    payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
    if payload.get("type") != expected_type:
        raise jwt.InvalidTokenError("令牌类型不匹配")
    return payload
