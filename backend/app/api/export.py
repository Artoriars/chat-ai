"""导出接口（M3）：入队 / 状态查询 / 文件下载。

入队的 SQL 来自会话 checkpoint 的成功轮次（不由客户端直接传 SQL，防越权注入任意语句）。
"""
import logging
import re
import uuid
from pathlib import Path

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app import exporter
from app.graph.build import get_chat_graph

logger = logging.getLogger(__name__)
router = APIRouter()

_JOB_ID_RE = re.compile(r"^[0-9a-f]{32}$")


class ExportRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=64, description="来源会话 ID（取其最近成功 SQL）")


def _last_sql(session_id: str) -> str:
    """从 checkpointer 读会话状态的成功 SQL；无则 404。"""
    graph = get_chat_graph()
    state = graph.get_state({"configurable": {"thread_id": session_id}}).values
    sql = (state or {}).get("sql")
    if not sql or state.get("error_code"):
        raise HTTPException(status_code=404, detail="该会话没有可导出的成功查询")
    return sql


@router.post("/export")
def create_export(req: ExportRequest, x_user_id: int = Header(default=1)) -> dict:
    sql = _last_sql(req.session_id)
    try:
        job_id = exporter.enqueue_export(sql, x_user_id)
    except Exception as e:
        logger.exception("export enqueue failed")
        raise HTTPException(status_code=502, detail=f"导出队列不可用: {e}") from e
    return {"ok": True, "job_id": job_id, "state": "queued"}


@router.get("/export/{job_id}")
def export_status(job_id: str) -> dict:
    if not _JOB_ID_RE.fullmatch(job_id):
        raise HTTPException(status_code=400, detail="非法 job_id")
    try:
        status = exporter.get_status(job_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"状态查询失败: {e}") from e
    if status is None:
        raise HTTPException(status_code=404, detail="任务不存在或已过期")
    return {"ok": True, "job_id": job_id, **status}


@router.get("/export/{job_id}/download")
def export_download(job_id: str) -> FileResponse:
    if not _JOB_ID_RE.fullmatch(job_id):
        raise HTTPException(status_code=400, detail="非法 job_id")
    try:
        status = exporter.get_status(job_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"状态查询失败: {e}") from e
    if status is None:
        raise HTTPException(status_code=404, detail="任务不存在或已过期")
    if status.get("state") != "done":
        raise HTTPException(status_code=409, detail=f"任务未完成（{status.get('state')}）")
    path = exporter.export_dir() / f"{job_id}.xlsx"  # job_id 已过正则校验，无路径穿越风险
    if not path.exists():
        raise HTTPException(status_code=404, detail="导出文件已清理")
    return FileResponse(path, filename=f"chatbi-export-{job_id[:8]}.xlsx")
