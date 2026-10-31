"""Prompt 拼装：元数据库记录 → CREATE TABLE 风格 DDL 文本（含中文注释，LLM 选表选列的依据）。

M1 全量 schema 进 prompt（演示库表少）；M2 替换为 pgvector 检索 + join 图扩展。
"""
from sqlalchemy import select
from sqlalchemy.engine import Engine

from app.models import column_meta, table_meta


def build_schema_prompt(meta_engine: Engine, datasource_id: int = 1) -> str:
    """按表分组生成 DDL；无数据时抛错（提示先执行采集）。"""
    with meta_engine.connect() as conn:
        tables = conn.execute(
            select(table_meta.c.id, table_meta.c.table_name, table_meta.c.table_comment)
            .where(table_meta.c.datasource_id == datasource_id)
            .order_by(table_meta.c.table_name)
        ).all()
        if not tables:
            raise ValueError("元数据库为空，请先调用 POST /api/schema/collect 采集表结构")

        cols = conn.execute(
            select(
                column_meta.c.table_meta_id,
                column_meta.c.column_name,
                column_meta.c.data_type,
                column_meta.c.is_nullable,
                column_meta.c.column_key,
                column_meta.c.column_comment,
                column_meta.c.ordinal_position,
            ).order_by(column_meta.c.table_meta_id, column_meta.c.ordinal_position)
        ).all()

    cols_by_table: dict[int, list] = {}
    for c in cols:
        cols_by_table.setdefault(c.table_meta_id, []).append(c)

    chunks: list[str] = []
    for t in tables:
        lines = [f"CREATE TABLE `{t.table_name}` ("]
        col_lines = []
        for c in cols_by_table.get(t.id, []):
            nullable = "" if c.is_nullable == "YES" else " NOT NULL"
            pk = " PRIMARY KEY" if c.column_key == "PRI" else ""
            comment = f" COMMENT '{c.column_comment}'" if c.column_comment else ""
            col_lines.append(f"  `{c.column_name}` {c.data_type}{nullable}{pk}{comment}")
        lines.append(",\n".join(col_lines))
        lines.append(f") COMMENT='{t.table_comment}';")
        chunks.append("\n".join(lines))

    return "\n\n".join(chunks)
