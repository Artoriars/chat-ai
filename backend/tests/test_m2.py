"""M2 单测：join 图推导/扩展、口径别名匹配。"""
from app.join_graph import build_join_edges, expand_seeds
from app.metric import constraint_block, match_metrics

TABLES = {
    "users": {"id", "username", "region"},
    "orders": {"id", "user_id", "product_id", "pay_amount"},
    "products": {"id", "category"},
    "misc": {"foo"},
}


# ---- join 图 ----
def test_join_edges_from_naming_convention():
    edges = build_join_edges(TABLES)
    assert edges["orders"] == {"users", "products"}
    assert edges["users"] == {"orders"}
    assert "misc" not in edges


def test_expand_seeds_one_hop():
    edges = build_join_edges(TABLES)
    assert expand_seeds({"orders"}, edges, max_hops=1) == {"orders", "users", "products"}


def test_expand_seeds_max_tables_truncates():
    edges = build_join_edges(TABLES)
    assert len(expand_seeds({"orders"}, edges, max_hops=2, max_tables=2)) == 2


def test_expand_seeds_no_edges():
    edges = build_join_edges(TABLES)
    assert expand_seeds({"misc"}, edges) == {"misc"}


# ---- 口径 ----
METRICS = [
    {
        "name": "GMV",
        "aliases": ["销售额", "成交额", "gmv"],
        "definition": "已支付订单的实付金额总和（不含退款）",
        "expression": "SUM(orders.pay_amount)",
        "base_filter": "status = 'PAID'",
        "time_semantics": "按 pay_time（支付时间）计",
        "version": 1,
    }
]


def test_metric_alias_match_case_insensitive():
    assert len(match_metrics(METRICS, "上个月的GMV是多少")) == 1
    assert len(match_metrics(METRICS, "查一下销售额")) == 1
    assert match_metrics(METRICS, "按大区拆分用户数") == []


def test_metric_match_dedup_when_name_and_alias_both_hit():
    assert len(match_metrics(METRICS, "gmv 销售额")) == 1


def test_constraint_block_contains_expression_and_filter():
    block = constraint_block(match_metrics(METRICS, "销售额"))
    assert "SUM(orders.pay_amount)" in block
    assert "status = 'PAID'" in block
    assert "禁止自行改写" in block


def test_constraint_block_empty_when_no_match():
    assert constraint_block([]) == ""
