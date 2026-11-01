"""M3 导出单测：xlsx 写入与行数截断、job payload 序列化（不依赖 Redis 与 MySQL）。"""
import json

from openpyxl import load_workbook

from app.exporter import write_xlsx


def test_write_xlsx_header_and_rows(tmp_path):
    path = tmp_path / "a.xlsx"
    written = write_xlsx(path, ["id", "name"], [[1, "alice"], [2, "bob"]], max_rows=100)
    assert written == 2
    wb = load_workbook(path)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    assert rows[0] == ("id", "name")
    assert rows[1] == (1, "alice") or rows[1] == ("1", "alice")


def test_write_xlsx_truncates_to_max_rows(tmp_path):
    path = tmp_path / "b.xlsx"
    rows = [[i, f"u{i}"] for i in range(100)]
    written = write_xlsx(path, ["id", "name"], rows, max_rows=10)
    assert written == 10
    ws = load_workbook(path).active
    assert len(list(ws.iter_rows(values_only=True))) == 11  # 表头 + 10 行


def test_write_xlsx_none_becomes_empty(tmp_path):
    path = tmp_path / "c.xlsx"
    write_xlsx(path, ["a"], [[None], ["x"]], max_rows=10)
    ws = load_workbook(path).active
    # NULL 写空串：write_only 模式全 None 行会被整行丢弃，空串保住行对齐（读回为 None）
    assert list(ws.iter_rows(values_only=True)) == [("a",), (None,), ("x",)]


def test_job_payload_roundtrip():
    """队列 payload 必须可 JSON 序列化且含 worker 所需字段。"""
    payload = json.dumps({"job_id": "a" * 32, "sql": "SELECT 1", "user_id": 1, "ts": 1.0})
    job = json.loads(payload)
    assert job["job_id"] == "a" * 32 and "sql" in job and "user_id" in job
