"""refresh_tables 增量 diff 单测：sqlite 元库 + mock 业务库读取，验证 hash 比对与定向刷新。"""
import pytest
from sqlalchemy import create_engine, func, select

import app.collector as collector
from app.models import column_meta, metadata, table_meta

TABLES_V1 = [
    {"table_name": "users", "table_comment": "用户表", "table_rows": 8},
    {"table_name": "orders", "table_comment": "订单表", "table_rows": 15},
]
COLUMNS_V1 = [
    {"table_name": "users", "column_name": "id", "data_type": "bigint", "is_nullable": "NO", "column_key": "PRI", "column_comment": "用户ID", "ordinal_position": 1},
    {"table_name": "users", "column_name": "username", "data_type": "varchar(64)", "is_nullable": "NO", "column_key": "", "column_comment": "用户名", "ordinal_position": 2},
    {"table_name": "orders", "column_name": "id", "data_type": "bigint", "is_nullable": "NO", "column_key": "PRI", "column_comment": "订单ID", "ordinal_position": 1},
    {"table_name": "orders", "column_name": "pay_amount", "data_type": "decimal(12,2)", "is_nullable": "NO", "column_key": "", "column_comment": "实付金额", "ordinal_position": 2},
]
# orders.pay_amount 注释变化 → 只有 orders 进变更集
COLUMNS_V2 = [
    dict(c, column_comment="实付金额(元)") if c["column_name"] == "pay_amount" else c for c in COLUMNS_V1
]


@pytest.fixture()
def env(monkeypatch):
    """sqlite 元库 + mock read_schema，隔离 MySQL；index_cards 换成计数桩。"""
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    monkeypatch.setattr("app.collector.read_schema", lambda _engine: (TABLES_V1, COLUMNS_V1))
    monkeypatch.setattr("app.db.get_business_engine", lambda: None)
    monkeypatch.setattr("app.db.get_meta_engine", lambda: engine)
    calls = {"index": []}
    monkeypatch.setattr(
        collector, "index_cards",
        lambda ds, only_tables=None: calls["index"].append(set(only_tables or [])) or {"indexed": len(only_tables or [])},
    )
    return engine, calls


def test_first_refresh_full_change(env):
    engine, calls = env
    stats = collector.refresh_tables(1, None)
    assert stats["changed"] == 2
    assert sorted(stats["changed_tables"]) == ["orders", "users"]
    assert calls["index"] == [{"orders", "users"}]
    with engine.connect() as conn:
        assert conn.execute(select(func.count()).select_from(column_meta)).scalar() == 4


def test_no_change_no_downstream(env):
    _, calls = env
    collector.refresh_tables(1, None)
    calls["index"].clear()
    stats = collector.refresh_tables(1, None)
    assert stats["changed"] == 0
    assert calls["index"] == []  # 无变化零下游动作
    assert stats["index"] == {"skipped": "无变更"}


def test_only_changed_table_reindexed(env, monkeypatch):
    _, calls = env
    collector.refresh_tables(1, None)
    calls["index"].clear()
    monkeypatch.setattr(collector, "read_schema", lambda _e: (TABLES_V1, COLUMNS_V2))  # 注释变更
    stats = collector.refresh_tables(1, None)
    assert stats["changed"] == 1
    assert stats["changed_tables"] == ["orders"]
    assert calls["index"] == [{"orders"}]  # 只重嵌入变更表卡片


def test_targeted_refresh_filters_tables(env):
    _, _ = env
    collector.refresh_tables(1, None)
    stats = collector.refresh_tables(1, ["users"])  # 未变更 → 仍零动作
    assert stats["scanned"] == 1
    assert stats["changed"] == 0
    # 不存在的表被忽略不报错
    stats2 = collector.refresh_tables(1, ["ghost"])
    assert stats2["scanned"] == 0 and stats2["changed"] == 0
