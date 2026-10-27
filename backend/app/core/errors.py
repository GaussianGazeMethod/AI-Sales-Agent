"""统一业务异常。

约定：业务代码抛 BizError（带 HTTP 状态码与中文提示），由 main.py 的异常处理器
统一转成 JSON，前端只需处理 `{detail: "..."}`，不再散落各种裸 Exception。
"""

from __future__ import annotations


class BizError(Exception):
    """业务异常（可预期，非系统故障）。"""

    def __init__(self, message: str, status_code: int = 400, code: str = "biz_error") -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code


class NotFoundError(BizError):
    def __init__(self, message: str = "记录不存在") -> None:
        super().__init__(message, status_code=404, code="not_found")


class PermissionDeniedError(BizError):
    def __init__(self, message: str = "无权限执行该操作") -> None:
        super().__init__(message, status_code=403, code="permission_denied")


class AuthError(BizError):
    def __init__(self, message: str = "登录状态无效，请重新登录") -> None:
        super().__init__(message, status_code=401, code="unauthorized")


class ConflictError(BizError):
    def __init__(self, message: str = "数据冲突") -> None:
        super().__init__(message, status_code=409, code="conflict")


class ValidationError(BizError):
    def __init__(self, message: str = "参数校验未通过") -> None:
        super().__init__(message, status_code=422, code="validation_error")


class AiUnavailableError(BizError):
    """AI 能力不可用（未配置密钥、provider 未激活、模型调用失败）。

    设计约定（Agently-Skills）：**失败就明确失败**。
    `skills/agently/SKILL.md` 反模式明令禁止用 mock、确定性逻辑或手写本地替代品
    替换模型负责的行为——因此这里不提供任何"规则降级"路径，一律以 503 暴露给调用方。
    """

    def __init__(self, message: str = "AI 能力当前不可用，请稍后重试或联系管理员") -> None:
        super().__init__(message, status_code=503, code="ai_unavailable")
