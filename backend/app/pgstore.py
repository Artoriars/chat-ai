"""pgvector 表卡片存储（M2）：建表按首个向量的实际维度自动定维；维度变更自动重建。

向量以字符串字面量 '[a,b,c]' + ::vector 传参，避免引入 numpy 依赖。
"""
import logging
from functools import lru_cache
from typing import Any

import psycopg
from psycopg_pool import ConnectionPool

from app.config import get_settings

logger = logging.getLogger(__name__)

_DDL = """
CREATE TABLE IF NOT EXISTS table_card (
  id            BIGSERIAL PRIMARY KEY,
  datasource_id INT  NOT NULL,
  table_name    TEXT NOT NULL,
  card_text     TEXT NOT NULL,
  embedding     vector({dim}),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (datasource_id, table_name)
);
-- 不建 HNSW 索引：pgvector 索引上限 2000 维，而主流 embedding 模型常见 2048~3072 维；
-- 表卡片量级（≤ 数千行）顺序扫描足够，真到亿级再换支持高维的方案
"""

# 自记维度元数据，避免依赖 pgvector typmod 内部约定
_META_DIM_DDL = "CREATE TABLE IF NOT EXISTS vector_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);"


@lru_cache
def get_pg_pool() -> ConnectionPool:
    dsn = get_settings().chatbi_pg_dsn
    if not dsn:
        raise ValueError("CHATBI_PG_DSN 未配置")
    # autocommit 必需：PostgresSaver.setup() 的 CREATE INDEX CONCURRENTLY 不允许在事务块内执行
    pool = ConnectionPool(dsn, min_size=1, max_size=4, open=True, kwargs={"autocommit": True})
    with pool.connection() as conn:
        conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        conn.execute(_META_DIM_DDL)
    return pool


def _vec_literal(vec: list[float]) -> str:
    return "[" + ",".join(f"{x:.8g}" for x in vec) + "]"


def _ensure_card_table(pool: ConnectionPool, dim_hint: int | None = None) -> None:
    """表不存在 → 按提示维度建表；已存在但维度不符 → 重建（需重新跑索引）。"""
    with pool.connection() as conn:
        row = conn.execute("SELECT value FROM vector_meta WHERE key = 'table_card_dim'").fetchone()
        current_dim = int(row[0]) if row else None
        if current_dim is None:
            conn.execute(_DDL.format(dim=dim_hint or 1024))
            conn.execute(
                "INSERT INTO vector_meta (key, value) VALUES ('table_card_dim', %s) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
                (str(dim_hint or 1024),),
            )
        elif dim_hint and current_dim != dim_hint:
            logger.warning("table_card 维度变更 %s -> %s，重建表（需重新索引）", current_dim, dim_hint)
            conn.execute("DROP TABLE IF EXISTS table_card")
            conn.execute(_DDL.format(dim=dim_hint))
            conn.execute("UPDATE vector_meta SET value = %s WHERE key = 'table_card_dim'", (str(dim_hint),))


def upsert_cards(rows: list[dict[str, Any]], dim: int) -> None:
    """批量写入/更新表卡片（整批一个事务）。rows: {datasource_id, table_name, card_text, embedding}"""
    pool = get_pg_pool()
    _ensure_card_table(pool, dim_hint=dim)
    with pool.connection() as conn:
        with conn.transaction():
            for r in rows:
                conn.execute(
                    """
                    INSERT INTO table_card (datasource_id, table_name, card_text, embedding)
                    VALUES (%s, %s, %s, %s::vector)
                    ON CONFLICT (datasource_id, table_name)
                    DO UPDATE SET card_text = EXCLUDED.card_text,
                                  embedding = EXCLUDED.embedding,
                                  updated_at = now()
                    """,
                    (r["datasource_id"], r["table_name"], r["card_text"], _vec_literal(r["embedding"])),
                )


def search_cards(
    datasource_id: int,
    query_embedding: list[float],
    k: int,
    allowed_tables: set[str] | None = None,
) -> list[str]:
    """余弦相似度 top-k，返回表名（按相似度降序）。allowed_tables=None 表示不限。"""
    pool = get_pg_pool()
    _ensure_card_table(pool, dim_hint=len(query_embedding))
    sql = """
        SELECT table_name FROM table_card
        WHERE datasource_id = %s
          AND embedding IS NOT NULL
          {allowed}
        ORDER BY embedding <=> %s::vector
        LIMIT %s
    """
    params: list[Any] = [datasource_id, _vec_literal(query_embedding), k]
    if allowed_tables is not None:
        if not allowed_tables:
            return []
        sql = sql.format(allowed="AND table_name = ANY(%s)")
        params.insert(2, list(allowed_tables))
    else:
        sql = sql.format(allowed="")
    with pool.connection() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [r[0] for r in rows]
