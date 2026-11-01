"""LangGraph 会话状态（M2）：多轮 checkpointer 持久化。

turns 用 reducer 追加（每轮历史）；tables 跨轮保留（跟进式问题沿用工作集，is_new_topic 时重置）。
"""
import operator
from typing import Annotated, Any, TypedDict


class TurnRecord(TypedDict, total=False):
    question: str
    rewritten: str
    sql: str
    error_code: str


class ChatState(TypedDict, total=False):
    session_id: str
    user_id: int
    # 本轮输入
    question: str
    # rewrite 三合一输出
    rewritten_query: str
    is_new_topic: bool
    output_signal: str  # conclusion / dataset / both / clarify（M3 输出路由使用）
    metric_constraints: str
    # 检索输出（跨轮保留：跟进式问题沿用工作集）
    tables: list[str]
    # 检索过程可观测：LangSmith trace 的 retrieve 节点输出里可见三路来源明细
    retrieval_debug: dict[str, Any]
    # 生成/校验/执行
    sql: str
    error_code: str
    error: str
    columns: list[str]
    rows: list[list[Any]]
    row_count: int
    digest: str
    # 历史轮次（reducer 追加）
    turns: Annotated[list[TurnRecord], operator.add]
