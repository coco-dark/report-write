"""主表元数据 + 子表 document JSON 的建表与迁移。"""

from __future__ import annotations

import json
from typing import Any, Callable


def table_columns(connection, table: str) -> set[str]:
    rows = connection.execute(
        """
        SELECT COLUMN_NAME FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s
        """,
        (table,),
    ).fetchall()
    return {str(row[0]) for row in rows or []}


def upsert_document_child(
    connection,
    child_table: str,
    fk_column: str,
    parent_id: str,
    document: dict[str, Any],
) -> None:
    payload = json.dumps(document, ensure_ascii=False)
    connection.execute(
        f"""
        INSERT INTO {child_table} ({fk_column}, document)
        VALUES (%s, CAST(%s AS JSON)) AS incoming
        ON DUPLICATE KEY UPDATE document = incoming.document
        """,
        (parent_id, payload),
    )


def migrate_legacy_document_column(
    connection,
    parent_table: str,
    child_table: str,
    fk_column: str,
    *,
    on_row: Callable[[str, dict, str], None],
) -> None:
    """将父表 document 列迁入子表，并由 on_row 写父表元数据。"""
    cols = table_columns(connection, parent_table)
    if "document" not in cols:
        return
    try:
        connection.execute(f"ALTER TABLE {parent_table} MODIFY document json NULL")
    except Exception:
        pass
    rows = connection.execute(
        f"SELECT id, document, updated_at FROM {parent_table} WHERE document IS NOT NULL",
    ).fetchall()
    for row in rows or []:
        parent_id = str(row[0])
        updated_at = str(row[2] or "")
        doc = _parse_json(row[1])
        if not doc:
            continue
        upsert_document_child(connection, child_table, fk_column, parent_id, doc)
        on_row(parent_id, doc, updated_at)
    drop_legacy_document_column(connection, parent_table)


def drop_legacy_document_column(connection, parent_table: str) -> None:
    """父表 document 列与元数据+子表 JSON 新结构冲突，迁移后删除或放宽。"""
    cols = table_columns(connection, parent_table)
    if "document" not in cols:
        return
    try:
        connection.execute(f"ALTER TABLE {parent_table} DROP COLUMN document")
        return
    except Exception:
        pass
    try:
        connection.execute(f"ALTER TABLE {parent_table} MODIFY document json NULL")
    except Exception:
        pass


def _parse_json(payload) -> dict[str, Any] | None:
    if payload is None:
        return None
    if isinstance(payload, (bytes, bytearray)):
        payload = payload.decode("utf-8")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            return None
    if not isinstance(payload, dict):
        return None
    return payload


def fetch_document_child(
    connection,
    child_table: str,
    fk_column: str,
    parent_id: str,
) -> dict[str, Any] | None:
    row = connection.execute(
        f"SELECT document FROM {child_table} WHERE {fk_column} = %s",
        (parent_id,),
    ).fetchone()
    if row is None:
        return None
    return _parse_json(row[0])
