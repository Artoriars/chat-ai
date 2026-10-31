"""Chat BI 后端入口。启动：uvicorn app.main:app --port 8600（在 backend/ 目录下执行）。"""
import logging

from fastapi import FastAPI

from app.api import chat, schema

logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Chat BI", version="0.1.0")
app.include_router(chat.router, prefix="/api")
app.include_router(schema.router, prefix="/api")


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}
