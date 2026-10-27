"""应用配置管理 — 基于 pydantic-settings，从 .env / 环境变量加载。

约定：
- 配置全部收敛到 Settings，业务代码通过 get_settings() 读取，不直接读 os.environ
- .env 永不入库；仓库里只保留 .env.example
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ══════════════ 应用 ══════════════
    APP_NAME: str = "市场助手"
    DEBUG: bool = True
    CORS_ORIGINS: str = "http://localhost:5230,http://127.0.0.1:5230"

    # 对外访问基地址：生成需要绝对 URL 的场景（如渠道二维码）使用。
    # 留空则按请求的 scheme+host 推断（开发够用）；
    # 生产应显式配置为对外域名，否则内网地址生成的链接外部无法访问。
    PUBLIC_BASE_URL: str = ""

    # ══════════════ 数据库 ══════════════
    DATABASE_URL: str = "sqlite+aiosqlite:///./showcase.db"
    # SQL 回显独立开关：DEBUG=true 时也默认关闭，避免日志淹没业务输出
    #: SQLite 遇锁等待秒数（并发写时排队而不是立刻 `database is locked`）
    SQLITE_BUSY_TIMEOUT_SECONDS: float = 15.0
    SQL_ECHO: bool = False

    # ══════════════ 安全 ══════════════
    SECRET_KEY: str = "dev-secret-change-me"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = 480  # 8 小时

    # ══════════════ AI：DeepSeek（文本生成 / 项目诊断）══════════════
    # 模型名和参数需与使用者的供应商账户匹配。
    DEEPSEEK_API_KEY: str = ""
    DEEPSEEK_BASE_URL: str = "https://api.deepseek.com"
    DEEPSEEK_MODEL: str = "deepseek-flash"
    # 思考强度：low / high / max
    DEEPSEEK_EFFORT: str = "high"
    # 诊断场景开启思考；结构化信息抽取场景关闭思考以提速度
    DEEPSEEK_THINKING_FOR_DIAGNOSIS: str = "enabled"
    DEEPSEEK_THINKING_FOR_EXTRACTION: str = "disabled"
    LLM_TEMPERATURE: float = 0.3
    LLM_MAX_TOKENS: int = 4096
    LLM_TIMEOUT_SECONDS: int = 180

    # ══════════════ AI：阿里 DashScope（向量嵌入）══════════════
    DASHSCOPE_API_KEY: str = ""
    DASHSCOPE_BASE_URL: str = "https://dashscope.aliyuncs.com/api/v1"
    DASHSCOPE_OPENAI_BASE_URL: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    EMBEDDING_MODEL: str = "text-embedding-v4"
    EMBEDDING_DIMENSION: int = 1024

    # ══════════════ 知识库（Chroma 嵌入式）══════════════
    CHROMA_PERSIST_DIR: str = "./chroma_db"
    CHROMA_COLLECTION: str = "market_knowledge"
    RETRIEVAL_TOP_K: int = 5

    # ══════════════ IM 适配器（6.10 预留；只做"是否已配置"的状态投影）══════════════
    WECOM_WEBHOOK_URL: str = ""
    DINGTALK_WEBHOOK_URL: str = ""
    FEISHU_WEBHOOK_URL: str = ""

    # ══════════════ 文件上传 ══════════════
    UPLOAD_DIR: str = "./uploads"
    MAX_UPLOAD_SIZE_MB: int = 50
    ALLOWED_UPLOAD_EXTENSIONS: str = ".xlsx,.xls,.csv,.doc,.docx,.pdf,.png,.jpg,.jpeg"

    # ══════════════ 业务参数：闭环一 SLA ══════════════
    # 首次跟进时限：1 个工作日
    LEAD_FIRST_FOLLOW_WORKING_DAYS: int = 1
    # 超期未跟进自动回收：7 个工作日
    LEAD_RECYCLE_WORKING_DAYS: int = 7
    # 是否排除寒暑假计时（已确认：不排除）
    LEAD_EXCLUDE_SCHOOL_VACATION: bool = False

    # ══════════════ 业务参数：闭环二 7 环节停留时长基准（自然日）══════════════
    # 一 7 / 二 10 / 三 15 / 四 10 / 五 20 / 六 20 / 七 8 = 90 天 = 3 个月
    STAGE_DURATION_DAYS: str = "7,10,15,10,20,20,8"

    # ══════════════ 定时任务 ══════════════
    # 进程内调度扫描间隔（秒）
    SCHEDULER_INTERVAL_SECONDS: int = 300

    # ── 派生属性 ──

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @property
    def allowed_upload_extension_list(self) -> list[str]:
        return [e.strip().lower() for e in self.ALLOWED_UPLOAD_EXTENSIONS.split(",") if e.strip()]

    @property
    def stage_duration_days_list(self) -> list[int]:
        return [int(x) for x in self.STAGE_DURATION_DAYS.split(",") if x.strip()]

    @property
    def llm_configured(self) -> bool:
        return bool(self.DEEPSEEK_API_KEY)

    @property
    def embedding_configured(self) -> bool:
        return bool(self.DASHSCOPE_API_KEY)


@lru_cache
def get_settings() -> Settings:
    return Settings()
