"""StateGraph 组装（M3）：rewrite → (clarify | retrieve → generate → validate ⇄ repair → execute) → summarize。

M3 新增：validate/execute 确定性错误进 repair 修复循环（bounded，上限 MAX_SQL_RETRIES）；
rewrite 判定含糊问题转 clarify 追问（简化状态法，不使用 interrupt）。

Checkpointer：配置了 CHATBI_PG_DSN 用 PostgresSaver（多轮持久化），否则 MemorySaver（单进程内存）。
"""
import logging
from functools import lru_cache

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph

from app.config import get_settings
from app.graph.nodes import (
    clarify_node,
    execute_node,
    generate_node,
    repair_node,
    retrieve_node,
    rewrite_node,
    summarize_node,
    validate_node,
)
from app.graph.state import ChatState

logger = logging.getLogger(__name__)

# 有确定性错误信号、可交给 repair 循环自愈的错误码（防线三）；
# 其余（权限/检索/LLM 基础设施类）重试无意义，直接结构化返回
RETRYABLE_CODES = frozenset({
    "PARSE_ERROR",
    "UNKNOWN_TABLE",
    "UNKNOWN_COLUMN",
    "UNKNOWN_QUALIFIER",
    "NON_SELECT",
    "DANGEROUS_OP",
    "MULTI_STATEMENT",
    "EMPTY_SQL",
    "EXECUTE_ERROR",
    "ROWS_EXCEEDED",
})


def _is_error(s: ChatState) -> bool:
    return bool(s.get("error_code"))


def _route_validate(s: ChatState) -> str:
    if not _is_error(s):
        return "execute"
    if s.get("error_code") in RETRYABLE_CODES and s.get("retry_count", 0) < get_settings().max_sql_retries:
        return "repair"
    return END


def _route_execute(s: ChatState) -> str:
    if not _is_error(s):
        return "summarize"
    if s.get("error_code") in RETRYABLE_CODES and s.get("retry_count", 0) < get_settings().max_sql_retries:
        return "repair"
    return END


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
    g.add_node("clarify", clarify_node)
    g.add_node("retrieve", retrieve_node)
    g.add_node("generate", generate_node)
    g.add_node("validate", validate_node)
    g.add_node("repair", repair_node)
    g.add_node("execute", execute_node)
    g.add_node("summarize", summarize_node)

    g.add_edge(START, "rewrite")
    g.add_conditional_edges(
        "rewrite",
        lambda s: "clarify" if s.get("output_signal") == "clarify" and not _is_error(s) else ("retrieve" if not _is_error(s) else END),
    )
    g.add_conditional_edges("retrieve", lambda s: "generate" if not _is_error(s) else END)
    g.add_conditional_edges("generate", lambda s: "validate" if not _is_error(s) else END)
    g.add_conditional_edges("validate", _route_validate)
    g.add_edge("repair", "validate")
    g.add_conditional_edges("execute", _route_execute)
    g.add_edge("summarize", END)

    checkpointer = _get_checkpointer()
    return g.compile(checkpointer=checkpointer)
