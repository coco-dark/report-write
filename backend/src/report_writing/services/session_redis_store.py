"""会话 Redis 热存 + 异步刷 MySQL 队列。"""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any, Callable

from report_writing.infra.redis_cache import (
    cache_delete,
    cache_get_json,
    cache_set_json,
    get_redis,
    session_cache_key,
)

_log = logging.getLogger(__name__)

SESSION_HOT_TTL_SECONDS = 86400  # 24h
_FLUSH_QUEUE_KEY = "rw:chat:session:flush"
_flush_thread: threading.Thread | None = None
_flush_lock = threading.Lock()
_flush_handler: Callable[[str, int], None] | None = None


def session_flush_queue_key() -> str:
    return _FLUSH_QUEUE_KEY


def put_session_hot(session_dict: dict[str, Any], *, ttl_seconds: int = SESSION_HOT_TTL_SECONDS) -> bool:
    """写入 Redis 热会话；成功返回 True。

    并发护栏：
    - revision 落后则拒绝覆盖（返回 False）
    - 消息条数不得变少（防止 ui-state 用旧快照冲掉刚落库的「已建好」轮次）
    - 蓝本向导步数不得回退
    """
    sid = str(session_dict.get("id") or "").strip()
    if not sid:
        return False
    if get_redis() is None:
        return False
    payload = dict(session_dict)
    existing = get_session_hot(sid)
    if isinstance(existing, dict):
        old_rev = int(existing.get("revision") or 0)
        new_rev = int(payload.get("revision") or 0)
        if new_rev < old_rev:
            return False
        payload = _merge_hot_session_preserve_progress(existing, payload)
    cache_set_json(session_cache_key(sid), payload, ttl_seconds=ttl_seconds)
    return True


