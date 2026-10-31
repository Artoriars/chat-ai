"""StateGraph 组装（M2）：rewrite → retrieve → generate → validate → execute → summarize。

Checkpointer：配置了 CHATBI_PG_DSN 用 PostgresSaver（多轮持久化），否则 MemorySaver（单进程内存）。
"""
import logging
from functools import lru_cache

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph

from app.config import get_settings
from app.graph.nodes import (
    execute_node,
    generate_node,
    retrieve_node,
    rewrite_node,
    summarize_node,
    validate_node,
)
from app.graph.state import ChatState

logger = logging.getLogger(__name__)


def _is_error(s: ChatState) -> bool:
    return bool(s.get("error_code"))


@lru_cache
def _get_checkpointer() -> BaseCheckpointSaver | None:
    if not get_settings().chatbi_pg_dsn:
        return None
    from langgraph.checkpoint.postgres import PostgresSaver

    from app.pgstore import get_pg_pool

    saver = PostgresSaver(get_pg_pool())  # 复用 pgvector 同一连接池
    saver.setup()  # 首次建 checkpoint 表（幂等）
    logger.info("checkpointer: postgres")
    return saver


@lru_cache
def get_chat_graph():
    g = StateGraph(ChatState)
    g.add_node("rewrite", rewrite_node)
    g.add_node("retrieve", retrieve_node)
    g.add_node("generate", generate_node)
    g.add_node("validate", validate_node)
    g.add_node("execute", execute_node)
    g.add_node("summarize", summarize_node)

    g.add_edge(START, "rewrite")
    g.add_conditional_edges("rewrite", lambda s: "retrieve" if not _is_error(s) else END)
    g.add_conditional_edges("retrieve", lambda s: "generate" if not _is_error(s) else END)
    g.add_conditional_edges("generate", lambda s: "validate" if not _is_error(s) else END)
    g.add_conditional_edges("validate", lambda s: "execute" if not _is_error(s) else END)
    g.add_conditional_edges("execute", lambda s: "summarize" if not _is_error(s) else END)
    g.add_edge("summarize", END)

    checkpointer = _get_checkpointer()
    return g.compile(checkpointer=checkpointer)
