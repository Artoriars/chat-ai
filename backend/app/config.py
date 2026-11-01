"""集中配置：全部来自 .env / 进程环境变量，严禁硬编码凭据。

.env 单一来源为仓库根（chat-bi/.env，docker-compose 同源）；此处按文件位置解析，
保证 uvicorn 无论从哪个目录启动都能加载到。
"""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(_ENV_FILE), env_file_encoding="utf-8", extra="ignore")

    # LLM（OpenAI 兼容）
    llm_base_url: str
    llm_api_key: str
    llm_model: str
    llm_temperature: float = 0.1

    # 数据库
    meta_db_url: str
    business_db_url: str

    # SQL 安全检查
    max_explain_rows: int = 1_000_000
    default_row_limit: int = 50
    max_row_limit: int = 1000

    # M2: embedding（不配则检索降级为全量 schema）
    embedding_base_url: str = ""
    embedding_api_key: str = ""
    embedding_model: str = ""

    # M2: LangGraph checkpointer / 检索参数
    chatbi_pg_dsn: str = ""
    retrieve_top_k: int = 8
    max_context_tables: int = 12

    # M3: 自愈重试上限（初始生成之外的最大修复次数）
    max_sql_retries: int = 2

    # M3: Redis（导出队列 / 状态 / QA 去重）
    redis_url: str = "redis://localhost:16379/0"

    # M3: Excel 导出
    export_max_rows: int = 5000
    export_dir: str = ""

    # M3: schema 定时 diff（秒；0=关闭，仅手动/query-time 触发）
    schema_sync_interval_seconds: int = 0

    # M3: question-SQL 沉淀 dataset（需同时配置 LANGSMITH_API_KEY）
    langsmith_dataset: str = "chatbi-regression"


@lru_cache
def get_settings() -> Settings:
    return Settings()
