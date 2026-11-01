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
        if t.get("clarify"):
            line += f" | 系统追问: {t['clarify']}"
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
        # 自愈重试为本轮临时上下文，同样每轮清零
        "retry_count": 0,
        "repair_feedback": "",
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


_CLARIFY_PROMPT = """你是 BI 助手的追问生成器。用户的问题太含糊，无法确定查询意图，请生成一条追问。

规则：
- clarify_question：一条具体、友好的追问，帮用户补充关键信息（想看的指标、维度、时间范围、条数等）。
- options：2~4 个候选快捷选项，每个不超过 20 字，用户点选即可作为下一轮输入。
- 只输出一个 JSON 对象，格式：{"clarify_question": "...", "options": ["...", "..."]}"""


def _parse_clarify_json(content: str, fallback_question: str) -> dict[str, Any]:
    m = _JSON_RE.search(content)
    default = {"question": fallback_question, "options": []}
    if not m:
        return default
    try:
        obj = json.loads(m.group(0))
        options = [str(o).strip() for o in (obj.get("options") or []) if str(o).strip()][:4]
        return {
            "question": str(obj.get("clarify_question") or fallback_question),
            "options": options,
        }
    except (ValueError, TypeError):
        return default


def clarify_node(state: ChatState) -> ChatState:
    """问题含糊时生成追问与快捷选项；LLM 失败降级为模板追问，不阻断。"""
    fallback = f"「{state['question']}」想看什么？请补充具体指标、维度或时间范围。"
    try:
        resp = _llm().invoke(
            [SystemMessage(_CLARIFY_PROMPT), HumanMessage(state["rewritten_query"])]
        )
        parsed = _parse_clarify_json(_message_text(resp.content), fallback)
    except Exception as e:
        logger.warning("clarify LLM failed, fallback template: %s", e)
        parsed = {"question": fallback, "options": []}
    return {
        "clarify_question": parsed["question"],
        "clarify_options": parsed["options"],
        "turns": [{"question": state["question"], "clarify": parsed["question"]}],
        "error_code": None,
        "error": None,
    }


def _retrieve_impl(state: ChatState) -> ChatState:
    settings = get_settings()
    allowed = allowed_tables(get_meta_engine(), state.get("user_id", 1))
    if allowed is not None and not allowed:
        return {"error_code": "PERMISSION_DENIED", "error": "当前用户未授权任何数据表"}

    tables: set[str] = set()
    from_working_set: list[str] = []
    if state.get("tables"):  # 跨轮工作集（跟进式问题优先沿用）
        tables |= set(state["tables"])
        from_working_set = sorted(state["tables"])

    embedder = get_embedder()
    if embedder.enabled:
        # 向量召回种子 + join 图扩展（权限过滤在召回层先行裁剪）
        query_vec = embedder.embed_query(state["rewritten_query"])
        seeds = search_cards(1, query_vec, settings.retrieve_top_k, allowed_tables=allowed)
        all_tables = _all_table_names()
        edges = build_join_edges(all_tables)
        expanded = expand_seeds(set(seeds), edges, max_hops=1, max_tables=settings.max_context_tables)
        tables |= expanded
        debug = {
            "mode": "vector",
            "vector_hits": seeds,
            "from_working_set": from_working_set,
            "join_expanded": sorted(expanded - set(seeds) - set(from_working_set)),
        }
    else:
        # 降级：全量 schema（权限内）
        tables |= allowed if allowed is not None else set(_all_table_names())
        debug = {"mode": "full_schema_fallback", "vector_hits": [], "from_working_set": from_working_set}

    if not tables:
        return {"error_code": "NO_SCHEMA", "error": "未检索到可用表，请先采集表结构"}
    return {"tables": sorted(tables), "retrieval_debug": debug}


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
    try:
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
    except Exception as e:
        # 结构化反馈给自愈修复循环；message 保留 MySQL 原始错误（含表/列不存在信号）
        logger.warning("execute failed: %s", e)
        return {"error_code": "EXECUTE_ERROR", "error": f"SQL 执行失败: {e}"}
    return {"columns": columns, "rows": rows, "row_count": len(rows)}


_REPAIR_PROMPT = """你是 SQL 修复器。下面这条 MySQL SELECT 语句在校验或执行时失败，请根据错误反馈修复它。

规则（必须全部遵守）：
1. 只修正错误，保持原查询意图不变；若存在指标口径硬约束，禁止改写口径。
2. 只能使用下方表结构中出现的表和列，禁止编造任何不存在的表、列或函数。
3. 用户明确指定返回条数时保留对应 LIMIT N，否则不要写 LIMIT（系统会注入默认值）。
4. 只输出修复后的 SQL 本身，不要输出任何解释或 markdown 代码块。

可用表结构：
{schema}
{metric_block}

历史尝试与错误反馈（按时间累积，最后一条是最近失败）：
{feedback}"""


