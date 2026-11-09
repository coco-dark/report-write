"""旧版 doc-outline 内存/Redis 缓存残留：写路径仍 invalidate 以免部署迁移期读到过期键。"""

from __future__ import annotations


def _redis_key(task_id: str) -> str:
    return f"rw:task:doc-outline:{task_id}"


def invalidate_doc_outline_cache(task_id: str) -> None:
    tid = (task_id or "").strip()
    if not tid:
        return
    try:
        from report_writing.infra.redis_cache import cache_delete

        cache_delete(_redis_key(tid))
    except Exception:
        pass
