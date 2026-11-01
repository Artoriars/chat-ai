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
            # 自愈消耗的修复次数（0 = 一次成功，便于观测自愈健康度）
            "retry_count": state.get("retry_count", 0),
        }
    # clarify 轮：无 SQL 无数据，返回追问与快捷选项，用户回答走下一轮正常 chat
    if state.get("output_signal") == "clarify":
        return {
            "ok": True,
            "session_id": session_id,
            "output_signal": "clarify",
            "clarify_question": state.get("clarify_question"),
            "clarify_options": state.get("clarify_options", []),
            "rewritten_query": state.get("rewritten_query"),
        }
    return {
        "ok": True,
        "session_id": session_id,
        "output_signal": state.get("output_signal"),
        "rewritten_query": state.get("rewritten_query"),
        "tables": state.get("tables"),
        # 检索过程明细（向量召回 / 工作集沿用 / join 扩展），与 LangSmith trace 对照用
        "retrieval_debug": state.get("retrieval_debug"),
        "sql": state["sql"],
        "columns": state["columns"],
        "rows": state["rows"],
        "row_count": state["row_count"],
        # output_signal ∈ {conclusion, both} 时的分析结论（dataset 时空串）
        "conclusion": state.get("conclusion", ""),
        "retry_count": state.get("retry_count", 0),
        "digest": state["digest"],
    }
