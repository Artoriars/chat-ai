"""LangGraph 会话状态定义（M1 单轮；M2 扩展为会话级 working_set/history）。"""
from typing import Any, TypedDict


class ChatState(TypedDict, total=False):
    question: str
    sql: str
    error_code: str
    error: str
    columns: list[str]
    rows: list[list[Any]]
    row_count: int
    digest: str
