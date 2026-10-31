"""Join 关系图（M2）：从列名约定推导表间关联边（<x>_id → 表 x / xs），沿边做 1~2 跳扩展。

M1 采集未存显式外键（information_schema 外键多为缺失），命名约定是最可靠的确定性来源；
后续若采集到真实 FK，可在 build_join_edges 里优先使用。
"""
import re
from collections import defaultdict
from typing import Any

_ID_SUFFIX_RE = re.compile(r"^(.+)_id$")


def build_join_edges(tables: dict[str, set[str]]) -> dict[str, set[str]]:
    """{表名: {列名}} → {表名: {可关联表名}}（双向边，去自环）。"""
    edges: dict[str, set[str]] = defaultdict(set)
    names = set(tables)
    for t, cols in tables.items():
        for c in cols:
            m = _ID_SUFFIX_RE.match(c)
            if not m:
                continue
            base = m.group(1)
            for cand in (base, base + "s"):
                if cand != t and cand in names:
                    edges[t].add(cand)
                    edges[cand].add(t)
                    break
    return edges


def expand_seeds(
    seeds: set[str],
    edges: dict[str, set[str]],
    max_hops: int = 1,
    max_tables: int = 12,
) -> set[str]:
    """从种子表沿 join 边 BFS 扩展，返回种子+邻接表（截断到 max_tables）。"""
    result = set(seeds)
    frontier = set(seeds)
    for _ in range(max_hops):
        nxt: set[str] = set()
        for t in frontier:
            for n in edges.get(t, ()):
                if n not in result:
                    nxt.add(n)
        if not nxt:
            break
        for n in sorted(nxt):
            if len(result) >= max_tables:
                return result
            result.add(n)
        frontier = nxt
    return result
