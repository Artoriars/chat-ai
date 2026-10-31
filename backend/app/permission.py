"""数据权限预裁剪（M2）：用户-表级授权，检索与校验共用同一来源（无登录期 X-User-Id 指定）。

grant_all_tables=1 的用户跳过明细授权表；权限配置是唯一事实来源，执行层硬边界是 M3 范围。
"""
from sqlalchemy import text
from sqlalchemy.engine import Engine


def allowed_tables(meta_engine: Engine, user_id: int = 1) -> set[str] | None:
    """返回该用户可见的表名集合；grant_all 返回 None 表示不限。"""
    with meta_engine.connect() as conn:
        row = conn.execute(
            text("SELECT grant_all_tables FROM `user` WHERE id = :uid"), {"uid": user_id}
        ).first()
        if row is None:
            # 未知用户一律拒绝（返回空集比默认放开安全）
            return set()
        if row.grant_all_tables:
            return None
        rows = conn.execute(
            text("SELECT table_name FROM user_table_permission WHERE user_id = :uid"),
            {"uid": user_id},
        ).all()
    return {r.table_name for r in rows}


def filter_tables(tables: set[str], allowed: set[str] | None) -> set[str]:
    if allowed is None:
        return tables
    return tables & allowed
