"""对话接口：自然语言 → SQL → 安全执行 → 结果集。"""
import logging

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.graph.build import get_chat_graph

logger = logging.getLogger(__name__)
router = APIRouter()


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000, description="自然语言问题")


@router.post("/chat")
def chat(req: ChatRequest) -> dict:
    state = get_chat_graph().invoke({"question": req.question.strip()})
    if state.get("error_code"):
        return {
            "ok": False,
            "error_code": state["error_code"],
            "error": state["error"],
            "sql": state.get("sql"),
        }
    return {
        "ok": True,
        "sql": state["sql"],
        "columns": state["columns"],
        "rows": state["rows"],
        "row_count": state["row_count"],
        "digest": state["digest"],
    }
