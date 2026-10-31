"""业务口径（M2）：别名命中 → 规范表达式硬约束注入。确定性匹配，不交给 LLM 自由发挥。"""
from sqlalchemy import text
from sqlalchemy.engine import Engine
from typing import Any

import json


def load_active_metrics(meta_engine: Engine) -> list[dict[str, Any]]:
    """仅 status='active' 的口径参与注入（pending_review 宁可不注入，不错误注入旧口径）。"""
    with meta_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT name, aliases, definition, expression, base_filter, time_semantics, version "
                "FROM metric WHERE status = 'active'"
            )
        ).mappings().all()
    metrics = []
    for r in rows:
        try:
            aliases = json.loads(r["aliases"]) if r["aliases"] else []
        except (TypeError, ValueError):
            aliases = []
        metrics.append({**dict(r), "aliases": aliases})
    return metrics


def match_metrics(metrics: list[dict[str, Any]], text_input: str) -> list[dict[str, Any]]:
    """按名称/别名对输入做不区分大小写的包含匹配（去重，一个指标最多命中一次）。"""
    lowered = text_input.lower()
    matched: dict[str, dict[str, Any]] = {}
    for m in metrics:
        candidates = [m["name"], *(a for a in m["aliases"] if a)]
        if any(c and c.lower() in lowered for c in candidates):
            matched[m["name"]] = m
    return list(matched.values())


def constraint_block(matched: list[dict[str, Any]]) -> str:
    """口径命中后的硬约束文本段（注入 system prompt，LLM 禁止改写）。"""
    if not matched:
        return ""
    lines = ["以下业务指标口径已由系统确定，本查询必须严格使用，禁止自行改写表达式或过滤条件："]
    for m in matched:
        lines.append(
            f"- 指标「{m['name']}」(v{m['version']})：{m['definition']}\n"
            f"  规范表达式: {m['expression']}"
        )
        if m.get("base_filter"):
            lines.append(f"  基础过滤: {m['base_filter']}")
        if m.get("time_semantics"):
            lines.append(f"  时间语义: {m['time_semantics']}")
    return "\n".join(lines)
