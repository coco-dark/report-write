"""列表查询：先按 id/时间排序，再取 JSON，避免 filesort 拖入大 document。"""

from __future__ import annotations

from typing import Any, Callable, TypeVar

T = TypeVar("T")

_DEFAULT_LIMIT = 500


def list_json_documents(
    connection,
    table: str,
    *,
    order_column: str = "updated_at",
    limit: int = _DEFAULT_LIMIT,
    where_sql: str = "",
    where_params: tuple[Any, ...] = (),
    parse: Callable[[Any], T | None],
) -> list[T]:
    cap = max(1, min(int(limit or _DEFAULT_LIMIT), 2000))
    base = f"SELECT id FROM {table}"
    if where_sql.strip():
        base += f" WHERE {where_sql}"
    base += f" ORDER BY {order_column} DESC LIMIT %s"
    params = tuple(where_params) + (cap,)
    id_rows = connection.execute(base, params).fetchall()
    ids = [str(row[0]) for row in id_rows or [] if row and row[0]]
    items: list[T] = []
    for row_id in ids:
        row = connection.execute(
            f"SELECT document FROM {table} WHERE id = %s",
            (row_id,),
        ).fetchone()
        if row is None:
            continue
        try:
            parsed = parse(row[0])
        except Exception:
            continue
        if parsed is not None:
            items.append(parsed)
    return items
