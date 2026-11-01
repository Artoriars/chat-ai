"""Schema 采集：业务库 information_schema → 元数据库 table_meta / column_meta。

M1 只做手动触发（POST /api/schema/collect）；定时调度与增量 diff 是 M3 范围。
"""
import hashlib
import logging
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from app.models import column_meta, table_meta

logger = logging.getLogger(__name__)

# 只采基表（排除视图），限定当前库
_TABLES_SQL = text(
    "SELECT TABLE_NAME AS table_name, TABLE_COMMENT AS table_comment, TABLE_ROWS AS table_rows "
    "FROM information_schema.TABLES "
    "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_TYPE = 'BASE TABLE' "
    "ORDER BY TABLE_NAME"
)
_COLUMNS_SQL = text(
    "SELECT TABLE_NAME AS table_name, COLUMN_NAME AS column_name, COLUMN_TYPE AS data_type, "
    "IS_NULLABLE AS is_nullable, COLUMN_KEY AS column_key, COLUMN_COMMENT AS column_comment, "
    "ORDINAL_POSITION AS ordinal_position "
    "FROM information_schema.COLUMNS "
    "WHERE TABLE_SCHEMA = DATABASE() "
    "ORDER BY TABLE_NAME, ORDINAL_POSITION"
)


def _table_hash(columns: list[dict[str, Any]]) -> str:
    """单表结构指纹：列名+类型+注释 参与 hash，任一变化即触发 M3 的增量级联。"""
    payload = "|".join(
        f"{c['column_name']}:{c['data_type']}:{c['column_comment']}" for c in columns
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def read_schema(business_engine: Engine) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """读取业务库当前库的全部基表与列定义。"""
    with business_engine.connect() as conn:
        tables = [dict(r) for r in conn.execute(_TABLES_SQL).mappings()]
        columns = [dict(r) for r in conn.execute(_COLUMNS_SQL).mappings()]
    return tables, columns


def upsert_schema(
    meta_conn: Connection,
    datasource_id: int,
    tables: list[dict[str, Any]],
    columns: list[dict[str, Any]],
) -> dict[str, int]:
    """采集结果写元数据库：表级 upsert，列级全删重插（幂等，原型阶段取简单正确解）。

    单连接事务内完成；调用方负责事务边界（begin()）。
    """
    columns_by_table: dict[str, list[dict[str, Any]]] = {}
    for col in columns:
        columns_by_table.setdefault(col["table_name"], []).append(col)

    stats = {"tables": 0, "columns": 0}
    for tbl in tables:
        tname = tbl["table_name"]
        tcols = columns_by_table.get(tname, [])

        sel = meta_conn.execute(
            table_meta.select().where(
                table_meta.c.datasource_id == datasource_id,
                table_meta.c.table_name == tname,
            )
        ).first()
        if sel is None:
            result = meta_conn.execute(
                table_meta.insert().values(
                    datasource_id=datasource_id,
                    table_name=tname,
                    table_comment=tbl["table_comment"] or "",
                    table_rows=tbl["table_rows"] or 0,
                    schema_hash=_table_hash(tcols),
                )
            )
            table_meta_id = result.lastrowid
        else:
            table_meta_id = sel.id
            meta_conn.execute(
                table_meta.update()
                .where(table_meta.c.id == table_meta_id)
                .values(
                    table_comment=tbl["table_comment"] or "",
                    table_rows=tbl["table_rows"] or 0,
                    schema_hash=_table_hash(tcols),
                )
            )

        meta_conn.execute(column_meta.delete().where(column_meta.c.table_meta_id == table_meta_id))
        if tcols:
            meta_conn.execute(
                column_meta.insert(),
                [
                    {
                        "table_meta_id": table_meta_id,
                        "column_name": c["column_name"],
                        "data_type": c["data_type"],
                        "is_nullable": c["is_nullable"],
                        "column_key": c["column_key"] or "",
                        "column_comment": c["column_comment"] or "",
                        "ordinal_position": c["ordinal_position"],
                    }
                    for c in tcols
                ],
            )
        stats["tables"] += 1
        stats["columns"] += len(tcols)

    return stats


def collect(datasource_id: int = 1) -> dict[str, Any]:
    """完整采集入口 = 全量 diff（M3 起与 refresh_tables 同一实现）。"""
    stats = refresh_tables(datasource_id, None)
    stats["schema_changed"] = stats["changed"] > 0
    logger.info("schema collected: %s", stats)
    return stats


def refresh_tables(datasource_id: int, table_names: list[str] | None = None) -> dict[str, Any]:
    """增量 diff：只对 schema_hash 变化的表（含新增）更新元库并重嵌入表卡片。

    - table_names=None 全量 diff（定时调度）；传列表 = 定向刷新（手动单表 / query-time 兜底）。
    - 无变化零下游动作（不调 embedding API、不写库）。
    - 业务库已删除的表原样保留，仅 log warning（原型阶段不归档）。
    """
    from sqlalchemy import select

    from app.models import table_meta
    from app.db import get_business_engine, get_meta_engine

    tables, columns = read_schema(get_business_engine())
    if table_names is not None:
        wanted = set(table_names)
        tables = [t for t in tables if t["table_name"] in wanted]
        missing = wanted - {t["table_name"] for t in tables}
        if missing:
            logger.warning("refresh: 业务库中不存在待刷新表 %s（已忽略）", sorted(missing))

    columns_by_table: dict[str, list[dict[str, Any]]] = {}
    for col in columns:
        columns_by_table.setdefault(col["table_name"], []).append(col)

    # 现存 hash → 找出变更表（新增表的 hash 查不到，自然落入变更集）
    with get_meta_engine().connect() as conn:
        rows = conn.execute(
            select(table_meta.c.table_name, table_meta.c.schema_hash).where(
                table_meta.c.datasource_id == datasource_id
            )
        ).all()
    known_hash = {r.table_name: r.schema_hash for r in rows}

    changed_tables: list[dict[str, Any]] = []
    for tbl in tables:
        tname = tbl["table_name"]
        new_hash = _table_hash(columns_by_table.get(tname, []))
        if known_hash.get(tname) == new_hash:
            continue
        tbl["_columns"] = columns_by_table.get(tname, [])
        changed_tables.append(tbl)

    stats: dict[str, Any] = {
        "scanned": len(tables),
        "changed": 0,
        "changed_tables": [],
        "index": {"skipped": "无变更"},
    }
    if not changed_tables:
        return stats

    # 仅变更表走 upsert（列级全删重插）+ 定向重嵌入
    with get_meta_engine().begin() as meta_conn:
        upsert_schema(meta_conn, datasource_id, changed_tables,
                      [dict(c) for t in changed_tables for c in t["_columns"]])
    stats["changed"] = len(changed_tables)
    stats["changed_tables"] = [t["table_name"] for t in changed_tables]
    stats["index"] = index_cards(datasource_id, only_tables={t["table_name"] for t in changed_tables})
    return stats


def index_cards(datasource_id: int = 1, only_tables: set[str] | None = None) -> dict[str, Any]:
    """表卡片向量索引：默认全量重建；only_tables 定向重建（增量 diff 用）。embedding 未配置则跳过。"""
    from app.db import get_meta_engine
    from app.embedder import get_embedder
    from app.pgstore import upsert_cards
    from app.prompt import build_schema_prompt

    embedder = get_embedder()
    if not embedder.enabled:
        return {"skipped": "embedding 未配置"}

    ddl = build_schema_prompt(get_meta_engine(), datasource_id, only_tables=only_tables)
    chunks = [c.strip() for c in ddl.split("\n\n") if c.strip()]
    if only_tables is not None:
        table_names = sorted(only_tables)  # build_schema_prompt 按表名排序输出
    else:
        # build_schema_prompt 按 table_name 排序输出，块顺序与元库表名顺序一致
        from sqlalchemy import text as _text

        with get_meta_engine().connect() as conn:
            table_names = [
                r[0]
                for r in conn.execute(
                    _text("SELECT table_name FROM table_meta WHERE datasource_id = :d ORDER BY table_name"),
                    {"d": datasource_id},
                ).all()
            ]
    if len(table_names) != len(chunks):
        return {"skipped": f"卡片数与表数不一致（{len(chunks)} vs {len(table_names)}），跳过索引"}

    vectors = embedder.embed_documents(chunks)
    rows = [
        {
            "datasource_id": datasource_id,
            "table_name": t,
            "card_text": c,
            "embedding": v,
        }
        for t, c, v in zip(table_names, chunks, vectors)
    ]
    upsert_cards(rows, dim=len(vectors[0]))
    return {"indexed_tables": len(rows), "dim": len(vectors[0])}