def _merge_hot_session_preserve_progress(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    """合并热存：保留更长的消息列表，向导步只前进不回退。"""
    out = dict(incoming)
    old_msgs = list(existing.get("messages") or [])
    new_msgs = list(incoming.get("messages") or [])
    if len(old_msgs) > len(new_msgs):
        out["messages"] = old_msgs
    elif len(new_msgs) >= len(old_msgs):
        out["messages"] = new_msgs

    old_ff = dict(existing.get("freeform") or {}) if isinstance(existing.get("freeform"), dict) else {}
    new_ff = dict(incoming.get("freeform") or {}) if isinstance(incoming.get("freeform"), dict) else {}
    pending = new_ff.get("awaiting_path_after_no_match")
    abandoned = isinstance(pending, dict) and bool(pending.get("asked"))
    if abandoned:
        cleaned = {**old_ff, **new_ff}
        cleaned.pop("blueprint_wizard", None)
        rt = dict(cleaned.get("runtime") or {}) if isinstance(cleaned.get("runtime"), dict) else {}
        wf = dict(rt.get("workflows") or {})
        wf.pop("blueprint_wizard", None)
        if rt or wf:
            rt["workflows"] = wf
            cleaned["runtime"] = rt
        out["freeform"] = cleaned
        out["blueprint_id"] = str(incoming.get("blueprint_id") or "")
        return out
    old_wiz = dict(old_ff.get("blueprint_wizard") or {}) if isinstance(old_ff.get("blueprint_wizard"), dict) else {}
    new_wiz = dict(new_ff.get("blueprint_wizard") or {}) if isinstance(new_ff.get("blueprint_wizard"), dict) else {}
    old_focus = str(old_ff.get("focus_document_key") or "").strip()
    new_focus = str(new_ff.get("focus_document_key") or "").strip()
    focus_changed = bool(old_focus and new_focus and old_focus != new_focus)
    old_wid = str(old_wiz.get("working_task_id") or old_wiz.get("blueprint_id") or "").strip()
    new_wid = str(new_wiz.get("working_task_id") or new_wiz.get("blueprint_id") or "").strip()
    wizard_switched = bool(old_wid and new_wid and old_wid != new_wid) or bool(
        new_wiz.get("fresh_meta")
    )
    if old_wiz or new_wiz:
        if focus_changed or wizard_switched:
            merged_wiz = dict(new_wiz)
            # 焦点已切到新文档：热存不得把上一份 writing_path / 向导合回来
            new_ff = {**new_ff, "blueprint_wizard": merged_wiz}
        else:
            try:
                from report_writing.services.chat_session import _merge_blueprint_wizard_ui_state

                merged_wiz = _merge_blueprint_wizard_ui_state(old_wiz, new_wiz)
            except Exception:
                merged_wiz = {**old_wiz, **new_wiz}
            new_ff = {**old_ff, **new_ff, "blueprint_wizard": merged_wiz}
        # runtime.workflows.blueprint_wizard 同步
        rt = dict(new_ff.get("runtime") or {}) if isinstance(new_ff.get("runtime"), dict) else {}
        if not rt and isinstance(old_ff.get("runtime"), dict) and not (focus_changed or wizard_switched):
            rt = dict(old_ff.get("runtime") or {})
        wf = dict(rt.get("workflows") or {})
        if merged_wiz:
            wf["blueprint_wizard"] = dict(merged_wiz)
        else:
            wf.pop("blueprint_wizard", None)
        rt["workflows"] = wf
        new_ff["runtime"] = rt
        out["freeform"] = new_ff

    old_bid = str(existing.get("blueprint_id") or "").strip()
    new_bid = str(out.get("blueprint_id") or "").strip()
    if old_bid and not new_bid and not (focus_changed or wizard_switched):
        out["blueprint_id"] = old_bid
    return out


def get_session_hot(session_id: str) -> dict[str, Any] | None:
    sid = (session_id or "").strip()
    if not sid:
        return None
    cached = cache_get_json(session_cache_key(sid))
    return cached if isinstance(cached, dict) else None


def drop_session_hot(session_id: str) -> None:
    sid = (session_id or "").strip()
    if not sid:
        return
    cache_delete(session_cache_key(sid))


def enqueue_session_flush(session_id: str, revision: int) -> bool:
    """将刷库任务入队；Redis 不可用时返回 False。"""
    client = get_redis()
    if client is None:
        return False
    sid = (session_id or "").strip()
    if not sid:
        return False
    payload = json.dumps({"id": sid, "revision": int(revision or 0)}, ensure_ascii=False)
    try:
        client.lpush(_FLUSH_QUEUE_KEY, payload)
        return True
    except Exception as error:
        _log.warning("会话刷库入队失败 %s@%s: %s", sid, revision, error)
        return False


def register_flush_handler(handler: Callable[[str, int], None]) -> None:
    global _flush_handler
    _flush_handler = handler


def ensure_session_flush_worker() -> None:
    """启动守护线程消费刷库队列（幂等）。"""
    global _flush_thread
    with _flush_lock:
        if _flush_thread is not None and _flush_thread.is_alive():
            return
        thread = threading.Thread(target=_flush_loop, name="rw-session-mysql-flush", daemon=True)
        _flush_thread = thread
        thread.start()
        _log.info("会话 MySQL 异步刷库线程已启动")


def _flush_loop() -> None:
    while True:
        handler = _flush_handler
        client = get_redis()
        if client is None or handler is None:
            time.sleep(1.5)
            continue
        try:
            item = client.brpop(_FLUSH_QUEUE_KEY, timeout=2)
        except Exception as error:
            _log.debug("刷库队列 BRPOP 失败: %s", error)
            time.sleep(1.0)
            continue
        if not item:
            continue
        _, raw = item
        try:
            data = json.loads(raw)
            sid = str(data.get("id") or "").strip()
            rev = int(data.get("revision") or 0)
        except Exception:
            continue
        if not sid:
            continue
        try:
            handler(sid, rev)
        except Exception:
            _log.exception("会话刷库失败 %s@%s", sid, rev)
            # 短暂退避后重新入队，避免永久丢
            try:
                time.sleep(0.2)
                enqueue_session_flush(sid, rev)
            except Exception:
                pass
