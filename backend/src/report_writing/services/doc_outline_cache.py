"""文档窗目录摘要缓存（已废弃读路径）。

GET /doc-outline 现一律直读 TaskStore/MySQL；此处仅保留 invalidate 供写后清旧键，get/set 勿再接入新逻辑。
"""

from __future__ import annotations

import time
from typing import Any

_MEM: dict[str, tuple[float, dict[str, Any]]] = {}
_MEM_TTL_S = 45.0
_REDIS_TTL_S = 120


def _redis_key(task_id: str) -> str:
    return f"rw:task:doc-outline:{task_id}"


def get_doc_outline_cached(task_id: str) -> dict[str, Any] | None:
    tid = (task_id or "").strip()
    if not tid:
        return None
    now = time.monotonic()
    hit = _MEM.get(tid)
    if hit and hit[0] > now:
        return dict(hit[1])
    if hit:
        _MEM.pop(tid, None)
    try:
        from report_writing.infra.redis_cache import cache_get_json

        data = cache_get_json(_redis_key(tid))
    except Exception:
        data = None
    if isinstance(data, dict) and data.get("id"):
        _MEM[tid] = (now + _MEM_TTL_S, data)
        return dict(data)
    return None


def set_doc_outline_cached(task_id: str, payload: dict[str, Any]) -> None:
    tid = (task_id or "").strip()
    if not tid or not isinstance(payload, dict):
        return
    data = dict(payload)
    _MEM[tid] = (time.monotonic() + _MEM_TTL_S, data)
    try:
        from report_writing.infra.redis_cache import cache_set_json

        cache_set_json(_redis_key(tid), data, ttl_seconds=_REDIS_TTL_S)
    except Exception:
        pass


def invalidate_doc_outline_cache(task_id: str) -> None:
    tid = (task_id or "").strip()
    if not tid:
        return
    _MEM.pop(tid, None)
    try:
        from report_writing.infra.redis_cache import cache_delete

        cache_delete(_redis_key(tid))
    except Exception:
        pass
