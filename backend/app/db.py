"""数据库引擎：meta 库 + 业务库。M1 单数据源，URL 来自配置；M2 扩展为按 datasource 表动态创建。"""
from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from app.config import get_settings


@lru_cache
def get_meta_engine() -> Engine:
    settings = get_settings()
    return create_engine(settings.meta_db_url, pool_pre_ping=True, pool_recycle=3600)


@lru_cache
def get_business_engine() -> Engine:
    settings = get_settings()
    return create_engine(settings.business_db_url, pool_pre_ping=True, pool_recycle=3600)
