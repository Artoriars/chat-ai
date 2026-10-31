"""元数据库（chatbi_meta）表定义 —— SQLAlchemy Core，与 docker/mysql/init/01-meta.sql 保持一致。

主键用 BigInteger().with_variant(Integer, "sqlite")：MySQL 下为 BIGINT，sqlite 测试环境下
能正确获得 rowid 自增（sqlite 仅对 INTEGER PRIMARY KEY 做 rowid 别名）。
"""
from sqlalchemy import (
    CHAR,
    Column,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
)

metadata = MetaData()


def _pk() -> Column:
    from sqlalchemy import BigInteger

    return Column("id", BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)


datasource = Table(
    "datasource",
    metadata,
    _pk(),
    Column("name", String(128), nullable=False, unique=True),
    Column("db_type", String(32), nullable=False, server_default="mysql"),
    Column("host", String(255), nullable=False),
    Column("port", Integer, nullable=False, server_default="3306"),
    Column("database_name", String(128), nullable=False),
    Column("username", String(128), nullable=False),
)

table_meta = Table(
    "table_meta",
    metadata,
    _pk(),
    Column("datasource_id", Integer, nullable=False, server_default="1"),
    Column("table_name", String(255), nullable=False),
    Column("table_comment", String(1024), nullable=False, server_default=""),
    Column("table_rows", Integer, nullable=False, server_default="0"),
    Column("schema_hash", CHAR(64), nullable=False),
    # collected_at 由 DB 侧 DEFAULT CURRENT_TIMESTAMP 维护，模型不声明
    UniqueConstraint("datasource_id", "table_name", name="uk_ds_table"),
)

column_meta = Table(
    "column_meta",
    metadata,
    _pk(),
    Column("table_meta_id", Integer, nullable=False),
    Column("column_name", String(255), nullable=False),
    Column("data_type", String(128), nullable=False),
    Column("is_nullable", String(8), nullable=False, server_default="YES"),
    Column("column_key", String(8), nullable=False, server_default=""),
    Column("column_comment", String(1024), nullable=False, server_default=""),
    Column("ordinal_position", Integer, nullable=False),
    UniqueConstraint("table_meta_id", "column_name", name="uk_table_col"),
)

metric = Table(
    "metric",
    metadata,
    _pk(),
    Column("name", String(128), nullable=False),
    Column("aliases", Text),  # JSON 字符串（M2 使用）
    Column("definition", String(2048), nullable=False),
    Column("expression", String(2048), nullable=False),
    Column("base_filter", String(2048)),
    Column("time_semantics", String(255)),
    Column("owner", String(128)),
    Column("version", Integer, nullable=False, server_default="1"),
    Column("status", String(32), nullable=False, server_default="pending_review"),
    UniqueConstraint("name", "version", name="uk_name_version"),
)
