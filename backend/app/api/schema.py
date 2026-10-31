"""Schema 采集接口：手动触发业务库 → 元数据库。"""
import logging

from fastapi import APIRouter, HTTPException
from sqlalchemy.exc import OperationalError

from app.collector import collect as do_collect

logger = logging.getLogger(__name__)
router = APIRouter()


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
