"""对话接口（M2）：自然语言 → 多轮改写 → 检索 → SQL → 安全执行 → 结果集。

多轮：session_id 作 checkpointer thread_id；权限：X-User-Id 头（默认 1=全量授权用户）。
"""
import logging
import uuid

from fastapi import APIRouter, Header
from pydantic import BaseModel, Field

from app.graph.build import get_chat_graph

logger = logging.getLogger(__name__)
router = APIRouter()


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000, description="自然语言问题")
    session_id: str | None = Field(default=None, max_length=64, description="会话 ID（不传则新开会话）")


@router.post("/chat")
def chat(req: ChatRequest, x_user_id: int = Header(default=1)) -> dict:
    session_id = req.session_id or uuid.uuid4().hex
    state = get_chat_graph().invoke(
        {"question": req.question.strip(), "user_id": x_user_id, "session_id": session_id},
        config={
            "configurable": {"thread_id": session_id},
            # LangSmith run 元数据：trace 列表按会话/用户过滤
            "metadata": {"session_id": session_id, "user_id": x_user_id},
            "run_name": "chat-turn",
        },
    )
    if state.get("error_code"):
        return {
            "ok": False,
            "session_id": session_id,
            "error_code": state["error_code"],
            "error": state["error"],
            "sql": state.get("sql"),
        }
    return {
        "ok": True,
        "session_id": session_id,
        "output_signal": state.get("output_signal"),
        "rewritten_query": state.get("rewritten_query"),
        "tables": state.get("tables"),
        "sql": state["sql"],
        "columns": state["columns"],
        "rows": state["rows"],
        "row_count": state["row_count"],
        "digest": state["digest"],
    }
