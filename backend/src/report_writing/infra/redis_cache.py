"""可选 Redis：会话热存与缓存；权威持久化以 MySQL 为准（异步刷库）。"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from typing import Any

_log = logging.getLogger(__name__)

_PREFIX = "rw:"
_client: Any = None
_client_checked = False


def redis_url() -> str:
    from report_writing.infra.config_settings import redis_url as _url

    return _url()


def redis_enabled() -> bool:
    return bool(redis_url())


def get_redis():
    """惰性连接；未配置或连不上时返回 None（调用方走 MySQL/内存逻辑）。"""
    global _client, _client_checked
    if _client_checked:
        return _client
    _client_checked = True
    url = redis_url()
    if not url:
        return None
    try:
        import redis

        _client = redis.from_url(url, decode_responses=True, socket_connect_timeout=1.5)
        _client.ping()
        return _client
    except Exception as error:
        _log.warning("Redis 未启用（%s），缓存仅走会话文档", error)
        _client = None
        return None


def _key(name: str) -> str:
    return f"{_PREFIX}{name}"


def text_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:24]


def session_cache_key(session_id: str) -> str:
    return _key(f"chat:session:{session_id}")


def intent_cache_key(session_id: str) -> str:
    return _key(f"chat:intent:{session_id}")


def turn_context_cache_key(session_id: str, message: str, revision: str = "") -> str:
    rev = (revision or "").strip()
    if rev:
        return _key(f"chat:turn:{session_id}:{text_hash(rev)}:{text_hash(message)}")
    return _key(f"chat:turn:{session_id}:{text_hash(message)}")


def cache_get(key: str) -> str | None:
    client = get_redis()
    if client is None:
        return None
    try:
        return client.get(key)
    except Exception as error:
        _log.debug("redis get %s: %s", key, error)
        return None


def cache_set(key: str, value: str, *, ttl_seconds: int = 300) -> None:
    client = get_redis()
    if client is None:
        return
    try:
        client.setex(key, max(1, int(ttl_seconds)), value)
    except Exception as error:
        _log.debug("redis set %s: %s", key, error)


def cache_delete(key: str) -> None:
    client = get_redis()
    if client is None:
        return
    try:
        client.delete(key)
    except Exception as error:
        _log.debug("redis del %s: %s", key, error)


def cache_get_json(key: str) -> Any | None:
    raw = cache_get(key)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def cache_set_json(key: str, data: Any, *, ttl_seconds: int = 300) -> None:
    try:
        payload = json.dumps(data, ensure_ascii=False)
    except (TypeError, ValueError):
        return
    cache_set(key, payload, ttl_seconds=ttl_seconds)


def invalidate_session_cache(session_id: str) -> None:
    sid = (session_id or "").strip()
    if not sid:
        return
    cache_delete(session_cache_key(sid))
    cache_delete(intent_cache_key(sid))
