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


@lru_cache
def get_settings() -> Settings:
    return Settings()
