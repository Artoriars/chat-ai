"""Chat BI 后端入口。启动：uvicorn app.main:app --port 8600（在 backend/ 目录下执行）。"""
import logging
from pathlib import Path

# 必须在其他 app 导入前把 .env 写入 OS 环境：langchain 的 LangSmith tracing 等开关
# 从 os.environ 读取，pydantic-settings 只把 .env 喂给 Settings 对象、不导出到进程环境
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from fastapi import FastAPI  # noqa: E402

from app.api import chat, schema  # noqa: E402

logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Chat BI", version="0.1.0")
app.include_router(chat.router, prefix="/api")
app.include_router(schema.router, prefix="/api")


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}
