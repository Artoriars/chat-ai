"""SQL 静态安全检查（M1 第二道防线的拦截部分）。

职责：方言解析、只允许单条 SELECT、危险操作拒绝、表/列存在性校验（对照元数据库）、
LIMIT 注入与上限钳制。执行计划检查（EXPLAIN）在执行节点做（需要连接数据库）。

M1 校验失败直接返回结构化错误；bounded 重试自愈是 M3 范围。
"""
from typing import Any

import sqlglot
from sqlglot import exp
from sqlalchemy import select
from sqlalchemy.engine import Engine

from app.models import column_meta, table_meta

# 危险节点类型：出现即拒绝。exp.Alter 在不同 sqlglot 版本下名称有差异，做兼容探测。
_DENY_TYPES: tuple[type[exp.Expression], ...] = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Drop,
    exp.Create,
    exp.Command,  # SET / LOCK / KILL 等无法安全解析的语句
)
for _name in ("Alter", "AlterTable", "Grant", "TruncateTable", "Into"):
    _t = getattr(exp, _name, None)
    if isinstance(_t, type) and issubclass(_t, exp.Expression):
        _DENY_TYPES = (*_DENY_TYPES, _t)

# 语句级字符串黑名单（parse 之前拦截，防止解析器放行）
_DENY_PATTERNS = ("into outfile", "into dumpfile")

_ROOT_ALLOWED = (exp.Select, exp.Union, exp.Except, exp.Intersect)


class SecurityError(Exception):
    """结构化安全错误：code 用于 API 返回与 M3 自愈路由。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def load_valid_tables(meta_engine: Engine, datasource_id: int = 1) -> dict[str, set[str]]:
    """元数据库 → {表名: {列名集合}}。空库时抛错（提示先采集）。"""
    with meta_engine.connect() as conn:
        rows = conn.execute(
            select(table_meta.c.table_name, column_meta.c.column_name)
            .select_from(
                table_meta.join(column_meta, column_meta.c.table_meta_id == table_meta.c.id)
            )
            .where(table_meta.c.datasource_id == datasource_id)
        ).all()
    if not rows:
        raise ValueError("元数据库为空，请先调用 POST /api/schema/collect 采集表结构")
    valid: dict[str, set[str]] = {}
    for table_name, column_name in rows:
        valid.setdefault(table_name, set()).add(column_name)
    return valid


def parse_and_check(
    sql: str,
    valid_tables: dict[str, set[str]],
    default_limit: int,
    max_limit: int,
) -> str:
    """校验并规范化 SQL，返回注入/钳制 LIMIT 后的规范语句；不合规抛 SecurityError。"""
    if not sql or not sql.strip():
        raise SecurityError("EMPTY_SQL", "生成的 SQL 为空")

    lowered = sql.lower()
    for pattern in _DENY_PATTERNS:
        if pattern in lowered:
            raise SecurityError("DANGEROUS_OP", f"禁止使用 {pattern.upper()}")

    try:
        statements = sqlglot.parse(sql, dialect="mysql")
    except sqlglot.errors.ParseError as e:
        raise SecurityError("PARSE_ERROR", f"SQL 解析失败: {e}") from e

    statements = [s for s in statements if s is not None]
    if len(statements) != 1:
        raise SecurityError("MULTI_STATEMENT", "仅允许单条 SELECT 语句")
    root = statements[0]

    if not isinstance(root, _ROOT_ALLOWED):
        raise SecurityError("NON_SELECT", "仅允许 SELECT 查询语句")

    for node in root.walk():
        if isinstance(node, _DENY_TYPES):
            raise SecurityError("DANGEROUS_OP", f"检测到禁止的操作: {type(node).__name__}")

    # CTE 名不是真实表，跳过其存在性校验（sqlglot 不同版本 arg key 为 with_ / with）
    cte_names: set[str] = set()
    with_clause = root.args.get("with_") or root.args.get("with")
    if with_clause is not None:
        cte_names = {cte.alias_or_name for cte in with_clause.find_all(exp.CTE)}

    tables: set[str] = set()
    alias_map: dict[str, str] = {}
    for t in root.find_all(exp.Table):
        # 别名映射对所有 Table 节点建立（含 CTE 引用），存在性检查仅针对真实表
        alias_map[t.alias_or_name] = t.name
        if t.name not in cte_names:
            tables.add(t.name)

    unknown = tables - set(valid_tables)
    if unknown:
        raise SecurityError(
            "UNKNOWN_TABLE",
            f"引用了不存在的表: {', '.join(sorted(unknown))}；可用表: {', '.join(sorted(valid_tables))}",
        )

    referenced_columns: set[str] = set()
    for cols in (valid_tables[t] for t in tables):
        referenced_columns |= cols

    # SELECT 输出别名（如 SUM(x) AS total）在 MySQL 中可被 ORDER BY / GROUP BY / HAVING 引用
    select_aliases: set[str] = set()
    for a in root.find_all(exp.Alias):
        if a.alias:
            select_aliases.add(a.alias)

    for col in root.find_all(exp.Column):
        if col.table:
            resolved = alias_map.get(col.table)
            if resolved is None:
                raise SecurityError("UNKNOWN_QUALIFIER", f"未知的表别名或表名: {col.table}")
            if resolved in cte_names:
                continue  # CTE 输出列无法静态推断，M1 放行
            if col.name not in valid_tables[resolved]:
                raise SecurityError(
                    "UNKNOWN_COLUMN",
                    f"表 {resolved} 中不存在列 {col.name}；可用列: {', '.join(sorted(valid_tables[resolved]))}",
                )
        else:
            if col.name not in referenced_columns and col.name not in select_aliases:
                raise SecurityError(
                    "UNKNOWN_COLUMN",
                    f"引用的列 {col.name} 不在任何已引用表中；可用列: {', '.join(sorted(referenced_columns | select_aliases))}",
                )

    root = _ensure_limit(root, default_limit, max_limit)
    return root.sql(dialect="mysql")


def _ensure_limit(root: exp.Expression, default_limit: int, max_limit: int) -> exp.Expression:
    """无 LIMIT 注入默认值；已有 LIMIT 超上限则钳制到 max_limit。"""
    existing = root.args.get("limit")
    if existing is not None:
        value = existing.expression
        if isinstance(value, exp.Literal) and value.is_number:
            if int(value.this) <= max_limit:
                return root
    # 重新设置（覆盖超限值 / 注入默认值）
    root.set("limit", exp.Limit(expression=exp.Literal.number(max_limit if existing else default_limit)))
    return root


def valid_tables_summary(valid_tables: dict[str, set[str]]) -> str:
    return "; ".join(f"{t}({', '.join(sorted(cols))})" for t, cols in sorted(valid_tables.items()))
