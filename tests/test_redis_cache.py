from report_writing.infra.redis_cache import (
    cache_get,
    cache_set,
    intent_cache_key,
    redis_enabled,
    session_cache_key,
    text_hash,
)


def test_redis_graceful_without_url(monkeypatch):
    monkeypatch.delenv("REPORT_WRITING_REDIS_URL", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)
    assert redis_enabled() is False
    assert cache_get(session_cache_key("cs1")) is None
    cache_set(session_cache_key("cs1"), "x", ttl_seconds=10)
    assert cache_get(session_cache_key("cs1")) is None


def test_cache_key_helpers():
    assert intent_cache_key("cs1").startswith("rw:")
    assert len(text_hash("hello")) == 24
