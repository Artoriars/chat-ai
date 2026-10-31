"""security.parse_and_check 单测：正常路径 / 边界 / 异常分支。"""
import pytest

from app.security import SecurityError, _ensure_limit, parse_and_check

VALID_TABLES = {
    "users": {"id", "username", "region", "created_at"},
    "orders": {"id", "order_no", "user_id", "product_id", "quantity", "pay_amount", "status", "pay_time", "created_at"},
    "products": {"id", "product_name", "category", "unit_price"},
}
DEFAULT_LIMIT = 50
MAX_LIMIT = 1000


def check(sql: str) -> str:
    return parse_and_check(sql, VALID_TABLES, DEFAULT_LIMIT, MAX_LIMIT)


# ---- 正常路径 ----
def test_simple_select_passes_and_injects_limit():
    out = check("SELECT id, username FROM users")
    assert "LIMIT 50" in out.upper()


def test_alias_join_passes():
    out = check(
        "SELECT o.order_no, u.username FROM orders o JOIN users u ON o.user_id = u.id WHERE o.status = 'PAID'"
    )
    assert out.upper().startswith("SELECT")


def test_existing_valid_limit_kept():
    out = check("SELECT id FROM users LIMIT 10")
    assert "LIMIT 10" in out


def test_qualified_and_unqualified_columns_pass():
    check("SELECT order_no, pay_amount FROM orders WHERE status = 'PAID' ORDER BY pay_amount DESC")


def test_cte_passes():
    check(
        "WITH paid AS (SELECT user_id, pay_amount FROM orders WHERE status = 'PAID') "
        "SELECT u.username, SUM(p.pay_amount) FROM paid p JOIN users u ON p.user_id = u.id GROUP BY u.username"
    )


def test_order_by_select_alias_passes():
    """MySQL 允许 ORDER BY 引用 SELECT 输出别名（真实案例：SUM(o.pay_amount) AS total_amount）。"""
    out = check(
        "SELECT u.id, u.username, SUM(o.pay_amount) AS total_amount "
        "FROM orders o JOIN users u ON o.user_id = u.id "
        "WHERE o.status = 'PAID' GROUP BY u.id, u.username ORDER BY total_amount DESC"
    )
    assert "LIMIT 50" in out.upper()


# ---- 危险操作拒绝 ----
@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM users",
        "UPDATE users SET username = 'x'",
        "INSERT INTO users (username) VALUES ('x')",
        "DROP TABLE users",
        "TRUNCATE TABLE users",
        "SELECT * FROM users INTO OUTFILE '/tmp/x'",
    ],
)
def test_dangerous_statements_rejected(sql):
    with pytest.raises(SecurityError) as e:
        check(sql)
    assert e.value.code in {"NON_SELECT", "DANGEROUS_OP"}


def test_multi_statement_rejected():
    with pytest.raises(SecurityError) as e:
        check("SELECT id FROM users; DROP TABLE users")
    assert e.value.code == "MULTI_STATEMENT"


# ---- 边界与异常分支 ----
def test_unknown_table_rejected():
    with pytest.raises(SecurityError) as e:
        check("SELECT * FROM secret_table")
    assert e.value.code == "UNKNOWN_TABLE"


def test_unknown_column_rejected():
    with pytest.raises(SecurityError) as e:
        check("SELECT password_hash FROM users")
    assert e.value.code == "UNKNOWN_COLUMN"


def test_unknown_qualifier_rejected():
    with pytest.raises(SecurityError) as e:
        check("SELECT x.id FROM users")
    assert e.value.code == "UNKNOWN_QUALIFIER"


def test_parse_error_rejected():
    with pytest.raises(SecurityError) as e:
        check("SELECT FROM WHERE")
    assert e.value.code == "PARSE_ERROR"


def test_empty_sql_rejected():
    with pytest.raises(SecurityError) as e:
        check("   ")
    assert e.value.code == "EMPTY_SQL"


def test_limit_clamped_to_max():
    out = check("SELECT id FROM users LIMIT 999999")
    assert "LIMIT 1000" in out


def test_ensure_limit_overwrites_existing():
    import sqlglot
    from sqlglot import exp

    stmt = sqlglot.parse_one("SELECT id FROM users LIMIT 999999", dialect="mysql")
    out = _ensure_limit(stmt, DEFAULT_LIMIT, MAX_LIMIT).sql(dialect="mysql")
    assert "LIMIT 1000" in out
    assert isinstance(stmt.args["limit"], exp.Limit)
