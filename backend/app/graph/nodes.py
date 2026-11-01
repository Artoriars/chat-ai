"""LangGraph 节点（M2）：rewrite 三合一 → retrieve → generate → validate → execute → summarize。

rewrite 一次 LLM 调用产出：rewritten_query（指代消解）、is_new_topic（工作集重置）、output_signal。
口径别名命中在 rewrite 后做确定性匹配，命中即硬约束注入，LLM 不许改写。
无 embedding 配置时 retrieve 降级为全量 schema（权限过滤仍生效）。
M1 的教训在此继承：所有失败路径都返回结构化错误，不裸抛。
"""
import json
import logging
import re
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from sqlalchemy import text

from app.config import get_settings
from app.db import get_business_engine, get_meta_engine
from app.graph.state import ChatState
from app.join_graph import build_join_edges, expand_seeds
from app.metric import constraint_block, load_active_metrics, match_metrics
from app.permission import allowed_tables, filter_tables
from app.pgstore import search_cards
from app.embedder import get_embedder
from app.prompt import build_schema_prompt
from app.security import SecurityError, load_valid_tables, parse_and_check

logger = logging.getLogger(__name__)

_FENCE_RE = re.compile(r"```(?:sql)?\s*(.+?)\s*```", re.DOTALL | re.IGNORECASE)
_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)

_SYSTEM_PROMPT = """你是资深的 MySQL 数据分析师，负责把用户的自然语言问题转换为一条可执行的 MySQL SELECT 语句。

规则（必须全部遵守）：
1. 只能使用下方表结构中出现的表和列，禁止编造任何不存在的表、列或函数。
2. 只允许生成单条 SELECT 语句，禁止 INSERT/UPDATE/DELETE/DDL 等任何写操作。
3. 优先依据列注释中的口径语义选列；涉及金额统计时注意订单状态枚举（如已支付为 PAID，已退款为 REFUNDED，待支付为 PENDING）。
4. 用户明确指定返回条数（如「前5」「top10」）时写对应 LIMIT N；未指定条数时不要写 LIMIT，系统会注入默认值。
5. 若下方存在指标口径硬约束，必须严格使用其规范表达式与过滤条件，禁止改写口径。
6. 只输出 SQL 本身，不要输出任何解释或 markdown 代码块。

可用表结构：
{schema}
{metric_block}"""

_REWRITE_PROMPT = """你是多轮对话查询改写器。根据历史对话把用户本轮输入改写为自包含的查询问题，并做三个判定。

规则：
- rewritten_query：改写后的问题，必须脱离历史也能独立理解（如「再按月份拆一下」→「按月份拆分上一查询的销售额」）。
- is_new_topic：用户是否切换了新话题（true 时后续检索不沿用上一轮的表）。
- output_signal：本轮用户想要的输出。conclusion=只要分析结论；dataset=要明细数据/导出；both=两者都要；clarify=问题太含糊需要追问。
- 若历史为空，rewritten_query 原样保留输入，is_new_topic=true。

历史对话：
{history}

用户本轮输入：{question}

只输出一个 JSON 对象，格式：{{"rewritten_query": "...", "is_new_topic": true/false, "output_signal": "conclusion|dataset|both|clarify"}}"""


