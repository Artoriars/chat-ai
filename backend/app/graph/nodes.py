"""LangGraph 节点实现：generate → validate → execute → summarize。

M1 无 rewrite 节点（单轮直通）、无自愈重试（M3）。每个节点返回部分状态增量。
"""
import logging
import re
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from sqlalchemy import text

from app.config import get_settings
from app.db import get_business_engine, get_meta_engine
from app.graph.state import ChatState
from app.prompt import build_schema_prompt
from app.security import SecurityError, load_valid_tables, parse_and_check

logger = logging.getLogger(__name__)

_FENCE_RE = re.compile(r"```(?:sql)?\s*(.+?)\s*```", re.DOTALL | re.IGNORECASE)

_SYSTEM_PROMPT = """你是资深的 MySQL 数据分析师，负责把用户的自然语言问题转换为一条可执行的 MySQL SELECT 语句。

规则（必须全部遵守）：
1. 只能使用下方表结构中出现的表和列，禁止编造任何不存在的表、列或函数。
2. 只允许生成单条 SELECT 语句，禁止 INSERT/UPDATE/DELETE/DDL 等任何写操作。
3. 优先依据列注释中的口径语义选列；涉及金额统计时注意订单状态枚举（如已支付为 PAID，已退款为 REFUNDED，待支付为 PENDING）。
4. 用户明确指定返回条数（如「前5」「top10」）时写对应 LIMIT N；未指定条数时不要写 LIMIT，系统会注入默认值。
5. 只输出 SQL 本身，不要输出任何解释或 markdown 代码块。

可用表结构：
{schema}"""


def _message_text(content: Any) -> str:
    """兼容 str 与多模态 content 块列表两种返回形态。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(p.get("text", "") for p in content if isinstance(p, dict))
    return str(content)


def extract_sql(raw: str) -> str:
    """从 LLM 输出提取 SQL：优先取 markdown 围栏内容，否则整体视为 SQL。"""
    m = _FENCE_RE.search(raw)
    candidate = m.group(1) if m else raw
    return candidate.strip().rstrip(";").strip()


def generate_node(state: ChatState) -> ChatState:
    settings = get_settings()
    llm = ChatOpenAI(
        model=settings.llm_model,
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        temperature=settings.llm_temperature,
        timeout=60,
    )
    try:
        schema_ddl = build_schema_prompt(get_meta_engine())
    except ValueError as e:
        return {"error_code": "NO_SCHEMA", "error": str(e)}

    try:
        resp = llm.invoke(
            [
                SystemMessage(_SYSTEM_PROMPT.format(schema=schema_ddl)),
                HumanMessage(state["question"]),
            ]
        )
    except Exception as e:  # 网络/鉴权/超时等 LLM 网关侧错误，统一转结构化错误
        logger.warning("LLM invoke failed: %s", e)
        return {"error_code": "LLM_ERROR", "error": f"LLM 调用失败: {e}"}
    sql = extract_sql(_message_text(resp.content))
    if not sql:
        return {"error_code": "EMPTY_SQL", "error": "LLM 未返回有效 SQL"}
    logger.debug("generated sql: %s", sql)
    return {"sql": sql}


def validate_node(state: ChatState) -> ChatState:
    settings = get_settings()
    try:
        valid_tables = load_valid_tables(get_meta_engine())
        canonical = parse_and_check(
            state["sql"], valid_tables, settings.default_row_limit, settings.max_row_limit
        )
    except SecurityError as e:
        return {"error_code": e.code, "error": e.message}
    except ValueError as e:
        return {"error_code": "NO_SCHEMA", "error": str(e)}
    return {"sql": canonical}


def execute_node(state: ChatState) -> ChatState:
    settings = get_settings()
    engine = get_business_engine()
    with engine.connect() as conn:
        # EXPLAIN 估算扫描量：取各步骤 rows 估算的最大值（保守判定，宁拒勿漏）
        explain_rows = conn.execute(text("EXPLAIN " + state["sql"])).mappings().all()
        estimated = max((int(r.get("rows") or 0) for r in explain_rows), default=0)
        if estimated > settings.max_explain_rows:
            return {
                "error_code": "ROWS_EXCEEDED",
                "error": f"预估扫描行数 {estimated} 超过阈值 {settings.max_explain_rows}，已拒绝执行",
            }
        result = conn.execute(text(state["sql"]))
        columns = list(result.keys())
        rows = [list(row) for row in result]
    return {"columns": columns, "rows": rows, "row_count": len(rows)}


def summarize_node(state: ChatState) -> ChatState:
    settings = get_settings()
    digest = (
        f"返回 {state['row_count']} 行，列: {', '.join(state['columns'])}"
        f"（单次查询最多返回前 {settings.default_row_limit} 行）"
    )
    return {"digest": digest}
