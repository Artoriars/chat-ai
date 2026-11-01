"""M3 自愈闭环单测：可重试错误码路由、兜底刷新触发条件、结果预览截断（不依赖 LLM 与外部服务）。"""
from langgraph.graph import END

from app.graph.build import RETRYABLE_CODES, _route_execute, _route_validate
from app.graph.nodes import _needs_schema_refresh, _preview_rows


def test_retryable_codes_contents():
    assert "UNKNOWN_COLUMN" in RETRYABLE_CODES
    assert "EXECUTE_ERROR" in RETRYABLE_CODES
    assert "ROWS_EXCEEDED" in RETRYABLE_CODES
    # 权限/基础设施类错误不重试
    assert "PERMISSION_DENIED" not in RETRYABLE_CODES
    assert "LLM_ERROR" not in RETRYABLE_CODES
    assert "NO_SCHEMA" not in RETRYABLE_CODES


def test_route_validate_success_and_retry(monkeypatch):
    assert _route_validate({}) == "execute"
    assert _route_validate({"error_code": "UNKNOWN_COLUMN", "retry_count": 0}) == "repair"
    assert _route_validate({"error_code": "PARSE_ERROR", "retry_count": 1}) == "repair"


def test_route_validate_retry_budget_exhausted(monkeypatch):
    # 默认上限 2：retry_count 达到上限后不再进 repair
    assert _route_validate({"error_code": "UNKNOWN_COLUMN", "retry_count": 2}) is END
    assert _route_execute({"error_code": "EXECUTE_ERROR", "retry_count": 2}) is END


def test_route_non_retryable_direct_end():
    assert _route_validate({"error_code": "PERMISSION_DENIED", "retry_count": 0}) is END
    assert _route_execute({"error_code": "LLM_ERROR", "retry_count": 0}) is END


def test_needs_schema_refresh_conditions():
    assert _needs_schema_refresh("UNKNOWN_TABLE", "任意") is True
    assert _needs_schema_refresh("UNKNOWN_COLUMN", "任意") is True
    assert _needs_schema_refresh("EXECUTE_ERROR", "Table 'x' doesn't exist") is True
    assert _needs_schema_refresh("EXECUTE_ERROR", "不存在") is True
    assert _needs_schema_refresh("EXECUTE_ERROR", "Lock wait timeout") is False
    assert _needs_schema_refresh("ROWS_EXCEEDED", "超阈值") is False
    assert _needs_schema_refresh("PARSE_ERROR", "语法") is False


def test_preview_rows_truncation():
    rows = [[f"v{i}", i] for i in range(15)]
    text = _preview_rows(rows)
    assert "其余 5 行省略" in text
    assert len([l for l in text.split("\n")]) == 11  # 10 行 + 1 省略行

    long_cell = "x" * 200
    text2 = _preview_rows([[long_cell]])
    assert "x" * 80 in text2 and "x" * 81 not in text2

    assert _preview_rows([]) == "（查询结果为空）"
    assert "NULL" in _preview_rows([[None]])