def _message_text(content: Any) -> str:
    """兼容 str 与多模态 content 块列表两种返回形态。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(p.get("text", "") for p in content if isinstance(p, dict))
    return str(content)


def _llm() -> ChatOpenAI:
    s = get_settings()
    return ChatOpenAI(
        model=s.llm_model,
        base_url=s.llm_base_url,
        api_key=s.llm_api_key,
        temperature=s.llm_temperature,
        timeout=60,
    )


def extract_sql(raw: str) -> str:
    """从 LLM 输出提取 SQL：优先取 markdown 围栏内容，否则整体视为 SQL。"""
    m = _FENCE_RE.search(raw)
    candidate = m.group(1) if m else raw
    return candidate.strip().rstrip(";").strip()


def _history_text(turns: list[dict[str, Any]] | None) -> str:
    """最近 3 轮历史（含结果 digest 由上层 turns 记录的 sql 摘要替代）。"""
    if not turns:
        return "（无）"
    lines = []
    for i, t in enumerate(turns[-3:], 1):
        line = f"{i}. 问: {t.get('question', '')}"
        if t.get("sql"):
            line += f" | 生成SQL: {t['sql'][:120]}"
        if t.get("error_code"):
            line += f" | 失败({t['error_code']})"
        lines.append(line)
    return "\n".join(lines)


def _parse_rewrite_json(content: str, fallback_question: str) -> dict[str, Any]:
    """解析 rewrite 输出 JSON；解析失败降级为原样透传（不阻断流程）。"""
    m = _JSON_RE.search(content)
    if not m:
        return {"rewritten_query": fallback_question, "is_new_topic": True, "output_signal": "both"}
    try:
        obj = json.loads(m.group(0))
        return {
            "rewritten_query": str(obj.get("rewritten_query") or fallback_question),
            "is_new_topic": bool(obj.get("is_new_topic", True)),
            "output_signal": str(obj.get("output_signal") or "both"),
        }
    except (ValueError, TypeError):
        return {"rewritten_query": fallback_question, "is_new_topic": True, "output_signal": "both"}


def rewrite_node(state: ChatState) -> ChatState:
    try:
        resp = _llm().invoke(
            [
                SystemMessage(_REWRITE_PROMPT.format(history=_history_text(state.get("turns")), question=state["question"])),
                HumanMessage("请输出 JSON"),
            ]
        )
    except Exception as e:
        logger.warning("LLM invoke failed: %s", e)
        return {"error_code": "LLM_ERROR", "error": f"LLM 调用失败: {e}"}

    parsed = _parse_rewrite_json(_message_text(resp.content), state["question"])

    # 口径确定性匹配：本轮输入 + 改写文本都参与
    metrics = load_active_metrics(get_meta_engine())
    matched = match_metrics(metrics, f"{state['question']} {parsed['rewritten_query']}")

    out: ChatState = {
        "rewritten_query": parsed["rewritten_query"],
        "is_new_topic": parsed["is_new_topic"],
        "output_signal": parsed["output_signal"],
        "metric_constraints": constraint_block(matched),
        "turns": [{"question": state["question"], "rewritten": parsed["rewritten_query"]}],
        # 每轮开局必须清空上轮遗留的错误标记（checkpointer 会持久化失败状态，
        # 不清空则条件边在起点就误判短路，返回过期数据）
        "error_code": None,
        "error": None,
    }
    if parsed["is_new_topic"]:
        out["tables"] = []  # 重置工作集
    return out


def retrieve_node(state: ChatState) -> ChatState:
    try:
        return _retrieve_impl(state)
    except Exception as e:
        logger.exception("retrieve failed")
        return {"error_code": "RETRIEVE_ERROR", "error": f"检索失败: {e}"}


def _retrieve_impl(state: ChatState) -> ChatState:
    settings = get_settings()
    allowed = allowed_tables(get_meta_engine(), state.get("user_id", 1))
    if allowed is not None and not allowed:
        return {"error_code": "PERMISSION_DENIED", "error": "当前用户未授权任何数据表"}

    tables: set[str] = set()
    if state.get("tables"):  # 跨轮工作集（跟进式问题优先沿用）
        tables |= set(state["tables"])

    embedder = get_embedder()
    if embedder.enabled:
        # 向量召回种子 + join 图扩展（权限过滤在召回层先行裁剪）
        query_vec = embedder.embed_query(state["rewritten_query"])
        seeds = search_cards(1, query_vec, settings.retrieve_top_k, allowed_tables=allowed)
        all_tables = _all_table_names()
        edges = build_join_edges(all_tables)
        tables |= expand_seeds(set(seeds), edges, max_hops=1, max_tables=settings.max_context_tables)
    else:
        # 降级：全量 schema（权限内）
        tables |= allowed if allowed is not None else set(_all_table_names())

    if not tables:
        return {"error_code": "NO_SCHEMA", "error": "未检索到可用表，请先采集表结构"}
    return {"tables": sorted(tables)}


def _all_table_names() -> dict[str, set[str]]:
    return load_valid_tables(get_meta_engine())


def generate_node(state: ChatState) -> ChatState:
    schema_ddl = build_schema_prompt(get_meta_engine(), only_tables=set(state["tables"]))
    system = _SYSTEM_PROMPT.format(schema=schema_ddl, metric_block=state.get("metric_constraints", ""))
    try:
        resp = _llm().invoke([SystemMessage(system), HumanMessage(state["rewritten_query"])])
    except Exception as e:
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
    # 本轮落史：追加最终 SQL / 错误（turns 的 question 已在 rewrite 记录）
    update: ChatState = {"digest": digest}
    if state.get("sql"):
        update["turns"] = [{"sql": state["sql"], "error_code": state.get("error_code", "")}]
    return update
