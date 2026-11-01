"""LangSmith dataset 回归评估（手动执行）：沉淀样本重跑链路，对比新旧 SQL 结果集。

用法（backend/ 目录下）：
    .venv/Scripts/python scripts/run_eval.py [--dataset chatbi-regression] [--limit 20]

判定：新旧 SQL 都过安全检查 + 都可执行 + 结果集一致（行数与内容）→ pass。
需要真实 .env（LLM / 业务库 / LangSmith key）。
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from app.config import get_settings  # noqa: E402


def _fetch_examples(dataset_name: str, limit: int) -> list[dict]:
    from langsmith import Client

    client = Client()
    examples = list(client.list_examples(dataset_name=dataset_name))
    return examples[:limit]


def _execute_sql(sql: str) -> tuple[list[list], list[str]] | str:
    """执行 SQL（过同一套安全检查），返回 (rows, columns) 或错误字符串。"""
    from sqlalchemy import text

    from app.db import get_business_engine, get_meta_engine
    from app.security import SecurityError, load_valid_tables, parse_and_check

    s = get_settings()
    try:
        canonical = parse_and_check(sql, load_valid_tables(get_meta_engine()), s.default_row_limit, s.max_row_limit)
    except SecurityError as e:
        return f"security:{e.code}: {e.message}"
    try:
        with get_business_engine().connect() as conn:
            result = conn.execute(text(canonical))
            columns = list(result.keys())
            rows = [list(row) for row in result]
        return rows, columns
    except Exception as e:
        return f"execute: {e}"


def _run_case(question: str, expected_sql: str) -> dict:
    """单条样本回归：重跑链路（新 session）→ 双方执行 → 结果集比对。"""
    import uuid

    from app.graph.build import get_chat_graph

    session_id = f"eval-{uuid.uuid4().hex[:12]}"
    state = get_chat_graph().invoke(
        {"question": question, "user_id": 1, "session_id": session_id},
        config={"configurable": {"thread_id": session_id}, "run_name": "eval-case"},
    )
    case: dict = {"question": question, "session_id": session_id}

    if state.get("error_code"):
        case["verdict"] = "fail"
        case["detail"] = f"链路失败 {state['error_code']}: {state.get('error', '')}"
        return case
    if state.get("output_signal") == "clarify":
        case["verdict"] = "clarify"
        case["detail"] = state.get("clarify_question", "")
        return case

    new_sql = state["sql"]
    case["new_sql"] = new_sql
    old = _execute_sql(expected_sql)
    new = _execute_sql(new_sql)
    if isinstance(old, str):
        case["verdict"] = "skip"
        case["detail"] = f"沉淀 SQL 已不可执行（schema 漂移?）: {old}"
        return case
    if isinstance(new, str):
        case["verdict"] = "fail"
        case["detail"] = f"新 SQL 执行失败: {new}"
        return case
    if old == new:
        case["verdict"] = "pass"
        case["detail"] = f"结果集一致（{len(old[0])} 行）"
    else:
        case["verdict"] = "fail"
        case["detail"] = f"结果集不一致（沉淀 {len(old[0])} 行 vs 新 {len(new[0])} 行）"
    return case


def main() -> int:
    parser = argparse.ArgumentParser(description="chat-bi question-SQL 回归评估")
    parser.add_argument("--dataset", default=get_settings().langsmith_dataset)
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--out", default="", help="结果 JSON 输出路径（可选）")
    args = parser.parse_args()

    examples = _fetch_examples(args.dataset, args.limit)
    if not examples:
        print(f"dataset「{args.dataset}」无样本，先通过对话沉淀（需配置 LANGSMITH_API_KEY）")
        return 1

    results = []
    for ex in examples:
        question = ex.inputs["question"]
        sql = ex.outputs["sql"]
        try:
            results.append(_run_case(question, sql))
        except Exception as e:  # 单条失败不中断整体评估
            results.append({"question": question, "verdict": "error", "detail": str(e)})
        r = results[-1]
        print(f"[{r['verdict'].upper():7}] {question[:40]}  ->  {r['detail'][:80]}")

    passed = sum(1 for r in results if r["verdict"] == "pass")
    total = len(results)
    print(f"\n通过率: {passed}/{total} ({passed / total:.0%})")
    by_verdict: dict[str, int] = {}
    for r in results:
        by_verdict[r["verdict"]] = by_verdict.get(r["verdict"], 0) + 1
    print("分布:", json.dumps(by_verdict, ensure_ascii=False))
    if args.out:
        Path(args.out).write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"明细已写入 {args.out}")
    return 0 if passed == total else 2


if __name__ == "__main__":
    sys.exit(main())
