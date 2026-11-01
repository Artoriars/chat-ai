"""Schema 采集接口：全量采集（M1）+ 增量定向刷新（M3）。"""
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.exc import OperationalError

from app.collector import collect as do_collect
from app.collector import refresh_tables

logger = logging.getLogger(__name__)
router = APIRouter()


class RefreshRequest(BaseModel):
    tables: list[str] | None = Field(
        default=None, max_length=100, description="定向刷新的表名列表；不传 = 全量 diff"
    )


@router.post("/schema/collect")
def collect_schema() -> dict:
    try:
        stats = do_collect()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except OperationalError as e:
        logger.exception("collect failed: db connection error")
        raise HTTPException(status_code=502, detail=f"数据库连接失败: {e}") from e
    except Exception as e:
        logger.exception("collect failed: unexpected error")
        raise HTTPException(status_code=500, detail=f"采集失败: {e}") from e
    return {"ok": True, **stats}


@router.post("/schema/refresh")
def refresh_schema(req: RefreshRequest) -> dict:
    """增量 diff：只对 schema_hash 变化的表更新元库与向量卡片；无变化零下游动作。"""
    try:
        stats = refresh_tables(1, req.tables)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except OperationalError as e:
        logger.exception("refresh failed: db connection error")
        raise HTTPException(status_code=502, detail=f"数据库连接失败: {e}") from e
    except Exception as e:
        logger.exception("refresh failed: unexpected error")
        raise HTTPException(status_code=500, detail=f"刷新失败: {e}") from e
    return {"ok": True, **stats}