def _needs_schema_refresh(error_code: str, error_msg: str) -> bool:
    """query-time 兜底触发条件：元库可能落后于业务库真实结构。"""
    if error_code in ("UNKNOWN_TABLE", "UNKNOWN_COLUMN"):
        return True
    if error_code == "EXECUTE_ERROR":
        return "doesn't exist" in error_msg.lower() or "不存在" in error_msg
    return False


def repair_node(state: ChatState) -> ChatState:
    """自愈修复循环：累积「尝试+错误」结构化反馈，LLM 只修正错误不改意图。

    库层报表/列不存在时先对涉及表定向刷新元数据（定时同步的 query-time 兜底）再修复。
    """
    error_code = state.get("error_code", "")
    error_msg = state.get("error", "")

    if _needs_schema_refresh(error_code, error_msg) and state.get("tables"):
        try:
            from app.collector import refresh_tables

            stats = refresh_tables(1, list(state["tables"]))
            logger.info("repair 兜底刷新: %s", stats)
        except Exception as e:
            logger.warning("repair 兜底刷新失败（继续修复）: %s", e)

    feedback = state.get("repair_feedback", "")
    entry = (
        f"尝试 {state.get('retry_count', 0) + 1}\n"
        f"SQL: {state.get('sql') or '(未生成)'}\n"
        f"错误({error_code}): {error_msg}"
    )
    feedback = f"{feedback}\n\n---\n\n{entry}".strip()

    schema_ddl = build_schema_prompt(get_meta_engine(), only_tables=set(state["tables"]))
    system = _REPAIR_PROMPT.format(
        schema=schema_ddl, metric_block=state.get("metric_constraints", ""), feedback=feedback
    )
    try:
        resp = _llm().invoke(
            [SystemMessage(system), HumanMessage(f"原始问题: {state['rewritten_query']}")]
        )
    except Exception as e:
        logger.warning("repair LLM invoke failed: %s", e)
        return {"error_code": "LLM_ERROR", "error": f"修复调用失败: {e}", "repair_feedback": feedback}
    sql = extract_sql(_message_text(resp.content))
    if not sql:
        return {
            "error_code": "EMPTY_SQL",
            "error": "修复未返回有效 SQL",
            "repair_feedback": feedback,
        }
    # 必须显式清空错误标记（checkpointer 持久化），否则条件边在 validate 前误判短路
    return {
        "sql": sql,
        "retry_count": state.get("retry_count", 0) + 1,
        "repair_feedback": feedback,
        "error_code": None,
        "error": None,
    }


_CONCLUSION_PROMPT = """你是数据分析师。根据用户问题与查询结果，用中文给出简洁的分析结论。

规则：
- 直接给结论与关键数字，最多 5 条要点；不要复述 SQL；不要编造结果中不存在的数字。

用户问题：{question}
结果列: {columns}
结果预览（共 {row_count} 行，下面只展示前 10 行）：
{rows}"""


def _preview_rows(rows: list[list[Any]], max_rows: int = 10, max_cell: int = 80) -> str:
    """结果集预览文本：行数与单元格长度双重截断，控制结论 prompt 体积。"""
    lines = []
    for row in rows[:max_rows]:
        cells = [str(v)[:max_cell] if v is not None else "NULL" for v in row]
        lines.append(" | ".join(cells))
    if len(rows) > max_rows:
        lines.append(f"...（其余 {len(rows) - max_rows} 行省略）")
    return "\n".join(lines) or "（查询结果为空）"


def _conclusion_text(state: ChatState) -> str:
    prompt = _CONCLUSION_PROMPT.format(
        question=state["rewritten_query"],
        columns=", ".join(state["columns"]),
        row_count=state["row_count"],
        rows=_preview_rows(state["rows"]),
    )
    resp = _llm().invoke([SystemMessage(prompt), HumanMessage("请输出分析结论")])
    return _message_text(resp.content).strip()


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

    # 输出路由：conclusion / both → 生成分析结论（失败降级为空串，不阻断结果返回）
    if state.get("output_signal") in ("conclusion", "both") and "row_count" in state:
        try:
            update["conclusion"] = _conclusion_text(state)
        except Exception as e:
            logger.warning("结论生成失败（不影响结果返回）: %s", e)
            update["conclusion"] = ""

    # question-SQL 对沉淀（LangSmith dataset；未配置或失败不阻断主流程）
    if state.get("sql") and not state.get("error_code"):
        try:
            from app.qa_store import record_qa_pair

            record_qa_pair(
                question=state["question"],
                sql=state["sql"],
                session_id=state.get("session_id", ""),
                healed=state.get("retry_count", 0) > 0,
                retry_count=state.get("retry_count", 0),
            )
        except Exception as e:
            logger.warning("QA 沉淀失败: %s", e)
    return update
