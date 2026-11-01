"""Excel 异步导出（M3）：Redis 队列 + 进程内后台线程 worker。

流程：POST /api/export 入队 → worker BRPOP → 与在线链路同源的安全检查
（权限 + sqlglot + EXPLAIN 守卫）→ 重新执行 → openpyxl 写文件 → 状态写 Redis（TTL 1h）。
队列与状态都在 Redis，API 进程重启不丢任务；Redis 不可用时 worker 退避重试，不影响在线链路。
"""
import json
import logging
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from app.config import get_settings

logger = logging.getLogger(__name__)

QUEUE_KEY = "chatbi:export:jobs"
STATUS_KEY_PREFIX = "chatbi:export:status:"
STATUS_TTL_SECONDS = 3600


def _redis_client():
    import redis  # 延迟导入：测试环境无 Redis 也能加载模块

    return redis.Redis.from_url(get_settings().redis_url, decode_responses=True, socket_timeout=5)


def export_dir() -> Path:
    s = get_settings()
    d = Path(s.export_dir) if s.export_dir else Path(__file__).resolve().parents[1] / "exports"
    d.mkdir(parents=True, exist_ok=True)
    return d


def enqueue_export(sql: str, user_id: int) -> str:
    """导出任务入队，返回 job_id。入队前不做安全检查（worker 内与执行同源检查）。"""
    job_id = uuid.uuid4().hex
    payload = json.dumps({"job_id": job_id, "sql": sql, "user_id": user_id, "ts": time.time()})
    _redis_client().rpush(QUEUE_KEY, payload)
    _set_status(job_id, {"state": "queued", "rows": 0, "error": None})
    return job_id


def _set_status(job_id: str, status: dict[str, Any]) -> None:
    try:
        _redis_client().set(STATUS_KEY_PREFIX + job_id, json.dumps(status), ex=STATUS_TTL_SECONDS)
    except Exception as e:
        logger.warning("export status write failed: %s", e)


def get_status(job_id: str) -> dict[str, Any] | None:
    raw = _redis_client().get(STATUS_KEY_PREFIX + job_id)
    return json.loads(raw) if raw else None


def write_xlsx(path: Path, columns: list[str], rows: list[list[Any]], max_rows: int) -> int:
    """结果集写 Excel（表头 + 数据行，超 max_rows 截断），返回实际写入行数。

    NULL 写空字符串而非 None：write_only 模式下全 None 行会被 openpyxl 整行丢弃，导致行错位。
    """
    from openpyxl import Workbook

    wb = Workbook(write_only=True)  # 流式写，大结果集不占内存
    ws = wb.create_sheet("result")
    ws.append(columns)
    written = 0
    for row in rows[:max_rows]:
        ws.append(["" if v is None else str(v) for v in row])
        written += 1
    wb.save(path)
    return written


def _run_job(job: dict[str, Any]) -> None:
    """单个导出任务：同源安全检查（权限 → parse_and_check → EXPLAIN 守卫）→ 执行 → 写文件。"""
    from sqlalchemy import text

    from app.db import get_business_engine, get_meta_engine
    from app.security import SecurityError, load_valid_tables, parse_and_check

    job_id = job["job_id"]
    sql = job["sql"]
    user_id = job.get("user_id", 1)
    s = get_settings()

    # 权限校验并入静态检查：只把授权表喂给 parse_and_check（一次解析两用）
    valid = load_valid_tables(get_meta_engine())
    allowed = allowed_tables(get_meta_engine(), user_id)
    if allowed is not None:
        valid = {t: cols for t, cols in valid.items() if t in allowed}
        if not valid:
            _set_status(job_id, {"state": "failed", "rows": 0, "error": "当前用户未授权任何数据表"})
            return
    try:
        canonical = parse_and_check(sql, valid, s.default_row_limit, s.max_row_limit)
    except SecurityError as e:
        _set_status(job_id, {"state": "failed", "rows": 0, "error": f"安全检查失败({e.code}): {e.message}"})
        return

    try:
        engine = get_business_engine()
        with engine.connect() as conn:
            explain_rows = conn.execute(text("EXPLAIN " + canonical)).mappings().all()
            estimated = max((int(r.get("rows") or 0) for r in explain_rows), default=0)
            if estimated > s.max_explain_rows:
                _set_status(job_id, {"state": "failed", "rows": 0, "error": f"预估扫描行数 {estimated} 超过阈值，已拒绝导出"})
                return
            result = conn.execute(text(canonical))
            columns = list(result.keys())
            rows = [list(row) for row in result]
    except Exception as e:
        logger.warning("export execute failed: %s", e)
        _set_status(job_id, {"state": "failed", "rows": 0, "error": f"SQL 执行失败: {e}"})
        return

    try:
        path = export_dir() / f"{job_id}.xlsx"
        written = write_xlsx(path, columns, rows, s.export_max_rows)
        _set_status(job_id, {
            "state": "done",
            "rows": written,
            "truncated": len(rows) > written,
            "error": None,
        })
    except Exception as e:
        logger.exception("export write failed")
        _set_status(job_id, {"state": "failed", "rows": 0, "error": f"Excel 生成失败: {e}"})


def worker_loop(poll_timeout: int = 5) -> None:
    """后台线程主循环：BRPOP 队列，逐个处理。异常只 log，循环不退出。"""
    logger.info("export worker started (queue=%s)", QUEUE_KEY)
    client = _redis_client()
    while True:
        try:
            item = client.brpop(QUEUE_KEY, timeout=poll_timeout)
            if item is None:
                continue
            _, raw = item
            job = json.loads(raw)
            _set_status(job["job_id"], {"state": "running", "rows": 0, "error": None})
            _run_job(job)
        except Exception as e:
            # Redis 断连/任务损坏都不退出，退避后继续
            logger.warning("export worker error: %s", e)
            time.sleep(poll_timeout)


def start_export_worker() -> threading.Thread:
    t = threading.Thread(target=worker_loop, name="export-worker", daemon=True)
    t.start()
    return t
