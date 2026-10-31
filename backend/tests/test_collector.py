"""collector 单测：采集结果落库（用 sqlite 内存库隔离 MySQL 依赖）与结构指纹。"""
from sqlalchemy import create_engine, func, select

from app.collector import _table_hash, upsert_schema
from app.models import column_meta, metadata, table_meta

TABLES = [
    {"table_name": "users", "table_comment": "用户表", "table_rows": 8},
    {"table_name": "orders", "table_comment": "订单表", "table_rows": 15},
]
COLUMNS = [
    {"table_name": "users", "column_name": "id", "data_type": "bigint", "is_nullable": "NO", "column_key": "PRI", "column_comment": "用户ID", "ordinal_position": 1},
    {"table_name": "users", "column_name": "username", "data_type": "varchar(64)", "is_nullable": "NO", "column_key": "", "column_comment": "用户名", "ordinal_position": 2},
    {"table_name": "orders", "column_name": "id", "data_type": "bigint", "is_nullable": "NO", "column_key": "PRI", "column_comment": "订单ID", "ordinal_position": 1},
    {"table_name": "orders", "column_name": "pay_amount", "data_type": "decimal(12,2)", "is_nullable": "NO", "column_key": "", "column_comment": "实付金额", "ordinal_position": 2},
]


def _meta_conn():
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    return engine


def test_table_hash_deterministic_and_sensitive():
    cols = [{"column_name": "a", "data_type": "int", "column_comment": "注释"}]
    h1 = _table_hash(cols)
    h2 = _table_hash([dict(c) for c in cols])
    assert h1 == h2 and len(h1) == 64
    changed = _table_hash([{**cols[0], "column_comment": "新注释"}])
    assert changed != h1


def test_first_collect_inserts():
    engine = _meta_conn()
    with engine.begin() as conn:
        stats = upsert_schema(conn, datasource_id=1, tables=TABLES, columns=COLUMNS)
    assert stats == {"tables": 2, "columns": 4}
    with engine.connect() as conn:
        assert conn.execute(select(func.count()).select_from(table_meta)).scalar() == 2
        assert conn.execute(select(func.count()).select_from(column_meta)).scalar() == 4


def test_reclect_is_idempotent_and_updates():
    engine = _meta_conn()
    with engine.begin() as conn:
        upsert_schema(conn, 1, TABLES, COLUMNS)
    # 列注释变化 → 重采集应走 update 路径且不产生重复行
    columns2 = [dict(c, column_comment="新注释") if c["column_name"] == "username" else c for c in COLUMNS]
    with engine.begin() as conn:
        stats = upsert_schema(conn, 1, TABLES, columns2)
    assert stats == {"tables": 2, "columns": 4}
    with engine.connect() as conn:
        assert conn.execute(select(func.count()).select_from(column_meta)).scalar() == 4
        username_comment = conn.execute(
            select(column_meta.c.column_comment).where(column_meta.c.column_name == "username")
        ).scalar()
        assert username_comment == "新注释"


def test_empty_column_list_tolerated():
    engine = _meta_conn()
    with engine.begin() as conn:
        stats = upsert_schema(conn, 1, [{"table_name": "empty_t", "table_comment": "", "table_rows": 0}], [])
    assert stats == {"tables": 1, "columns": 0}
