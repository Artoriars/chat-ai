"""Chat BI 后端入口。启动：uvicorn app.main:app --port 8600（在 backend/ 目录下执行）。"""
import logging
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

# 必须在其他 app 导入前把 .env 写入 OS 环境：langchain 的 LangSmith tracing 等开关
# 从 os.environ 读取，pydantic-settings 只把 .env 喂给 Settings 对象、不导出到进程环境
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from fastapi import FastAPI  # noqa: E402

from app.api import chat, export, schema  # noqa: E402
from app.config import get_settings  # noqa: E402

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _schema_sync_loop() -> None:
    """定时全量 diff（最终一致的主同步通道）；间隔 0 = 关闭。"""
    interval = get_settings().schema_sync_interval_seconds
    if interval <= 0:
        logger.info("schema sync scheduler disabled")
        return
    logger.info("schema sync scheduler started (interval=%ss)", interval)
    while True:
        time.sleep(interval)
        try:
            from app.collector import refresh_tables

            stats = refresh_tables(1, None)
            if stats.get("changed"):
                logger.info("schema sync: %s", stats)
        except Exception as e:
            logger.warning("schema sync failed: %s", e)


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.exporter import start_export_worker

    start_export_worker()
    threading.Thread(target=_schema_sync_loop, name="schema-sync", daemon=True).start()
    yield


app = FastAPI(title="Chat BI", version="0.2.0", lifespan=lifespan)
app.include_router(chat.router, prefix="/api")
app.include_router(schema.router, prefix="/api")
app.include_router(export.router, prefix="/api")


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


# UI 静态页必须挂在所有 API 路由之后（Starlette 按注册顺序匹配，根挂载会吞掉后注册的路由）
from fastapi.staticfiles import StaticFiles  # noqa: E402

app.mount("/", StaticFiles(directory=str(Path(__file__).resolve().parent / "static"), html=True), name="ui")
