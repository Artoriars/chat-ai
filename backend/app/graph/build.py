"""StateGraph 组装。M1 单轮：校验/执行失败即短路返回结构化错误。"""
from functools import lru_cache

from langgraph.graph import END, START, StateGraph

from app.graph.nodes import execute_node, generate_node, summarize_node, validate_node
from app.graph.state import ChatState


@lru_cache
def get_chat_graph():
    g = StateGraph(ChatState)
    g.add_node("generate", generate_node)
    g.add_node("validate", validate_node)
    g.add_node("execute", execute_node)
    g.add_node("summarize", summarize_node)

    g.add_edge(START, "generate")
    g.add_conditional_edges("generate", lambda s: "validate" if not s.get("error_code") else END)
    g.add_conditional_edges("validate", lambda s: "execute" if not s.get("error_code") else END)
    g.add_conditional_edges("execute", lambda s: "summarize" if not s.get("error_code") else END)
    g.add_edge("summarize", END)
    return g.compile()
