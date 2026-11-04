"""同会话多文档登记与 task 解析：读写必须绑到明确文档，禁止串写其它任务。"""

from __future__ import annotations

import re
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from report_writing.domain.models import ChatPhase, ChatSession

def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def normalize_document_registry_key(key: str) -> str:
    """bp:w* 是脏键：写作任务只能登记为 task:w*。"""
    k = str(key or "").strip()
    if not k:
        return ""
    if k.startswith("bp:"):
        raw = k[3:].strip()
        if raw:
            from report_writing.services.task_ids import is_writing_task_id

            if is_writing_task_id(raw):
                return f"task:{raw}"
    return k


def normalize_session_document_keys(session: ChatSession) -> bool:
    """把会话登记表/焦点里的 bp:w* 归一成 task:w*。有改动返回 True。"""
    ff = dict(session.freeform or {})
    changed = False
    raw = ff.get("session_documents")
    if isinstance(raw, dict):
        new_docs: dict[str, dict[str, Any]] = {}
        for key, val in raw.items():
            if not isinstance(val, dict):
                continue
            nk = normalize_document_registry_key(str(key or "").strip())
            if not nk:
                continue
            if nk != str(key or "").strip():
                changed = True
            prev = new_docs.get(nk)
            if prev is None:
                new_docs[nk] = dict(val)
            else:
                # 同键合并：较新 updated_at 覆盖
                prev_ts = str(prev.get("updated_at") or "")
                cur_ts = str(val.get("updated_at") or "")
                new_docs[nk] = {**prev, **dict(val)} if cur_ts >= prev_ts else {**dict(val), **prev}
        if changed:
            ff["session_documents"] = new_docs
    focus = str(ff.get("focus_document_key") or "").strip()
    nfocus = normalize_document_registry_key(focus)
    if nfocus != focus:
        if nfocus:
            ff["focus_document_key"] = nfocus
        else:
            ff.pop("focus_document_key", None)
        changed = True
    if changed:
        session.freeform = ff
    return changed


def session_documents_map(session: ChatSession) -> dict[str, dict[str, Any]]:
    raw = (session.freeform or {}).get("session_documents")
    if not isinstance(raw, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for key, val in raw.items():
        k = normalize_document_registry_key(str(key or "").strip())
        if not k or not isinstance(val, dict):
            continue
        if k in out:
            prev = out[k]
            prev_ts = str(prev.get("updated_at") or "")
            cur_ts = str(val.get("updated_at") or "")
            out[k] = {**prev, **dict(val)} if cur_ts >= prev_ts else {**dict(val), **prev}
        else:
            out[k] = dict(val)
    return out


def sanitize_freeform_document_leak(session: ChatSession) -> None:
    """无蓝本未收录时，清掉焦点写作文档上误挂的库蓝本 id（防串台/误显示同步）。

    含「依据样本」支线（writing_path 常被清空以保留上传向导）；不删其它 bp: 登记项。
    """
    ff = dict(session.freeform or {})
    from report_writing.chat.wizard_state import is_freeform_sample_wizard_mode

    sample = is_freeform_sample_wizard_mode(ff.get("blueprint_wizard"))
    if str(ff.get("writing_path") or "").strip() != "freeform" and not sample:
        return
    collected = str(
        ff.get("collected_blueprint_id") or ff.get("freeform_saved_blueprint_id") or ""
    ).strip()
    if collected:
        return
    sess_tid = str(getattr(session, "task_id", "") or "").strip()
    sess_bp = str(getattr(session, "blueprint_id", "") or "").strip()
    # 依据样本：blueprint_id 槽可为 w*；库蓝本残留必须清掉
    if sess_bp and sess_bp != sess_tid:
        from report_writing.services.task_ids import is_writing_task_id

        if not is_writing_task_id(sess_bp):
            session.blueprint_id = ""
        elif sample:
            # 样本向导用 w* 占槽，会话级不要挂库蓝本
            pass
    docs = session_documents_map(session)
    focus = focus_document_key(session)
    changed = False
    for key, meta in list(docs.items()):
        if not isinstance(meta, dict):
            continue
        if not str(key).startswith("task:"):
            continue
        bid = str(meta.get("blueprint_id") or "").strip()
        if not bid:
            continue
        tid = str(meta.get("task_id") or meta.get("writing_task_id") or "").strip()
        if key == focus or (sess_tid and tid == sess_tid) or key == f"task:{sess_tid}":
            docs[key] = {**meta, "blueprint_id": ""}
            changed = True
    if changed:
        ff["session_documents"] = docs
        session.freeform = ff

def doc_registry_key(*, blueprint_id: str = "", task_id: str = "", kind: str = "") -> str:
    from report_writing.services.task_ids import is_writing_task_id

    bid = (blueprint_id or "").strip()
    tid = (task_id or "").strip()
    k = (kind or "").strip()
    # 写作任务 id 禁止套 bp: 前缀（样本向导会把 w* 填进 blueprint_id 槽）
    if bid and is_writing_task_id(bid) and (not tid or tid == bid):
        return f"task:{bid}"
    if tid and is_writing_task_id(tid) and (not bid or is_writing_task_id(bid)):
        return f"task:{tid}"
    if k == "blueprint" or (bid and (not tid or tid == bid)):
        return f"bp:{bid or tid}"
    if bid and tid and tid != bid:
        # 写作 clone：仍挂在蓝本登记项下，额外记 writing_task_id
        return f"bp:{bid}"
    if tid:
        return f"task:{tid}"
    if bid:
        return f"bp:{bid}"
    return ""

def clean_document_display_title(title: str) -> str:
    """去掉写作 clone 后缀，供文档窗展示。"""
    name = (title or "").strip()
    if name.endswith("（对话写作）"):
        name = name[: -len("（对话写作）")].strip() or name
    return name

def _titles_conflict(existing: str, wanted: str) -> bool:
    """是否应另开文档：题目不同即冲突（禁止子串误判为「同一份」而复用旧 task）。"""
    a = clean_document_display_title(existing)
    b = clean_document_display_title(wanted)
    if not a or not b:
        return False
    return a != b


def should_update_title_in_place(
    session: ChatSession,
    existing: str,
    wanted: str,
    *,
    message: str = "",
) -> bool:
    """定名/微调阶段：标题变化仍属同一份文档，勿另开 w*。"""
    if not _titles_conflict(existing, wanted):
        return True
    msg = (message or "").strip()
    if msg and title_is_write_subject_not_doc_name(msg, wanted):
        if focus_has_active_writing_context(session):
            return False
    if focus_awaiting_doc_name_after_blueprint(session):
        if msg and title_is_write_subject_not_doc_name(msg, wanted):
            cur = clean_document_display_title(existing)
            if cur and _titles_conflict(cur, wanted):
                return False
        return True
    if focus_needs_doc_name(session):
        if msg and title_is_write_subject_not_doc_name(msg, wanted):
            cur = clean_document_display_title(existing)
            if cur and _titles_conflict(cur, wanted) and focus_has_active_writing_context(
                session
            ):
                return False
        return True
    ob = focus_onboarding(session)
    if not ob.get("name_confirmed"):
        return True
    # 已定名且已选主路：用户只是在改/细化文档名（非「写一份××」新题材）→ 同一份 w*
    br = str(ob.get("entry_branch") or "").strip()
    msg = (message or "").strip()
    if br in _ENTRY_BRANCH_VALUES and is_writing_document_named(session):
        if not msg or not message_looks_like_new_write_topic(msg):
            return True
    return False


def focus_awaiting_doc_name_after_blueprint(session: ChatSession) -> bool:
    """已选用/绑定蓝本，但用户尚未确认写作文档名（库名≠文档名属正常）。"""
    if is_writing_document_named(session):
        return False
    bid = str(getattr(session, "blueprint_id", "") or "").strip()
    if bid:
        return True
    ff = dict(session.freeform or {})
    wiz = dict(ff.get("blueprint_wizard") or {})
    if str(wiz.get("blueprint_id") or wiz.get("source_blueprint_id") or "").strip():
        return True
    key = focus_document_key(session)
    if key:
        entry = dict(session_documents_map(session).get(key) or {})
        if str(entry.get("source_blueprint_id") or "").strip():
            return True
        if _library_blueprint_id_from_entry(entry) and str(
            entry.get("writing_task_id") or ""
        ).strip():
            return True
    return False


def session_blueprint_already_selected(session: ChatSession) -> bool:
    """当前会话焦点已绑定库蓝本（定名后不应再 match_blueprints）。"""
    if str(getattr(session, "blueprint_id", "") or "").strip():
        return True
    ff = dict(session.freeform or {})
    wiz = dict(ff.get("blueprint_wizard") or {})
    if str(wiz.get("blueprint_id") or "").strip():
        return True
    key = focus_document_key(session)
    if not key:
        return False
    entry = dict(session_documents_map(session).get(key) or {})
    return bool(
        str(entry.get("source_blueprint_id") or _library_blueprint_id_from_entry(entry) or "").strip()
    )


_ACTIVE_WRITING_PHASES = frozenset(
    {
        ChatPhase.CREATE_HANDOFF.value,
        ChatPhase.FREEFORM_ASK.value,
        ChatPhase.FREEFORM_OUTLINE.value,
        ChatPhase.FREEFORM_POINTS.value,
        ChatPhase.READY_TO_WRITE.value,
        ChatPhase.WRITING.value,
        ChatPhase.CONFIRM_EXPORT.value,
    }
)


def focus_has_active_writing_context(session: ChatSession) -> bool:
    """焦点上已有实质写作上下文（宽于 writing_doc_named）。"""
    if onboarding_lane_active(session) and not is_writing_document_named(session):
        if not session_blueprint_already_selected(session):
            wiz = dict((session.freeform or {}).get("blueprint_wizard") or {})
            step = str(wiz.get("step") or "").strip()
            if step not in {"study", "outline_confirm"}:
                return False
    if session_blueprint_already_selected(session):
        return True
    meta = focus_document_meta(session)
    if str(meta.get("writing_task_id") or "").strip():
        return True
    tid = str(meta.get("task_id") or getattr(session, "task_id", "") or "").strip()
    if tid:
        try:
            from report_writing.services.task_ids import is_writing_task_id

            if is_writing_task_id(tid):
                return True
        except Exception:
            pass
    phase = str(getattr(session, "phase", "") or "").strip()
    if phase in _ACTIVE_WRITING_PHASES:
        return True
    key = focus_document_key(session)
    if key and clean_document_display_title(_focus_display_title(session)):
        entry = dict(session_documents_map(session).get(key) or {})
        if str(entry.get("writing_task_id") or entry.get("task_id") or "").strip():
            return True
    return False


def should_mint_new_document_slot(
    session: ChatSession,
    entry_title: str,
    wanted: str,
    *,
    message: str = "",
) -> bool:
    """True → 另开文档槽 / force_new；False → 同一份 w* 上改标题。"""
    if not _titles_conflict(entry_title, wanted):
        return False
    msg = (message or "").strip()
    if should_update_title_in_place(session, entry_title, wanted, message=msg):
        return False
    if msg:
        scope = resolve_document_scope(session, msg, title_hint=wanted)
        if scope.get("force_new") or str(scope.get("action") or "") == "force_new":
            return True
        if scope.get("need_clarify"):
            return False
    return True


def record_new_document_thread_anchor(
    session: ChatSession,
    *,
    message_index: int | None = None,
) -> int:
    """记下「可能要新开文档」的首条 user 消息索引；force_new 时消费为 document_thread_since_index。"""
    msgs = list(session.messages or [])
    idx = message_index if message_index is not None else max(0, len(msgs) - 1)
    ff = dict(session.freeform or {})
    prev = ff.get("new_document_anchor_index")
    if prev is not None:
        try:
            idx = min(int(prev), int(idx))
        except (TypeError, ValueError):
            pass
    ff["new_document_anchor_index"] = max(0, int(idx))
    session.freeform = ff
    return int(ff["new_document_anchor_index"])


def consume_new_document_thread_anchor(session: ChatSession) -> int:
    """force_new 时取锚点索引；无记录则用当前最后一条 user 消息索引。"""
    ff = dict(session.freeform or {})
    anchor = ff.pop("new_document_anchor_index", None)
    session.freeform = ff
    msgs = list(session.messages or [])
    default = max(0, len(msgs) - 1)
    if anchor is None:
        return default
    try:
        return max(0, int(anchor))
    except (TypeError, ValueError):
        return default


def open_document_thread(
    session: ChatSession,
    key: str,
    *,
    since_index: int | None = None,
) -> None:
    """新开/绑定文档后记录线程锚点，供 message document_key 回溯盖戳。"""
    ff = dict(session.freeform or {})
    k = normalize_document_registry_key((key or "").strip())
    if k:
        ff["document_thread_key"] = k
    if since_index is not None:
        ff["document_thread_since_index"] = max(0, int(since_index))
    elif "document_thread_since_index" not in ff:
        ff["document_thread_since_index"] = max(0, len(list(session.messages or [])))
    session.freeform = ff


def stamp_document_thread_messages(
    session: ChatSession,
    key: str | None = None,
    *,
    since_index: int | None = None,
) -> None:
    """从线程锚点起，为 user/assistant 消息统一 document_key。"""
    ff = dict(session.freeform or {})
    k = normalize_document_registry_key(
        (key or ff.get("document_thread_key") or focus_document_key(session) or "").strip()
    )
    if not k:
        return
    idx = since_index
    if idx is None:
        try:
            idx = int(ff.get("document_thread_since_index") or 0)
        except (TypeError, ValueError):
            idx = 0
    idx = max(0, int(idx))
    msgs = list(session.messages or [])
    for i in range(idx, len(msgs)):
        m = msgs[i]
        m.document_key = k
        m.document_id = k
    session.messages = msgs
    open_document_thread(session, k, since_index=idx)


def empty_writing_shell_reusable(home: Path, tid: str) -> bool:
    """写作任务 w* 是否仍为可复用的定名空壳（未上传样板、无目录）。"""
    from report_writing.services.store import TaskStore
    from report_writing.services.task_ids import is_writing_task_id

    if not tid or not is_writing_task_id(tid):
        return False
    try:
        store = TaskStore(home / "tasks" / tid)
        if not store.directory.is_dir():
            return False
        task = store.load()
    except Exception:
        return False
    if getattr(task, "sample_ready", False):
        return False
    chs = list((task.outline.chapters if task.outline else None) or [])
    if chs:
        return False
    return True


def reusable_onboarding_shell(
    session: ChatSession,
    home: Path | None,
    *,
    title_hint: str = "",
) -> str:
    """当前 onboarding / document thread 下可复用的 w* 空壳 task id。"""
    if home is None:
        return ""
    hint = clean_document_display_title(title_hint)
    ff = dict(session.freeform or {})
    thread_key = normalize_document_registry_key(
        str(ff.get("document_thread_key") or "").strip()
    )
    focus_key = normalize_document_registry_key(focus_document_key(session) or "")

    def _tid_from_key(key: str) -> str:
        k = normalize_document_registry_key((key or "").strip())
        if k.startswith("task:"):
            return k.replace("task:", "", 1)
        return ""

    ordered_keys: list[str] = []
    seen: set[str] = set()

    def _push_key(key: str) -> None:
        k = normalize_document_registry_key((key or "").strip())
        if not k or k in seen:
            return
        seen.add(k)
        ordered_keys.append(k)

    named_focus = is_writing_document_named(session)
    tid_sess = str(getattr(session, "task_id", "") or "").strip()
    # 已定名焦点：当前写作副本即使磁盘上仍是空壳，也不得复用（否则新开会绑回上一份 w*）
    if not named_focus:
        _push_key(focus_key)
        _push_key(thread_key)
        if tid_sess:
            _push_key(f"task:{tid_sess}")
    else:
        thread_is_focus = bool(thread_key) and thread_key == focus_key
        if thread_key and not thread_is_focus:
            _push_key(thread_key)

    docs = session_documents_map(session)

    def _shell_still_onboarding(key: str, tid: str) -> bool:
        if not tid or not empty_writing_shell_reusable(home, tid):
            return False
        meta = dict(docs.get(key) or {})
        lane = meta.get("lane") if isinstance(meta.get("lane"), dict) else {}
        ob = meta.get("onboarding") if isinstance(meta.get("onboarding"), dict) else {}
        if not ob and isinstance(lane, dict):
            ob = lane.get("onboarding") if isinstance(lane.get("onboarding"), dict) else {}
        if (ob or {}).get("name_confirmed") or (lane or {}).get("writing_doc_named"):
            return False
        return True

    for key in ordered_keys:
        tid = _tid_from_key(key) or str(
            (docs.get(key) or {}).get("task_id")
            or (docs.get(key) or {}).get("writing_task_id")
            or ""
        ).strip()
        if _shell_still_onboarding(key, tid):
            title = clean_document_display_title(
                str((docs.get(key) or {}).get("title") or "")
            )
            if (
                hint
                and title
                and _titles_conflict(title, hint)
                and not empty_writing_shell_reusable(home, tid)
            ):
                continue
            return tid

    norm_hint = _norm_title(hint) if hint else ""
    best_tid = ""
    best_score = -1
    for key, meta in docs.items():
        if not str(key).startswith("task:"):
            continue
        tid = _tid_from_key(key)
        if named_focus and (key == focus_key or tid == tid_sess):
            continue
        if not _shell_still_onboarding(key, tid):
            continue
        title = clean_document_display_title(str(meta.get("title") or ""))
        score = 0
        if key == focus_key or key == thread_key:
            score += 200
        if norm_hint and title and _norm_title(title) == norm_hint:
            score += 100
        elif (
            norm_hint
            and title
            and _titles_conflict(title, hint)
            and not empty_writing_shell_reusable(home, tid)
        ):
            score = -1
        if score > best_score:
            best_score = score
            best_tid = tid
    return best_tid if best_score >= 0 else ""


def bind_onboarding_shell_to_session(
    session: ChatSession,
    task_id: str,
    *,
    title_hint: str = "",
) -> None:
    """开模版向导时保留 onboarding 空壳与焦点，避免 force_new 二次 mint w*。"""
    tid = (task_id or "").strip()
    if not tid:
        return
    session.task_id = tid
    session.blueprint_id = ""
    reg_key = f"task:{tid}"
    set_focus_document_key(session, reg_key)
    if title_hint:
        ff = dict(session.freeform or {})
        ff["title"] = clean_document_display_title(title_hint)
        session.freeform = ff


def apply_focused_document_title(
    session: ChatSession,
    title: str,
    *,
    home: Path | None = None,
    category: str = "",
) -> str:
    """把对话里定下的文档名写到焦点任务 + 会话登记表，供文档窗立刻展示。

    同会话多文档时只改当前焦点，不碰其它登记项。
    """
    name = clean_document_display_title(title)
    if not name:
        return ""
    ff = dict(session.freeform or {})
    if name:
        ff["title"] = name
    cat = (category or "").strip()
    if cat:
        ff["category"] = cat
    session.freeform = ff

    tid = str(getattr(session, "task_id", "") or "").strip()
    bid = str(getattr(session, "blueprint_id", "") or "").strip()
    focus = focus_document_key(session)
    docs = session_documents_map(session)
    meta = docs.get(focus) if focus else None
    if isinstance(meta, dict):
        tid = tid or str(meta.get("task_id") or meta.get("writing_task_id") or "").strip()
        bid = bid or str(meta.get("blueprint_id") or "").strip()
        wid = str(meta.get("writing_task_id") or "").strip()
        kind = str(meta.get("kind") or "").strip()
    else:
        wid = ""
        kind = ""

    # 无蓝本路径未收录：禁止把会话/焦点残留蓝本挂到当前文档（否则文档串台、误显示「同步」）
    writing_path = str(ff.get("writing_path") or "").strip()
    collected = str(
        ff.get("collected_blueprint_id") or ff.get("freeform_saved_blueprint_id") or ""
    ).strip()
    if writing_path == "freeform" and not collected:
        bid = ""
        if kind == "blueprint":
            kind = ""

    # 文档窗「改名」只改会话文档展示名；落盘仅写写作任务，绝不改蓝本库条目 title
    from report_writing.services.task_ids import is_blueprint_task_id, is_writing_task_id

    persist_tid = ""
    if wid and is_writing_task_id(wid):
        persist_tid = wid
    elif tid and is_writing_task_id(tid):
        persist_tid = tid
    # tid 若是蓝本 id：只更新登记表，不写蓝本 task.json

    if home is not None and persist_tid:
        try:
            from report_writing.chat.helpers import _load_task
            from report_writing.services.store import TaskStore

            task = _load_task(home, persist_tid)
            if task is not None:
                task.title = name
                if cat and not str(getattr(task, "category", "") or "").strip():
                    task.category = cat
                TaskStore(home / "tasks" / persist_tid).save(task)
                try:
                    from report_writing.services.doc_outline_cache import invalidate_doc_outline_cache

                    invalidate_doc_outline_cache(persist_tid)
                except Exception:
                    pass
        except Exception:
            pass

    if bid and (
        kind == "blueprint"
        or is_blueprint_task_id(tid)
        or (tid and tid == bid)
        or (not tid and bid)
    ):
        upsert_session_document(
            session,
            kind="blueprint",
            task_id=bid,
            blueprint_id=bid,
            writing_task_id=wid or (persist_tid if persist_tid != bid else ""),
            title=name,
        )
        set_focus_document_key(session, f"bp:{bid}")
    elif tid or persist_tid:
        use_tid = persist_tid or tid
        k = "writing" if is_writing_task_id(use_tid) else (kind or "outline")
        upsert_session_document(
            session,
            kind=k if k in {"outline", "writing", "task", "blueprint"} else "outline",
            task_id=use_tid,
            blueprint_id=bid,
            writing_task_id=wid if k == "blueprint" else (use_tid if is_writing_task_id(use_tid) else wid),
            title=name,
            clear_blueprint_id=writing_path == "freeform" and not collected,
        )
        set_focus_document_key(session, doc_registry_key(blueprint_id=bid, task_id=use_tid, kind=k))
    return name

def is_writing_document_named(session: ChatSession) -> bool:
    """用户是否已确认「本份写作文档」名称（只认当前焦点车道，禁止沿用上一份）。"""
    key = focus_document_key(session)
    if key:
        entry = dict(session_documents_map(session).get(key) or {})
        raw_ob = entry.get("onboarding") if isinstance(entry.get("onboarding"), dict) else None
        lane = entry.get("lane") if isinstance(entry.get("lane"), dict) else None
        if raw_ob is not None or lane:
            if raw_ob and raw_ob.get("name_confirmed"):
                return True
            if lane and lane.get("writing_doc_named"):
                return True
            return False
    ff = dict(session.freeform or {})
    if bool(ff.get("writing_doc_named")):
        return True
    title = str(ff.get("title") or "").strip()
    if title and bool(ff.get("meta_confirmed")):
        return True
    return False

def clear_writing_document_named(session: ChatSession) -> None:
    """新开文档时清掉「已定名」标记，避免仍沿用上一份标题当当前文档。"""
    ff = dict(session.freeform or {})
    changed = False
    if "writing_doc_named" in ff:
        ff.pop("writing_doc_named", None)
        changed = True
    # is_writing_document_named 也会看 meta_confirmed+title；只清 writing_doc_named 不够
    if ff.get("meta_confirmed"):
        ff.pop("meta_confirmed", None)
        changed = True
    if changed:
        session.freeform = ff


_ENTRY_PATH_VALUES = frozenset({"match", "template", "freeform"})
_ENTRY_BRANCH_VALUES = frozenset({"with_blueprint", "without_blueprint"})


def _normalize_onboarding(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {
            "name_confirmed": False,
            "entry_path": "",
            "entry_branch": "",
            "preferred_entry_path": "",
            "clarify_residual": "",
            "freeform_mode": "",
            "title_working": "",
        }
    path = str(raw.get("entry_path") or "").strip()
    if path not in _ENTRY_PATH_VALUES:
        path = ""
    branch = str(raw.get("entry_branch") or "").strip()
    if branch not in _ENTRY_BRANCH_VALUES:
        branch = ""
    return {
        "name_confirmed": bool(raw.get("name_confirmed")),
        "entry_path": path,
        "entry_branch": branch,
        "preferred_entry_path": str(raw.get("preferred_entry_path") or "").strip(),
        "clarify_residual": str(raw.get("clarify_residual") or "").strip()[:500],
        "freeform_mode": str(raw.get("freeform_mode") or "").strip(),
        "title_working": clean_document_display_title(
            str(raw.get("title_working") or "")
        )[:80],
    }


def _infer_onboarding_from_legacy(session: ChatSession, entry: dict[str, Any]) -> dict[str, Any]:
    """旧会话无 onboarding 字段时从 writing_path / 全局定名推断。"""
    ff = dict(session.freeform or {})
    title = clean_document_display_title(str(entry.get("title") or ff.get("title") or ""))
    path = str(entry.get("writing_path") or "").strip()
    if not path and not _library_blueprint_id_from_entry(entry):
        path = str(ff.get("writing_path") or "").strip()
    name_ok = bool(entry.get("name_confirmed")) or bool(ff.get("writing_doc_named"))
    entry_path = str(entry.get("entry_path") or "").strip()
    if entry_path not in _ENTRY_PATH_VALUES:
        entry_path = ""
    if not entry_path:
        if _library_blueprint_id_from_entry(entry) or str(entry.get("kind") or "") == "blueprint":
            entry_path = "match"
        elif path == "freeform":
            entry_path = "freeform"
        wiz = dict(ff.get("blueprint_wizard") or {})
        if wiz.get("blueprint_id") or path == "blueprint_wizard":
            entry_path = entry_path or "template"
    return {
        "name_confirmed": name_ok,
        "entry_path": entry_path,
        "entry_branch": (
            "with_blueprint"
            if entry_path == "match"
            else ("without_blueprint" if entry_path in {"template", "freeform"} else "")
        ),
        "preferred_entry_path": "",
        "clarify_residual": "",
        "title_working": "" if name_ok else title,
    }


def focus_onboarding(session: ChatSession) -> dict[str, Any]:
    """当前焦点文档的开场引导状态（定名 → 选线路）。"""
    key = focus_document_key(session)
    if not key:
        ff = dict(session.freeform or {})
        pending = ff.get("pending_document_onboarding")
        if isinstance(pending, dict):
            base = _normalize_onboarding(pending)
            if is_writing_document_named(session):
                base["name_confirmed"] = True
            return base
        if is_writing_document_named(session):
            path = str(ff.get("writing_path") or "").strip()
            ep = "freeform" if path == "freeform" else ("match" if path == "blueprint" else "")
            branch = (
                "with_blueprint"
                if ep == "match"
                else ("without_blueprint" if ep in {"template", "freeform"} else "")
            )
            return {
                "name_confirmed": True,
                "entry_path": ep if ep in _ENTRY_PATH_VALUES else "",
                "entry_branch": branch,
                "preferred_entry_path": "",
                "clarify_residual": "",
                "title_working": "",
            }
        return _normalize_onboarding(None)
    entry = dict(session_documents_map(session).get(key) or {})
    raw = entry.get("onboarding")
    if isinstance(raw, dict):
        return _normalize_onboarding(raw)
    return _infer_onboarding_from_legacy(session, entry)


def patch_focus_onboarding(session: ChatSession, **updates: Any) -> dict[str, Any]:
    """更新焦点登记项上的 onboarding（无焦点时只写会话级 pending）。"""
    key = focus_document_key(session)
    merged = {**focus_onboarding(session), **{k: v for k, v in updates.items() if v is not None}}
    if str(merged.get("entry_path") or "") not in _ENTRY_PATH_VALUES:
        merged["entry_path"] = ""
    br = str(merged.get("entry_branch") or "").strip()
    if br not in _ENTRY_BRANCH_VALUES:
        merged["entry_branch"] = ""
    pep = str(merged.get("preferred_entry_path") or "").strip()
    if pep and pep not in _ENTRY_PATH_VALUES:
        merged["preferred_entry_path"] = ""
    if not key:
        ff = dict(session.freeform or {})
        ff["pending_document_onboarding"] = merged
        session.freeform = ff
        return merged
    ff = dict(session.freeform or {})
    docs = session_documents_map(session)
    entry = dict(docs.get(key) or {})
    entry["onboarding"] = merged
    lane = dict(entry.get("lane") or {})
    if lane:
        lane["onboarding"] = merged
        if merged.get("name_confirmed"):
            lane["writing_doc_named"] = True
            lane["meta_confirmed"] = True
        entry["lane"] = lane
    docs[key] = entry
    ff["session_documents"] = docs
    ff.pop("pending_document_onboarding", None)
    session.freeform = ff
    return merged


def empty_document_lane(
    *,
    title: str = "",
    task_id: str = "",
    phase: str = "",
) -> dict[str, Any]:
    """新文档空车道：不继承上一份 writing_path / 大纲确认 / 向导。"""
    return {
        "task_id": (task_id or "").strip(),
        "blueprint_id": "",
        "phase": (phase or "").strip() or ChatPhase.INTENT.value,
        "writing_path": "",
        "onboarding": _normalize_onboarding(None),
        "blueprint_wizard": {},
        "plan": {},
        "writing_doc_named": False,
        "meta_confirmed": False,
        "outline_confirmed": False,
        "outline_text": "",
        "outline_pending_edit": False,
        "title": clean_document_display_title(title),
        "workflows_blueprint_wizard": {},
        "session_goal": {},
        "writing_brief": {},
    }


def _lane_from_entry(entry: dict[str, Any]) -> dict[str, Any]:
    raw = entry.get("lane")
    if isinstance(raw, dict) and raw:
        base = empty_document_lane(
            title=str(entry.get("title") or raw.get("title") or ""),
            task_id=str(entry.get("task_id") or raw.get("task_id") or ""),
        )
        merged = {**base, **raw}
        ob = entry.get("onboarding") or raw.get("onboarding")
        if isinstance(ob, dict):
            merged["onboarding"] = _normalize_onboarding(ob)
        return merged
    snap = entry.get("doc_line_snapshot")
    lane = empty_document_lane(
        title=str(entry.get("title") or ""),
        task_id=str(entry.get("task_id") or entry.get("writing_task_id") or ""),
    )
    if isinstance(snap, dict) and snap:
        lane["task_id"] = str(snap.get("task_id") or lane["task_id"] or "")
        lane["blueprint_id"] = str(snap.get("blueprint_id") or "")
        lane["phase"] = str(snap.get("phase") or lane["phase"] or "")
        lane["writing_path"] = str(snap.get("writing_path") or "")
        lane["blueprint_wizard"] = dict(snap.get("blueprint_wizard") or {})
        lane["plan"] = dict(snap.get("plan") or {})
        lane["workflows_blueprint_wizard"] = dict(
            snap.get("workflows_blueprint_wizard") or {}
        )
    ob = entry.get("onboarding")
    if isinstance(ob, dict):
        lane["onboarding"] = _normalize_onboarding(ob)
        if lane["onboarding"].get("name_confirmed"):
            lane["writing_doc_named"] = True
            lane["meta_confirmed"] = True
    return lane


def document_lane(session: ChatSession, key: str | None = None) -> dict[str, Any]:
    k = normalize_document_registry_key((key or focus_document_key(session) or "").strip())
    if not k:
        return empty_document_lane()
    entry = dict(session_documents_map(session).get(k) or {})
    return _lane_from_entry(entry)


def _norm_lane_id(value: str) -> str:
    raw = str(value or "").strip()
    if raw.startswith("task:"):
        return raw[5:].strip()
    if raw.startswith("bp:"):
        return raw[3:].strip()
    return raw


def lane_identity_ids(key: str, entry: dict[str, Any] | None = None) -> set[str]:
    """本车道允许绑定的 task / blueprint id。"""
    ids: set[str] = set()
    k = normalize_document_registry_key((key or "").strip())
    nid = _norm_lane_id(k)
    if nid:
        ids.add(nid)
    meta = dict(entry or {})
    lane = meta.get("lane") if isinstance(meta.get("lane"), dict) else {}
    for raw in (
        meta.get("task_id"),
        meta.get("writing_task_id"),
        meta.get("blueprint_id"),
        (lane or {}).get("task_id"),
        (lane or {}).get("blueprint_id"),
    ):
        n = _norm_lane_id(str(raw or ""))
        if n:
            ids.add(n)
    return ids


def wizard_belongs_to_lane(
    wizard: dict[str, Any] | None,
    *,
    key: str = "",
    entry: dict[str, Any] | None = None,
    lane_task_id: str = "",
) -> bool:
    """向导是否属于该车道。异文档 selected/study 不得写入。"""
    wiz = dict(wizard or {})
    if not wiz:
        return True
    ids = lane_identity_ids(key, entry)
    tid = _norm_lane_id(lane_task_id)
    if tid:
        ids.add(tid)
    work = _norm_lane_id(str(wiz.get("working_task_id") or wiz.get("task_id") or ""))
    bid = _norm_lane_id(str(wiz.get("blueprint_id") or ""))
    mode = str(wiz.get("mode") or "").strip()
    step = str(wiz.get("step") or "").strip()
    fresh = bool(wiz.get("fresh_meta"))
    if fresh and not bid:
        return (not work) or (work in ids)
    found = {x for x in (work, bid) if x}
    if not found:
        if mode == "selected" or step == "study":
            return False
        return True
    if not ids:
        return True
    # blueprint_id 指向其它文档时，即使 working_task_id 被改成本车道也算异文档
    if bid and bid not in ids:
        return False
    if work and work not in ids and not (bid and bid in ids):
        return False
    if mode == "selected" and not (found & ids):
        return False
    return bool(found & ids)


def wizard_belongs_to_focus(
    session: ChatSession, wizard: dict[str, Any] | None = None
) -> bool:
    wiz = dict(
        wizard
        if wizard is not None
        else (session.freeform or {}).get("blueprint_wizard") or {}
    )
    key = focus_document_key(session) or ""
    entry = dict(session_documents_map(session).get(key) or {}) if key else {}
    return wizard_belongs_to_lane(
        wiz,
        key=key,
        entry=entry,
        lane_task_id=str(getattr(session, "task_id", "") or ""),
    )


def sanitize_wizard_for_focus(
    session: ChatSession, wizard: dict[str, Any] | None
) -> dict[str, Any]:
    """去掉不属于当前焦点的 blueprint_id / 章节分析，保留本文档 meta/create。"""
    wiz = dict(wizard or {})
    if not wiz:
        return {}
    if wizard_belongs_to_focus(session, wiz):
        return wiz
    tid = str(getattr(session, "task_id", "") or "").strip()
    keep: dict[str, Any] = {}
    for key in ("step", "mode", "fresh_meta", "title", "working_task_id"):
        if key in wiz and wiz.get(key) not in (None, ""):
            keep[key] = wiz.get(key)
    if tid:
        keep["working_task_id"] = tid
    bid = _norm_lane_id(str(wiz.get("blueprint_id") or ""))
    if bid and bid == _norm_lane_id(tid):
        keep["blueprint_id"] = wiz.get("blueprint_id")
    if bool(wiz.get("fresh_meta")) or str(keep.get("mode") or "") in {
        "create",
        "freeform_sample",
    }:
        keep["fresh_meta"] = True
        keep.setdefault("mode", "create")
        if str(keep.get("step") or "") in {"", "study"}:
            keep["step"] = "meta"
    return keep


def drop_foreign_blueprint_wizard(session: ChatSession) -> bool:
    """当前向导不属于焦点文档则丢掉。有改动返回 True。"""
    wiz = dict((session.freeform or {}).get("blueprint_wizard") or {})
    if not wiz:
        return False
    step = str(wiz.get("step") or "").strip()
    mode = str(wiz.get("mode") or "").strip()
    if (
        mode != "selected"
        and step in {"upload", "outline", "outline_confirm", "upload_done"}
    ):
        return False
    if wizard_belongs_to_focus(session, wiz):
        return False
    ff = dict(session.freeform or {})
    ff.pop("blueprint_wizard", None)
    session.freeform = ff
    try:
        from report_writing.chat.session_runtime import WORKFLOW_BLUEPRINT, workflow_set

        workflow_set(session, WORKFLOW_BLUEPRINT, {})
    except Exception:
        pass
    bid = _norm_lane_id(str(getattr(session, "blueprint_id", "") or ""))
    tid = _norm_lane_id(str(getattr(session, "task_id", "") or ""))
    if bid and bid != tid:
        session.blueprint_id = ""
    return True


def focus_blocks_stale_blueprint_sync(session: ChatSession) -> bool:
    """未定名新 w* / 已声明模版入口：禁止用残留 blueprint_id 灌 selected 向导。"""
    from report_writing.services.task_ids import is_writing_task_id

    tid = str(getattr(session, "task_id", "") or "").strip()
    unnamed = not is_writing_document_named(session)
    ob = focus_onboarding(session)
    pep = str(ob.get("preferred_entry_path") or "").strip()
    wiz = dict((session.freeform or {}).get("blueprint_wizard") or {})
    writing_shell = bool(tid) and is_writing_task_id(tid)
    foreign = bool(wiz) and not wizard_belongs_to_focus(session, wiz)
    # 未定名新 w* 空壳：禁止用残留库蓝本 id 灌 selected
    if unnamed and writing_shell:
        return True
    # 仅认用户声明的模版入口，不把「有 blueprint_id」推断成 template
    if pep == "template" and (unnamed or bool(wiz.get("fresh_meta")) or not wiz or foreign):
        return True
    if foreign and writing_shell:
        return True
    return False


def project_focus_lane_to_session(
    session: ChatSession, *, home: Path | None = None
) -> None:
    """回合开始：会话单例 = 当前焦点快车道投影（禁止沿用上一份 wizard/path）。"""
    key = focus_document_key(session)
    if not key:
        drop_foreign_blueprint_wizard(session)
        return
    hydrate_lane(session, key)
    drop_foreign_blueprint_wizard(session)
    try:
        sanitize_freeform_document_leak(session)
    except Exception:
        pass
    if home is not None:
        try:
            rebind_session_to_focus_document(session, home)
        except Exception:
            pass


def flush_lane(session: ChatSession, *, key: str | None = None) -> None:
    """把会话单例写回当前（或指定）文档车道。异文档向导一律丢弃。"""
    k = normalize_document_registry_key((key or focus_document_key(session) or "").strip())
    if not k:
        return
    ff = dict(session.freeform or {})
    docs = session_documents_map(session)
    entry = dict(docs.get(k) or {})
    rt = getattr(session, "runtime", None)
    workflows: dict[str, Any] = {}
    if isinstance(rt, dict):
        workflows = dict(rt.get("workflows") or {})
    lane_tid = str(
        entry.get("task_id") or entry.get("writing_task_id") or ""
    ).strip()
    if k.startswith("task:") and not lane_tid:
        lane_tid = k.replace("task:", "", 1)
    sess_tid = str(getattr(session, "task_id", "") or "").strip()
    if not lane_tid and sess_tid and wizard_belongs_to_lane(
        {"working_task_id": sess_tid},
        key=k,
        entry=entry,
        lane_task_id=sess_tid,
    ):
        lane_tid = sess_tid
    wiz = dict(ff.get("blueprint_wizard") or {})
    wf_wiz = dict(workflows.get("blueprint_wizard") or {})
    foreign = not wizard_belongs_to_lane(
        wiz, key=k, entry=entry, lane_task_id=lane_tid or sess_tid
    )
    if foreign:
        wiz = {}
    if not wizard_belongs_to_lane(
        wf_wiz, key=k, entry=entry, lane_task_id=lane_tid or sess_tid
    ):
        wf_wiz = {}
    sess_bid = str(getattr(session, "blueprint_id", "") or "").strip()
    lane_ids = lane_identity_ids(k, entry)
    if lane_tid:
        lane_ids.add(_norm_lane_id(lane_tid))
    if sess_bid and _norm_lane_id(sess_bid) not in lane_ids:
        sess_bid = str(entry.get("blueprint_id") or "").strip()
        if foreign:
            sess_bid = ""
    writing_named = bool(ff.get("writing_doc_named"))
    meta_confirmed = bool(ff.get("meta_confirmed"))
    writing_path = str(ff.get("writing_path") or "").strip()
    onboarding = focus_onboarding(session)
    title = clean_document_display_title(
        str(ff.get("title") or entry.get("title") or "")
    )
    flush_focus = k == normalize_document_registry_key(focus_document_key(session) or "")
    if foreign and not flush_focus:
        # 指定写回其它车道时，若单例向导不属于它，保留该车道原向导，勿用空壳盖掉
        prev = _lane_from_entry(entry)
        wiz = dict(prev.get("blueprint_wizard") or {})
        wf_wiz = dict(prev.get("workflows_blueprint_wizard") or {})
        writing_named = bool(prev.get("writing_doc_named"))
        meta_confirmed = bool(prev.get("meta_confirmed"))
        writing_path = str(prev.get("writing_path") or "")
        onboarding = _normalize_onboarding(prev.get("onboarding") or entry.get("onboarding"))
        title = clean_document_display_title(str(prev.get("title") or entry.get("title") or title))
        sess_bid = str(prev.get("blueprint_id") or sess_bid or "")
        lane_tid = str(prev.get("task_id") or lane_tid)
    elif foreign and flush_focus:
        ob_lane = _normalize_onboarding(
            entry.get("onboarding") or (entry.get("lane") or {}).get("onboarding")
        )
        if not ob_lane.get("name_confirmed"):
            writing_named = False
            meta_confirmed = False
            writing_path = ""
            sess_bid = ""
    prev_lane = _lane_from_entry(entry) if (foreign and not flush_focus) else {}
    use_session_extras = flush_focus or not foreign
    lane = {
        "task_id": lane_tid or sess_tid,
        "blueprint_id": sess_bid,
        "phase": str(getattr(session, "phase", "") or "").strip()
        if use_session_extras
        else str(prev_lane.get("phase") or ""),
        "writing_path": writing_path,
        "onboarding": onboarding
        if flush_focus
        else _normalize_onboarding(entry.get("onboarding") or onboarding),
        "blueprint_wizard": wiz,
        "plan": dict(ff.get("plan") or {})
        if use_session_extras
        else dict(prev_lane.get("plan") or {}),
        "writing_doc_named": writing_named,
        "meta_confirmed": meta_confirmed,
        "outline_confirmed": bool(ff.get("outline_confirmed"))
        if use_session_extras
        else bool(prev_lane.get("outline_confirmed")),
        "outline_text": str(ff.get("outline_text") or "")
        if use_session_extras
        else str(prev_lane.get("outline_text") or ""),
        "outline_pending_edit": bool(ff.get("outline_pending_edit"))
        if use_session_extras
        else bool(prev_lane.get("outline_pending_edit")),
        "title": title,
        "workflows_blueprint_wizard": wf_wiz,
        "session_goal": dict(ff.get("session_goal") or {})
        if use_session_extras
        else dict(prev_lane.get("session_goal") or {}),
        "writing_brief": dict(ff.get("writing_brief") or {})
        if use_session_extras and isinstance(ff.get("writing_brief"), dict)
        else (
            dict(prev_lane.get("writing_brief") or {})
            if isinstance(prev_lane.get("writing_brief"), dict)
            else {}
        ),
    }
    entry["lane"] = lane
    entry["onboarding"] = lane["onboarding"]
    docs[k] = entry
    ff["session_documents"] = docs
    session.freeform = ff


def hydrate_lane(session: ChatSession, key: str) -> None:
    """用指定文档车道覆盖会话单例（空字段必须 pop，禁止残留上一份）。"""
    k = normalize_document_registry_key((key or "").strip())
    if not k:
        return
    docs = session_documents_map(session)
    entry = dict(docs.get(k) or {})
    if k not in docs:
        return
    lane = _lane_from_entry(entry)
    session.task_id = str(lane.get("task_id") or entry.get("task_id") or "").strip()
    session.blueprint_id = str(lane.get("blueprint_id") or entry.get("blueprint_id") or "").strip()
    ph = str(lane.get("phase") or "").strip()
    session.phase = ph or ChatPhase.INTENT.value
    ff = dict(session.freeform or {})
    ff["focus_document_key"] = k
    title = clean_document_display_title(str(lane.get("title") or entry.get("title") or ""))
    if title:
        ff["title"] = title
    else:
        ff.pop("title", None)
    ob = _normalize_onboarding(lane.get("onboarding") or entry.get("onboarding"))
    wp = str(lane.get("writing_path") or "").strip()
    if str(ob.get("entry_path") or "") == "template" and wp == "freeform":
        wp = ""
    if wp:
        ff["writing_path"] = wp
    else:
        ff.pop("writing_path", None)
    wiz = dict(lane.get("blueprint_wizard") or {})
    live_wiz = dict(ff.get("blueprint_wizard") or {})
    live_step = str(live_wiz.get("step") or "").strip()
    lane_step = str(wiz.get("step") or "").strip()
    _wiz_rank = {
        "meta": 0,
        "upload": 1,
        "outline": 2,
        "outline_confirm": 3,
        "upload_done": 4,
        "study": 5,
    }
    if (
        live_step
        and str(live_wiz.get("mode") or "") != "selected"
        and _wiz_rank.get(live_step, -1) > _wiz_rank.get(lane_step, -1)
    ):
        wiz = live_wiz
    if wiz and not wizard_belongs_to_lane(
        wiz,
        key=k,
        entry=entry,
        lane_task_id=str(lane.get("task_id") or entry.get("task_id") or ""),
    ):
        # 进行中的创建/样本向导不要因为 stub blueprint_id 被车道身份清掉
        if str(wiz.get("mode") or "") == "selected" or str(wiz.get("step") or "") == "study":
            wiz = {}
            lane["blueprint_wizard"] = {}
            lane["workflows_blueprint_wizard"] = {}
    if wiz:
        ff["blueprint_wizard"] = wiz
    else:
        ff.pop("blueprint_wizard", None)
    plan = dict(lane.get("plan") or {})
    if plan:
        ff["plan"] = plan
    else:
        ff.pop("plan", None)
    if lane.get("writing_doc_named"):
        ff["writing_doc_named"] = True
    else:
        ff.pop("writing_doc_named", None)
    if lane.get("meta_confirmed"):
        ff["meta_confirmed"] = True
    else:
        ff.pop("meta_confirmed", None)
    if lane.get("outline_confirmed"):
        ff["outline_confirmed"] = True
    else:
        ff.pop("outline_confirmed", None)
    ot = str(lane.get("outline_text") or "").strip()
    if ot:
        ff["outline_text"] = ot
    else:
        ff.pop("outline_text", None)
    if lane.get("outline_pending_edit"):
        ff["outline_pending_edit"] = True
    else:
        ff.pop("outline_pending_edit", None)
    goal = dict(lane.get("session_goal") or {})
    if any(
        str(goal.get(k) or "").strip()
        for k in ("summary", "doc_category", "doc_title", "project", "notes")
    ):
        ff["session_goal"] = goal
    else:
        ff.pop("session_goal", None)
    wb = lane.get("writing_brief")
    if isinstance(wb, dict) and any(str(v or "").strip() for v in wb.values() if not isinstance(v, (list, dict))):
        ff["writing_brief"] = dict(wb)
    elif isinstance(wb, dict) and (wb.get("facts") or wb.get("narrative")):
        ff["writing_brief"] = dict(wb)
    else:
        ff.pop("writing_brief", None)
    lane["onboarding"] = ob
    entry["onboarding"] = ob
    entry["lane"] = lane
    docs[k] = entry
    ff["session_documents"] = docs
    session.freeform = ff
    wf = dict(lane.get("workflows_blueprint_wizard") or {})
    rt = dict(getattr(session, "runtime", None) or {}) if isinstance(getattr(session, "runtime", None), dict) else {}
    wfs = dict(rt.get("workflows") or {})
    if wf:
        wfs["blueprint_wizard"] = wf
    else:
        wfs.pop("blueprint_wizard", None)
    if wfs:
        rt["workflows"] = wfs
    else:
        rt.pop("workflows", None)
    session.runtime = rt or None


def focus_document_event(session: ChatSession, key: str = "", **extra: Any) -> dict[str, Any]:
    """切档 SSE：带上当前车道 task_id / writing_path / wizard。"""
    k = normalize_document_registry_key((key or focus_document_key(session) or "").strip())
    lane = document_lane(session, k) if k else empty_document_lane()
    ff = dict(session.freeform or {})
    ev: dict[str, Any] = {
        "type": "focus_document",
        "focus_document_key": k,
        "task_id": str(
            extra.get("task_id")
            or lane.get("task_id")
            or getattr(session, "task_id", "")
            or ""
        ),
        "blueprint_id": str(
            extra.get("blueprint_id")
            or lane.get("blueprint_id")
            or getattr(session, "blueprint_id", "")
            or ""
        ),
        "title": str(extra.get("title") or lane.get("title") or ""),
        "writing_path": str(lane.get("writing_path") or ff.get("writing_path") or ""),
        "wizard": dict(lane.get("blueprint_wizard") or ff.get("blueprint_wizard") or {}),
    }
    for name, value in extra.items():
        if name not in ev and value is not None:
            ev[name] = value
    return ev


def _project_empty_onboarding_singleton(
    session: ChatSession, *, keep_title: bool = False
) -> None:
    """force_new mint 后：会话单例只投影空车道，禁止残留上一份 wizard/path/named。"""
    session.blueprint_id = ""
    ff = dict(session.freeform or {})
    ff.pop("blueprint_wizard", None)
    ff.pop("writing_path", None)
    ff.pop("writing_doc_named", None)
    ff.pop("meta_confirmed", None)
    ff.pop("plan", None)
    if not keep_title:
        ff.pop("title", None)
    rt = dict(ff.get("runtime") or {}) if isinstance(ff.get("runtime"), dict) else {}
    wfs = dict(rt.get("workflows") or {})
    wfs.pop("blueprint_wizard", None)
    if wfs:
        rt["workflows"] = wfs
    else:
        rt.pop("workflows", None)
    if rt:
        ff["runtime"] = rt
    else:
        ff.pop("runtime", None)
    session.freeform = ff
    session.runtime = None
    try:
        from report_writing.chat.session_runtime import WORKFLOW_BLUEPRINT, workflow_set

        workflow_set(session, WORKFLOW_BLUEPRINT, {})
    except Exception:
        pass


def attach_empty_lane(
    session: ChatSession,
    key: str,
    *,
    title: str = "",
    task_id: str = "",
    force: bool = False,
) -> None:
    k = normalize_document_registry_key((key or "").strip())
    if not k:
        return
    ff = dict(session.freeform or {})
    docs = session_documents_map(session)
    entry = dict(docs.get(k) or {})
    if not force and isinstance(entry.get("lane"), dict) and entry.get("lane"):
        return
    lane = empty_document_lane(title=title or str(entry.get("title") or ""), task_id=task_id)
    entry["lane"] = lane
    entry["onboarding"] = lane["onboarding"]
    docs[k] = entry
    ff["session_documents"] = docs
    session.freeform = ff


def onboarding_lane_active(session: ChatSession) -> bool:
    """是否存在进行中的写作文档车道（lazy shell：无 lane 则不算 onboarding）。"""
    ff = dict(session.freeform or {})
    key = focus_document_key(session)
    if key:
        return True
    if session_documents_map(session):
        return True
    tid = str(getattr(session, "task_id", "") or "").strip()
    if tid:
        try:
            from report_writing.services.task_ids import is_writing_task_id

            if is_writing_task_id(tid):
                return True
        except Exception:
            pass
    if isinstance(ff.get("pending_document_onboarding"), dict):
        return True
    wiz = dict(ff.get("blueprint_wizard") or {})
    if str(wiz.get("working_task_id") or wiz.get("blueprint_id") or "").strip():
        return True
    try:
        from report_writing.chat.intent_clarify import get_pending

        pending = get_pending(session) or {}
        if str(pending.get("source") or "") in {
            "doc_name",
            "entry",
            "entry_intent",
            "control",
        }:
            return True
    except Exception:
        pass
    return False


def focus_needs_doc_name(session: ChatSession) -> bool:
    """焦点文档是否仍需用户确认名称。只认当前车道，不因上一份 writing_doc_named 放行。"""
    if not onboarding_lane_active(session):
        return False
    ff = dict(session.freeform or {})
    wiz = dict(ff.get("blueprint_wizard") or {})
    wiz_step = str(wiz.get("step") or "").strip()
    # 模版向导进行中：名称由 meta 卡收集，勿把 upload/提取改成定名卡
    if wiz_step in {"meta", "upload", "outline", "outline_confirm", "upload_done"}:
        return False
    if is_writing_document_named(session):
        return False
    ob = focus_onboarding(session)
    if ob.get("name_confirmed"):
        return False
    key = focus_document_key(session)
    if key:
        lane = document_lane(session, key)
        if lane.get("writing_doc_named"):
            return False
    return True


def focus_needs_entry_branch(session: ChatSession) -> bool:
    """未选「有蓝本 / 无蓝本」。工作名即可选路，不必等正式定名。"""
    if not onboarding_lane_active(session):
        return False
    ob = focus_onboarding(session)
    if str(ob.get("entry_branch") or "").strip() in _ENTRY_BRANCH_VALUES:
        return False
    if str(ob.get("entry_path") or "").strip() in _ENTRY_PATH_VALUES:
        return False
    return True


def focus_needs_freeform_mode(session: ChatSession) -> bool:
    """已选无蓝本但未选「模版 / 自动大纲」。"""
    if focus_needs_entry_branch(session):
        return False
    ob = focus_onboarding(session)
    if str(ob.get("entry_branch") or "").strip() != "without_blueprint":
        return False
    return str(ob.get("entry_path") or "").strip() not in _ENTRY_PATH_VALUES


def focus_needs_entry_path(session: ChatSession) -> bool:
    """入口未完成：缺主路选择，或缺无蓝本子路。"""
    if str(getattr(session, "blueprint_id", "") or "").strip():
        return False
    ff = dict(session.freeform or {})
    wiz = dict(ff.get("blueprint_wizard") or {})
    if str(wiz.get("step") or "").strip() in {
        "meta",
        "upload",
        "outline",
        "outline_confirm",
        "upload_done",
    }:
        return False
    return focus_needs_entry_branch(session) or focus_needs_freeform_mode(session)


def focus_onboarding_entry_complete(session: ChatSession) -> bool:
    """主路/无蓝本子路已选齐。正式定名不再作为入口完成条件。"""
    return not focus_needs_entry_path(session)


def focus_ready_for_auto_outline(session: ChatSession) -> bool:
    """可无蓝本链式 submit_outline(regenerate)。"""
    if not focus_onboarding_entry_complete(session):
        return False
    ob = focus_onboarding(session)
    return str(ob.get("entry_path") or "").strip() == "freeform"


def mark_focus_freeform_mode(session: ChatSession, mode: str) -> None:
    m = (mode or "").strip()
    if m not in {"auto_outline", "template"}:
        return
    patch_focus_onboarding(session, freeform_mode=m)


def mark_focus_name_confirmed(session: ChatSession) -> None:
    patch_focus_onboarding(session, name_confirmed=True, title_working="")


def focus_working_title(session: ChatSession) -> str:
    """当前车道工作名（未正式定名前的展示题）。"""
    ob = focus_onboarding(session)
    tip = clean_document_display_title(str(ob.get("title_working") or ""))
    if tip:
        return tip
    title = clean_document_display_title(
        str((session.freeform or {}).get("title") or "")
    )
    if title and title not in {"未命名文档", "未命名"} and not is_writing_document_named(
        session
    ):
        return title
    return ""


def focus_has_display_title(session: ChatSession) -> bool:
    """有正式名或工作名即可推进写作；不等于已定名。"""
    if is_writing_document_named(session):
        return True
    if focus_working_title(session):
        return True
    title = clean_document_display_title(_focus_display_title(session))
    return bool(title) and title not in {"未命名文档", "未命名"}


def apply_working_title(
    session: ChatSession,
    title: str,
    *,
    home: Path | None = None,
    message: str = "",
) -> str:
    """把写意图题材落成工作名：文档窗可展示，不标记 writing_doc_named。"""
    name = sanitize_candidate_document_title(title, message=message)
    if not name:
        name = clean_document_display_title(title)
        if message:
            name = sanitize_candidate_document_title(name, message=message) or name
        name = strip_write_subject_tail(name)
    if title_contains_action_clause(name):
        name = clean_document_display_title(quick_title_from_write_message(message))
    if not name or title_contains_action_clause(name):
        return ""
    if title_rejects_as_document_name(name, message=message):
        return ""
    if is_writing_document_named(session):
        return ""
    ob = focus_onboarding(session)
    if ob.get("name_confirmed"):
        return ""
    existing = clean_document_display_title(
        str((session.freeform or {}).get("title") or "")
    )
    if (
        existing
        and existing not in {"未命名文档", "未命名"}
        and existing != name
        and (focus_has_active_writing_context(session) or focus_document_key(session))
    ):
        # 活跃文档已有展示名：异题不得静默改成工作名，交给 document_scope
        return ""
    patch_focus_onboarding(session, title_working=name)
    ff = dict(session.freeform or {})
    ff["title"] = name
    session.freeform = ff
    key = focus_document_key(session)
    if key:
        docs = session_documents_map(session)
        entry = dict(docs.get(key) or {})
        cur = clean_document_display_title(str(entry.get("title") or ""))
        if (not cur) or cur in {"未命名文档", "未命名"} or cur == name:
            entry["title"] = name
        elif not ob.get("name_confirmed"):
            entry["title"] = name
        lane = dict(entry.get("lane") or {})
        if lane:
            lane["title"] = str(entry.get("title") or name)
            ob_lane = _normalize_onboarding(lane.get("onboarding") or {})
            ob_lane["title_working"] = name
            lane["onboarding"] = ob_lane
            entry["lane"] = lane
        entry["onboarding"] = {
            **_normalize_onboarding(entry.get("onboarding")),
            "title_working": name,
        }
        docs[key] = entry
        ff = dict(session.freeform or {})
        ff["session_documents"] = docs
        session.freeform = ff
    if home is not None:
        try:
            apply_focused_document_title(session, name, home=home)
        except Exception:
            pass
    try:
        from report_writing.chat.session_goal import merge_session_goal

        merge_session_goal(session, {"doc_title": name})
    except Exception:
        pass
    return name


def mark_focus_entry_branch(session: ChatSession, branch: str) -> None:
    br = (branch or "").strip()
    if br not in _ENTRY_BRANCH_VALUES:
        return
    ob = focus_onboarding(session)
    current_ep = str(ob.get("entry_path") or "").strip()
    if br == "with_blueprint":
        keep = current_ep if current_ep == "match" else ""
    else:
        keep = current_ep if current_ep in {"template", "freeform"} else ""
    patch_focus_onboarding(session, entry_branch=br, entry_path=keep)


def mark_focus_entry_path(session: ChatSession, entry_path: str) -> None:
    ep = (entry_path or "").strip()
    if ep not in _ENTRY_PATH_VALUES:
        return
    branch = "with_blueprint" if ep == "match" else "without_blueprint"
    patch_focus_onboarding(session, entry_path=ep, entry_branch=branch)


def stash_or_commit_entry_path(session: ChatSession, entry_path: str) -> None:
    """未定名只暂存线路；已定名才锁 entry_path。"""
    ep = (entry_path or "").strip()
    if ep not in _ENTRY_PATH_VALUES:
        return
    stash_focus_preferred_entry_path(session, ep)
    if not focus_needs_doc_name(session):
        mark_focus_entry_path(session, ep)


def message_wants_skip_to_freeform(text: str) -> bool:
    """明确不要蓝本/模版、自己定大纲。不含「不用这个蓝本」（那是丢掉当前库蓝本另选路）。"""
    compact = (text or "").replace(" ", "")
    if not compact:
        return False
    if any(k in compact for k in ("没有模版", "没有模板", "没模版", "没模板")):
        return True
    if any(k in compact for k in ("跳过蓝本", "无蓝本", "自己定大纲", "自动大纲", "自动生成大纲")):
        return True
    if "不用蓝本" in compact and "不用这个蓝本" not in compact:
        return True
    return False


def preferred_path_from_write_message(message: str) -> str:
    """从本轮写句推断用户已声明的线路（不落盘）。"""
    msg = (message or "").strip()
    if not msg:
        return ""
    if message_wants_own_template(msg):
        return "template"
    if user_wants_existing_blueprint_match(msg):
        return "match"
    if message_wants_skip_to_freeform(msg):
        return "freeform"
    compact = msg.replace(" ", "")
    if any(
        k in compact
        for k in (
            "自动生成大纲",
            "自动大纲",
            "直接生成大纲",
            "帮我生成目录",
            "自动生成章节目录",
            "生成章节目录",
            "自动生成目录",
        )
    ):
        return "freeform"
    try:
        from report_writing.chat.freeform_outline import outline_top_chapter_count

        if outline_top_chapter_count(msg):
            return "freeform"
    except Exception:
        pass
    try:
        from report_writing.chat.freeform_intent import lexical_freeform_intent

        if lexical_freeform_intent(msg) == "generate_outline":
            return "freeform"
    except Exception:
        pass
    return ""


def stash_focus_preferred_entry_path(session: ChatSession, path: str, *, residual: str = "") -> None:
    ep = (path or "").strip()
    updates: dict[str, Any] = {}
    if ep in _ENTRY_PATH_VALUES:
        updates["preferred_entry_path"] = ep
    res = (residual or "").strip()
    if res:
        updates["clarify_residual"] = res[:500]
    if updates:
        patch_focus_onboarding(session, **updates)


def message_declares_document_title(
    message: str,
    title_hint: str = "",
    *,
    explicit: str = "",
) -> str:
    """本句是否已**明确**交代文档名（如「名字叫…」）；题材「写一份××」不算定名。"""
    from report_writing.chat.helpers import is_vague_freeform_meta

    ex = (explicit or "").strip() or explicit_doc_title_from_message(message)
    if ex and not is_vague_freeform_meta(ex, field=True):
        return clean_document_display_title(ex)
    return ""


def try_confirm_document_name_from_message(
    session: ChatSession,
    message: str = "",
    *,
    title_hint: str = "",
    explicit: str = "",
    home: Path | None = None,
) -> str:
    """句中已明确文档名（如「名字叫…」）且尚未确认 → 直接定名，返回标题。"""
    declared = message_declares_document_title(message, title_hint, explicit=explicit)
    if not declared:
        return ""
    if not focus_needs_doc_name(session):
        if home is not None and explicit_doc_title_from_message(message):
            key = focus_document_key(session)
            if key or str(getattr(session, "blueprint_id", "") or "").strip():
                confirm_writing_document_name(
                    session,
                    declared,
                    home=home,
                    message=(message or "").strip() or declared,
                )
        return declared
    confirm_writing_document_name(
        session,
        declared,
        home=home,
        message=(message or "").strip() or declared,
    )
    return declared


def user_wants_existing_blueprint_match(
    message: str,
    *,
    session: ChatSession | None = None,
    invoke=None,
) -> bool:
    """用户意图是「从库内选/匹配蓝本」，可先出蓝本列表再定名。"""
    msg = (message or "").strip()
    if not msg:
        return False
    compact = msg.replace(" ", "")
    markers = (
        "现有蓝本",
        "有的蓝本",
        "用现在有的蓝本",
        "用现有",
        "蓝本库",
        "库里",
        "已有蓝本",
        "用蓝本",
        "匹配蓝本",
        "找蓝本",
        "选蓝本",
        "依据蓝本",
        "按蓝本",
        "用现在",
    )
    if any(m in compact for m in markers):
        return True
    if _USE_NAMED_BP_WRITE_RE.search(compact):
        return True
    if message_looks_like_new_write_topic(msg) and re.search(
        r"(使用|选用|用)", compact
    ):
        return True
    bound = str(getattr(session, "blueprint_id", "") or "").strip() if session else ""
    if bound and "使用" in compact and message_looks_like_new_write_topic(msg):
        return True
    try:
        from report_writing.chat.intent_clarify import _clear_entry_tool_from_message

        if _clear_entry_tool_from_message(msg, session=session, invoke=invoke) == "match_blueprints":
            return True
    except Exception:
        pass
    return False


def append_doc_name_clarify_if_needed(
    session: ChatSession,
    *,
    message: str = "",
    reply: str = "",
    cards: list | None = None,
    invoke=None,
) -> tuple[str, list]:
    """未确认文档名时，在蓝本匹配/选用后追加定名澄清卡。有工作名则不追问。"""
    if not focus_needs_doc_name(session) or focus_has_display_title(session):
        return reply, list(cards or [])
    from report_writing.chat.cards import turn_should_defer_doc_name_card

    merged = list(cards or [])
    defer_card = turn_should_defer_doc_name_card(merged, session=session)
    extra = (
        "选好蓝本后还要给这份写作文档起个名字（与蓝本库标题可以不同）。"
        if session.blueprint_id
        else "开写前还需要确认文档名称（可与蓝本库里的标题不同）。"
    )
    hint = (reply or "").strip()
    if defer_card:
        if hint and extra not in hint:
            hint = f"{hint}\n\n{extra}"
        elif not hint:
            hint = extra
        return hint.strip(), merged

    from report_writing.chat.intent_clarify import build_document_name_clarify

    pending = peek_pending_entry(session)
    tip = str(pending.get("title") or (session.freeform or {}).get("title") or "").strip()
    if not tip and message:
        tip = quick_title_from_write_message(message)
    name_reply, name_cards = build_document_name_clarify(
        session,
        message=message,
        title_hint=tip,
        invoke=invoke,
    )
    for c in name_cards or []:
        src = str((getattr(c, "payload", None) or {}).get("source") or "")
        if src == "doc_name" or getattr(c, "type", "") == "intent_clarify":
            if not any(
                str((getattr(x, "payload", None) or {}).get("source") or "") == "doc_name"
                for x in merged
            ):
                merged.append(c)
    if hint and extra not in hint:
        hint = f"{hint}\n\n{extra}"
    elif not hint:
        hint = name_reply
    elif name_reply and name_reply not in hint:
        hint = f"{hint}\n{name_reply}"
    return hint.strip(), merged


def preferred_entry_path_resolved(session: ChatSession, message: str = "", *, invoke=None) -> str:
    """从本句 + 已 stash 的 preferred 解析用户已声明的线路（无则空）。

    「生成目录」 alone 不得锁 freeform（须先选有蓝本/无蓝本）；
    首句已同时定名+生成大纲，或已选无蓝本支线，才可声明 freeform。
    """
    ob = focus_onboarding(session)
    pep = str(ob.get("preferred_entry_path") or "").strip()
    if pep in _ENTRY_PATH_VALUES:
        return pep
    msg = (message or "").strip()
    if not msg:
        return ""
    try:
        from report_writing.chat.freeform_outline import wants_generate_outline

        pending = peek_pending_entry(session)
        original = str(pending.get("message") or "").strip() or msg
        branch = str(ob.get("entry_branch") or "").strip()
        gen_msg = wants_generate_outline(
            msg, invoke=invoke, phase=str(session.phase or ""), session=session
        )
        gen_orig = wants_generate_outline(
            original, invoke=invoke, phase=str(session.phase or ""), session=session
        )
        if gen_msg or gen_orig:
            # 已选无蓝本 → 生成目录即 freeform（可跳过模版/自动二选一）
            if branch == "without_blueprint":
                return "freeform"
            # 首句同时带短名（正式名或工作名）+ 生成大纲 → 视为声明无蓝本自动大纲
            named_in_orig = bool(
                message_declares_document_title(original, "")
                or explicit_doc_title_from_message(original)
                or quick_title_from_write_message(original)
            )
            named_in_msg = bool(
                message_declares_document_title(msg, "")
                or explicit_doc_title_from_message(msg)
                or quick_title_from_write_message(msg)
            )
            if (named_in_orig and gen_orig) or (named_in_msg and gen_msg):
                from report_writing.chat.freeform_intent import lexical_freeform_intent

                if lexical_freeform_intent(msg) == "generate_outline" or (
                    original != msg
                    and lexical_freeform_intent(original) == "generate_outline"
                ):
                    return "freeform"
        try:
            from report_writing.chat.freeform_outline import outline_top_chapter_count

            if outline_top_chapter_count(msg) and not message_wants_own_template(msg):
                if branch == "without_blueprint" or quick_title_from_write_message(msg):
                    return "freeform"
        except Exception:
            pass
    except Exception:
        pass
    if invoke is not None:
        try:
            from report_writing.chat.intent_clarify import entry_path_from_routing_message

            ep = entry_path_from_routing_message(msg, session=session, invoke=invoke)
            if ep in _ENTRY_PATH_VALUES:
                return ep
        except Exception:
            pass
    return ""


def observe_new_document_gaps(
    session: ChatSession,
    message: str = "",
    *,
    title_hint: str = "",
    explicit: str = "",
    invoke=None,
) -> dict[str, Any]:
    """只读观察：新建/入口阶段缺哪些要素（不执行 Tool；顺序不固定，按缺项收集）。"""
    msg = (message or "").strip()
    tip = (title_hint or "").strip()
    ob = focus_onboarding(session)
    declared_title = message_declares_document_title(msg, tip, explicit=explicit)
    path_ep = preferred_entry_path_resolved(session, msg, invoke=invoke)
    declared_branch = ""
    declared_freeform_mode = ""
    if path_ep == "match":
        declared_branch = "with_blueprint"
    elif path_ep == "template":
        declared_branch = "without_blueprint"
        declared_freeform_mode = "template"
    elif path_ep == "freeform":
        declared_branch = "without_blueprint"
        declared_freeform_mode = "auto_outline"

    working = clean_document_display_title(str(ob.get("title_working") or ""))
    name_ok = bool(ob.get("name_confirmed")) or bool(declared_title) or bool(working)
    branch = str(ob.get("entry_branch") or declared_branch or "").strip()
    entry_path = str(ob.get("entry_path") or path_ep or "").strip()

    residual_intents: list[str] = []
    try:
        from report_writing.chat.freeform_outline import wants_generate_outline

        if msg and wants_generate_outline(
            msg, invoke=invoke, phase=str(session.phase or ""), session=session
        ):
            residual_intents.append("generate_outline")
    except Exception:
        pass

    missing_name = not name_ok
    missing_branch = name_ok and branch not in _ENTRY_BRANCH_VALUES and entry_path not in _ENTRY_PATH_VALUES
    missing_freeform_mode = (
        name_ok
        and (branch == "without_blueprint" or declared_branch == "without_blueprint")
        and entry_path not in _ENTRY_PATH_VALUES
        and not missing_branch
    )
    if branch == "with_blueprint" and entry_path == "match":
        missing_branch = False
        missing_freeform_mode = False

    return {
        "missing_name": missing_name,
        "missing_branch": missing_branch,
        "missing_freeform_mode": missing_freeform_mode,
        "missing_path": missing_branch or missing_freeform_mode,
        "declared_title": declared_title,
        "declared_branch": declared_branch,
        "declared_freeform_mode": declared_freeform_mode,
        "declared_entry_path": path_ep,
        "residual_intents": residual_intents,
    }


def suggest_next_tools_from_gaps(
    gaps: dict[str, Any],
    *,
    message: str = "",
    title: str = "",
    session: ChatSession | None = None,
) -> list[dict[str, Any]]:
    """原话已明确的要素 → 建议同轮 Tool 序列（缺什么不强行凑固定顺序）。"""
    msg = (message or "").strip()
    tip = (title or "").strip()
    base_args: dict[str, Any] = {"message": msg[:500] if msg else ""}
    if tip:
        base_args["title_hint"] = tip
        base_args["title"] = tip
    ep = str(gaps.get("declared_entry_path") or "").strip()
    bp_bound = session is not None and session_blueprint_already_selected(session)

    if message_wants_own_template(msg):
        return [
            {
                "tool": "begin_blueprint_wizard",
                "args": {**base_args, "confirmed_create": True, "from_entry_path": True},
            }
        ]
    if user_wants_existing_blueprint_match(msg) and not bp_bound:
        return [{"tool": "match_blueprints", "args": dict(base_args)}]
    if gaps.get("missing_freeform_mode") or gaps.get("missing_branch"):
        return []

    if gaps.get("missing_name"):
        if bp_bound:
            return []
        if user_wants_existing_blueprint_match(msg):
            return [{"tool": "match_blueprints", "args": dict(base_args)}]
        return []

    tools: list[dict[str, Any]] = []
    if ep == "match" and not bp_bound:
        if session is not None:
            ob = focus_onboarding(session)
            br = str(ob.get("entry_branch") or "").strip()
            live = str(ob.get("entry_path") or "").strip()
            if br != "with_blueprint" and live != "match":
                return tools
        tools.append({"tool": "match_blueprints", "args": dict(base_args)})
    elif ep == "template":
        if session is not None:
            ob = focus_onboarding(session)
            if str(ob.get("entry_branch") or "").strip() != "without_blueprint":
                return tools
            if str(ob.get("entry_path") or "").strip() != "template":
                return tools
        tools.append(
            {
                "tool": "begin_blueprint_wizard",
                "args": {**base_args, "confirmed_create": True, "from_entry_path": True},
            }
        )
    elif ep == "freeform":
        if session is not None:
            ob = focus_onboarding(session)
            if str(ob.get("entry_branch") or "").strip() != "without_blueprint":
                return tools
            if str(ob.get("entry_path") or "").strip() != "freeform":
                return tools
        tools.append({"tool": "skip_blueprint", "args": dict(base_args)})
        # auto_outline 槽位本身即「自动生成大纲」意图，勿等标题句再命中 generate_outline
        want_gen = (
            str(gaps.get("declared_freeform_mode") or "").strip() == "auto_outline"
            or "generate_outline" in list(gaps.get("residual_intents") or [])
        )
        if want_gen:
            tools.append(
                {
                    "tool": "submit_outline",
                    "args": {**base_args, "regenerate": True},
                }
            )
    branch = str(gaps.get("declared_branch") or "").strip()
    if branch == "without_blueprint" or ep == "freeform":
        tools = [t for t in tools if str(t.get("tool") or "") != "match_blueprints"]
    return tools


def stash_onboarding_observation(
    session: ChatSession,
    gaps: dict[str, Any],
    suggested_tools: list[dict[str, Any]] | None = None,
) -> None:
    ff = dict(session.freeform or {})
    ff["onboarding_gaps_snapshot"] = dict(gaps or {})
    ff["suggested_next_tools"] = list(suggested_tools or [])
    session.freeform = ff


def prepare_observed_onboarding(
    session: ChatSession,
    home: Path | None,
    *,
    message: str,
    title_hint: str = "",
    explicit: str = "",
    invoke=None,
) -> dict[str, Any]:
    """观察后仅落盘声明（定名 / preferred 线路），不 dispatch Tool。"""
    msg = (message or "").strip()
    tip = (title_hint or "").strip()
    declared = message_declares_document_title(msg, tip, explicit=explicit)
    if declared and focus_needs_doc_name(session):
        confirm_writing_document_name(session, declared, home=home, message=msg)
    ob = focus_onboarding(session)
    branch = str(ob.get("entry_branch") or "").strip()
    if branch == "without_blueprint":
        path_ep = preferred_entry_path_resolved(session, msg, invoke=invoke)
        if path_ep:
            stash_focus_preferred_entry_path(session, path_ep, residual=msg[:500] if msg else "")
            if path_ep == "template" and message_wants_own_template(msg):
                mark_focus_entry_path(session, "template")
            elif path_ep == "freeform":
                from report_writing.chat.freeform_intent import lexical_freeform_intent

                if lexical_freeform_intent(msg) == "generate_outline":
                    mark_focus_entry_path(session, "freeform")
    elif branch == "with_blueprint":
        path_ep = preferred_entry_path_resolved(session, msg, invoke=invoke) or "match"
        if path_ep:
            stash_focus_preferred_entry_path(session, path_ep)
        if path_ep == "match" or not path_ep:
            mark_focus_entry_path(session, "match")
    else:
        path_ep = preferred_entry_path_resolved(session, msg, invoke=invoke)
        if path_ep:
            stash_focus_preferred_entry_path(session, path_ep)
            # 未选有蓝本/无蓝本前只记录倾向，不 mark 主路（避免只定名就锁 freeform）
    gaps = observe_new_document_gaps(
        session, msg, title_hint=tip or declared, explicit=explicit, invoke=invoke
    )
    suggested = suggest_next_tools_from_gaps(
        gaps,
        message=msg,
        title=declared or tip or str((session.freeform or {}).get("title") or ""),
        session=session,
    )
    stash_onboarding_observation(session, gaps, suggested)
    return gaps


def begin_document_onboarding(
    session: ChatSession,
    home: Path | None,
    *,
    message: str = "",
    title_hint: str = "",
    force_new: bool = False,
    emit=None,
) -> None:
    """同会话新开/重开：清上一份定名与线路；后续按 gaps 灵活收集缺项（非固定先名后选路）。

    「新写一份述职报告」里的述职报告是题材，不是文档名。只有显式定名或调用方
    另行传入的标题才写入展示名；题材留下等用户起名，也不沿用上一份文档的标题。
    """
    keep_preferred = ""
    keep_residual = ""
    if force_new:
        # 只认本句声明的线路。上一份文档的 template/freeform 不得串到新文档。
        keep_preferred = preferred_path_from_write_message(message)
        if message:
            keep_residual = (message or "")[:500]
    clear_writing_document_named(session)
    ff = dict(session.freeform or {})
    explicit = explicit_doc_title_from_message(message)
    raw = clean_document_display_title(title_hint)
    if explicit:
        tip = explicit
    elif raw and not (message and title_is_write_subject_not_doc_name(message, raw)):
        tip = raw
    else:
        tip = ""
    if force_new:
        old_key = focus_document_key(session)
        if old_key:
            flush_lane(session, key=old_key)
            ff = dict(session.freeform or {})
        ff.pop("focus_document_key", None)
        ff.pop("blueprint_wizard", None)
        ff.pop("clarify_residual_message", None)
        ff.pop("pending_entry", None)
        # 上一份文档的写作简报/目录约束/已记下的标题不得串到新文档
        ff.pop("writing_brief", None)
        ff.pop("outline_constraints", None)
        ff.pop("outline_confirmed", None)
        ff.pop("outline_text", None)
        ff.pop("outline_pending_edit", None)
        ff.pop("writing_path", None)
        ff.pop("plan", None)
        goal = dict(ff.get("session_goal") or {})
        for key in ("doc_title", "doc_category", "summary", "project"):
            goal.pop(key, None)
        if any(str(goal.get(k) or "").strip() for k in goal):
            ff["session_goal"] = goal
        else:
            ff.pop("session_goal", None)
        session.blueprint_id = ""
        session.task_id = ""
        ff.pop("suggested_next_tools", None)
        ff.pop("onboarding_gaps_snapshot", None)
        ff.pop("pending_document_onboarding", None)
        ff.pop("document_thread_key", None)
        session.freeform = ff
        ff = dict(session.freeform or {})
        ff["document_thread_since_index"] = consume_new_document_thread_anchor(session)
        try:
            from report_writing.domain.models import ChatPhase

            session.phase = ChatPhase.INTENT.value
        except Exception:
            session.phase = "intent"
    if tip:
        ff["title"] = tip
    elif force_new:
        ff.pop("title", None)
        ff.pop("category", None)
    ff.pop("writing_path", None)
    session.freeform = ff
    patch_focus_onboarding(
        session,
        name_confirmed=False,
        entry_path="",
        entry_branch="",
        preferred_entry_path=keep_preferred,
        clarify_residual=keep_residual,
        freeform_mode="",
        title_working="",
    )
    if force_new and home is not None:
        # 未定名也必须立刻开新槽并切焦点，禁止本轮消息回落到上一份 bp:/task:
        # 题材 hint 不当文档名，只建空壳。同一句 force_new 命中当前空壳则不再 mint。
        display = tip or "未命名文档"
        reuse_tid = reusable_onboarding_shell(session, home, title_hint=tip)
        if reuse_tid:
            bind_onboarding_shell_to_session(session, reuse_tid, title_hint=tip)
        else:
            ensure_named_freeform_document(
                session,
                home,
                title=display,
                confirm_name=False,
            )
        if not tip:
            ff_shell = dict(session.freeform or {})
            ff_shell.pop("title", None)
            session.freeform = ff_shell
        patch_focus_onboarding(
            session,
            name_confirmed=False,
            entry_path="",
            entry_branch="",
            preferred_entry_path=keep_preferred,
            clarify_residual=keep_residual,
            title_working="",
        )
        nk = focus_document_key(session)
        if nk:
            attach_empty_lane(
                session,
                nk,
                title=display,
                task_id=str(getattr(session, "task_id", "") or ""),
                force=True,
            )
            hydrate_lane(session, nk)
            _project_empty_onboarding_singleton(session, keep_title=bool(tip))
            patch_focus_onboarding(
                session,
                name_confirmed=False,
                entry_path="",
                entry_branch="",
                preferred_entry_path=keep_preferred,
                clarify_residual=keep_residual,
                title_working="",
            )
            open_document_thread(session, nk)
            if emit:
                try:
                    emit(focus_document_event(session, nk, title=display))
                except Exception:
                    pass
    if message or tip:
        # 只暂存真正的文档名。题材「写一份××」不要写进 pending title，
        # 否则下一轮会把它当成已经定好的名字。
        stash_pending_entry(session, message=message, title=tip)
        if (
            message
            and not explicit_doc_title_from_message(message)
        ):
            pending_now = peek_pending_entry(session)
            ptitle = str(pending_now.get("title") or "")
            if ptitle and title_is_write_subject_not_doc_name(message, ptitle):
                ff_pending = dict(session.freeform or {})
                pe = dict(ff_pending.get("pending_entry") or {})
                pe.pop("title", None)
                ff_pending["pending_entry"] = pe
                session.freeform = ff_pending
    if force_new and (message or "").strip():
        try:
            from report_writing.chat.freeform_outline import (
                remember_outline_constraints_from_message,
            )

            remember_outline_constraints_from_message(session, message)
        except Exception:
            pass
        try:
            from report_writing.chat.session_goal import ingest_user_message_goal

            ingest_user_message_goal(session, message)
        except Exception:
            pass

def confirm_writing_document_name(
    session: ChatSession,
    title: str,
    *,
    home: Path | None = None,
    category: str = "",
    message: str = "",
    apply_to_focus: bool = True,
) -> str:
    """开写前确认文档名：写入 freeform / session_goal，并尽量落到焦点文档窗。

    apply_to_focus=False：只写会话标记，不改当前焦点 task（新开文档应先 ensure_named）。
    焦点文档标题与新名冲突时也跳过落盘，避免把文档2的名字写到文档1上。
    """
    name = sanitize_candidate_document_title(title, message=message)
    if not name:
        name = clean_document_display_title(title)
        if not name and message:
            name = clean_document_display_title(quick_title_from_write_message(message))
        name = strip_write_subject_tail(name)
    if title_contains_action_clause(name):
        name = clean_document_display_title(quick_title_from_write_message(message))
    if not name or title_contains_action_clause(name):
        return ""
    msg_check = (message or "").strip()
    if msg_check and title_is_write_subject_not_doc_name(msg_check, name):
        if write_subject_on_active_doc_should_clarify(
            session, msg_check, title_hint=name
        ):
            return ""
    cat = (category or "").strip()
    if not apply_to_focus or home is None:
        ff = dict(session.freeform or {})
        ff["title"] = name
        if cat:
            ff["category"] = cat
        ff["writing_doc_named"] = True
        ff["meta_confirmed"] = True
        session.freeform = ff
        stash_pending_entry(session, message=message or name, title=name)
        mark_focus_name_confirmed(session)
        return name
    tid = str(getattr(session, "task_id", "") or "").strip()
    bid = str(getattr(session, "blueprint_id", "") or "").strip()
    docs = session_documents_map(session)
    focus_key = focus_document_key(session)
    entry = (
        (docs.get(f"task:{tid}") if tid else {})
        or docs.get(focus_key)
        or (docs.get(f"bp:{bid}") if bid else {})
        or {}
    )
    entry_title = clean_document_display_title(str(entry.get("title") or ""))
    if not entry_title and tid:
        try:
            from report_writing.chat.helpers import _load_task

            task = _load_task(home, tid)
            entry_title = clean_document_display_title(
                str(getattr(task, "title", "") or "") if task else ""
            )
        except Exception:
            entry_title = ""
    if entry_title and _titles_conflict(entry_title, name):
        if should_mint_new_document_slot(
            session, entry_title, name, message=message or name
        ):
            begin_document_onboarding(
                session,
                home,
                message=message or name,
                title_hint=name,
                force_new=True,
            )
            ensure_named_freeform_document(
                session,
                home,
                title=name,
                category=cat,
                confirm_name=True,
            )
            return name
        if not should_update_title_in_place(
            session, entry_title, name, message=(message or name)
        ) and not explicit_doc_title_from_message(message or name):
            return ""
    ff = dict(session.freeform or {})
    ff["title"] = name
    if cat:
        ff["category"] = cat
    ff["writing_doc_named"] = True
    ff["meta_confirmed"] = True
    session.freeform = ff
    try:
        from report_writing.chat.session_goal import merge_session_goal, sync_goal_into_freeform

        merge_session_goal(
            session,
            {
                "doc_title": name,
                **({"doc_category": cat} if cat else {}),
                "summary": f"{cat}｜{name}".strip("｜") if cat else name,
            },
        )
        sync_goal_into_freeform(session)
    except Exception:
        pass
    stash_pending_entry(session, message=message or name, title=name)
    mark_focus_name_confirmed(session)
    if not tid and not bid:
        return name
    if entry_title and _titles_conflict(entry_title, name):
        apply_focused_document_title(session, name, home=home, category=cat)
        return name
    apply_focused_document_title(session, name, home=home, category=cat)
    return name

def ensure_named_freeform_document(
    session: ChatSession,
    home: Path | None,
    *,
    title: str,
    category: str = "",
    confirm_name: bool = True,
) -> str:
    """无蓝本：对话定名后立刻登记文档（必要时建空壳 task），供文档窗展示。

    若当前焦点任务标题与新题目明显冲突，则新开一份，保留同会话其它文档。
    confirm_name=False：只建槽/写展示名，不标记 writing_doc_named（开场定名卡未确认）。
    """
    name = clean_document_display_title(title)
    if not name:
        return ""
    cat = (category or "").strip()
    ff = dict(session.freeform or {})
    ff["title"] = name
    if cat:
        ff["category"] = cat
    wiz_now = dict(ff.get("blueprint_wizard") or {})
    wiz_mode = str(wiz_now.get("mode") or "").strip()
    wiz_step = str(wiz_now.get("step") or "").strip()
    ep_now = str(focus_onboarding(session).get("entry_path") or "").strip()
    keep_template_wizard = ep_now == "template" and wiz_mode in {
        "create",
        "freeform_sample",
    } and wiz_step in {"meta", "upload", "outline", "outline_confirm", "upload_done"}
    if confirm_name:
        ff["meta_confirmed"] = True
        ff["writing_doc_named"] = True
        # 定名 ≠ 已选线路：必须清掉上一份文档残留的 writing_path，
        # 否则前端会把旧向导改写成「已改为无蓝本」、并跳过选路卡
        ff.pop("writing_path", None)
        if not keep_template_wizard:
            ff.pop("blueprint_wizard", None)
    else:
        ff.pop("meta_confirmed", None)
        ff.pop("writing_doc_named", None)
        ff.pop("writing_path", None)
        if not keep_template_wizard:
            ff.pop("blueprint_wizard", None)
    session.freeform = ff

    tid = str(getattr(session, "task_id", "") or "").strip()
    bid = str(getattr(session, "blueprint_id", "") or "").strip()
    # 无蓝本路径：清掉会话蓝本焦点，避免把上一份蓝本/资料包串到新文档
    if bid:
        if tid == bid:
            tid = ""
            session.task_id = ""
        session.blueprint_id = ""
        bid = ""
    if tid:
        docs = session_documents_map(session)
        entry = docs.get(f"task:{tid}") or {}
        entry_title = str(entry.get("title") or "").strip()
        # 该写作任务若已挂库蓝本源，说明属于上一份选用线文档，勿复用
        entry_bid = str(entry.get("blueprint_id") or "").strip()
        if entry_bid:
            tid = ""
            session.task_id = ""
            set_focus_document_key(session, "")
        elif not entry_title and home is not None:
            try:
                from report_writing.chat.helpers import _load_task

                task = _load_task(home, tid)
                entry_title = str(getattr(task, "title", "") or "").strip() if task else ""
            except Exception:
                entry_title = ""
        if tid and _titles_conflict(entry_title, name):
            if should_update_title_in_place(session, entry_title, name):
                apply_focused_document_title(session, name, home=home, category=cat)
                if confirm_name:
                    mark_focus_name_confirmed(session)
            else:
                tid = ""
                session.task_id = ""
                set_focus_document_key(session, "")

    if home is not None and not tid:
        from report_writing.controller import ensure_task, save_spec_meta

        stamp = datetime.now().strftime("%Y%m%d%H%M%S")
        tid = f"w{stamp}{secrets.token_hex(2)}"
        task_path = home / "tasks" / tid
        ensure_task(task_path)
        save_spec_meta(
            task_path,
            title=name,
            category=cat,
            tags=["chat"],
            description="对话无蓝本写作",
        )
        session.task_id = tid

    # 直接登记无蓝本文档；勿经 apply_focused（会读回会话残留 blueprint_id）
    from report_writing.services.task_ids import is_writing_task_id

    use_tid = str(getattr(session, "task_id", "") or tid or "").strip()
    if use_tid:
        upsert_session_document(
            session,
            kind="writing" if is_writing_task_id(use_tid) else "outline",
            task_id=use_tid,
            blueprint_id="",
            writing_task_id=use_tid if is_writing_task_id(use_tid) else "",
            title=name,
            clear_blueprint_id=True,
        )
        reg_key = f"task:{use_tid}"
        set_focus_document_key(session, reg_key)
        open_document_thread(session, reg_key)
        stamp_document_thread_messages(session, reg_key)
        attach_empty_lane(
            session,
            reg_key,
            title=name,
            task_id=use_tid,
        )
        pending_ob = dict(ff.get("pending_document_onboarding") or {})
        if pending_ob or not confirm_name:
            ob = _normalize_onboarding(pending_ob if pending_ob else None)
            if not confirm_name:
                ob["name_confirmed"] = False
                ob.setdefault("entry_path", "")
            patch_focus_onboarding(session, **ob)
        elif confirm_name:
            # 新壳文档：只确认名称；已选主路（有蓝本/无蓝本）勿因尚未选子路而清空
            ob_now = focus_onboarding(session)
            if not str(ob_now.get("entry_path") or "").strip():
                patch_kwargs: dict[str, Any] = {
                    "name_confirmed": True,
                    "entry_path": "",
                }
                br = str(ob_now.get("entry_branch") or "").strip()
                if br not in _ENTRY_BRANCH_VALUES:
                    patch_kwargs["entry_branch"] = ""
                pep = str(ob_now.get("preferred_entry_path") or "").strip()
                if pep in _ENTRY_PATH_VALUES:
                    patch_kwargs["preferred_entry_path"] = pep
                patch_focus_onboarding(session, **patch_kwargs)
                # 尚待选路：清掉上一份文档的阶段，避免 freeform_* 串台
                ph = str(getattr(session, "phase", "") or "").strip()
                if ph.startswith("freeform_") or ph in {
                    "ready_to_write",
                    "writing",
                    "confirm_export",
                    "blueprint_select",
                    "create_handoff",
                }:
                    try:
                        from report_writing.domain.models import ChatPhase

                        session.phase = ChatPhase.INTENT.value
                    except Exception:
                        session.phase = "intent"
            else:
                patch_focus_onboarding(session, name_confirmed=True)
    return use_tid

def remove_session_document(session: ChatSession, registry_key: str) -> bool:
    """按登记键删除一份会话文档（用于同文档大纲 regenerate 清掉被取代的旧 w*）。"""
    key = (registry_key or "").strip()
    if not key:
        return False
    ff = dict(session.freeform or {})
    docs = session_documents_map(session)
    if key not in docs:
        return False
    docs = {k: v for k, v in docs.items() if k != key}
    ff["session_documents"] = docs
    session.freeform = ff
    return True


def prune_superseded_freeform_documents(
    session: ChatSession,
    *,
    keep_task_id: str,
    title: str = "",
) -> list[str]:
    """同一无蓝本文档只留最新 task：清掉同标题（或空壳）的其它 task:/writing 登记。

    不碰 blueprint / bp: / doc: 槽，避免误删选用线文档。
    """
    keep = (keep_task_id or "").strip()
    if not keep:
        return []
    want = clean_document_display_title(title)
    docs = session_documents_map(session)
    removed: list[str] = []
    for key, meta in list(docs.items()):
        if not str(key).startswith("task:"):
            continue
        tid = str(meta.get("task_id") or meta.get("writing_task_id") or "").strip()
        if not tid or tid == keep:
            continue
        kind = str(meta.get("kind") or "").strip()
        if kind in {"blueprint"}:
            continue
        if str(meta.get("blueprint_id") or "").strip():
            continue
        entry_title = clean_document_display_title(str(meta.get("title") or ""))
        same_title = bool(want and entry_title and (
            entry_title == want or want in entry_title or entry_title in want
        ))
        # 同标题，或焦点已切走后的空壳/写作登记 → 视为被最新大纲取代
        if same_title or (not entry_title and kind in {"writing", "outline", "task", ""}):
            if remove_session_document(session, key):
                removed.append(key)
    return removed


def upsert_session_document(
    session: ChatSession,
    *,
    kind: str,
    task_id: str = "",
    blueprint_id: str = "",
    writing_task_id: str | None = None,
    title: str = "",
    registry_key: str = "",
    clear_blueprint_id: bool = False,
) -> dict[str, Any]:
    """登记/更新一份会话文档；不删除其它条目。

    writing_task_id:
      - None：保留原 writing_task_id（默认）
      - ""：清空（选用蓝本但尚未为本文档开写）
      - 非空：绑定本文档的写作副本（一份文档一份副本）
    registry_key: 显式登记键（多文档共用同一库蓝本时用 doc:… 隔离）
    clear_blueprint_id: 无蓝本写作登记时强制清掉残留库蓝本 id，禁止串到上一份选用蓝本
    """
    tid = (task_id or "").strip()
    bid = (blueprint_id or "").strip()
    k = (kind or "").strip() or ("blueprint" if bid and (not tid or tid == bid) else "task")
    from report_writing.services.task_ids import is_writing_task_id

    # 样本向导用 w* 占 blueprint 槽时，按写作文档登记，勿写成 kind=blueprint
    if bid and is_writing_task_id(bid) and (not tid or tid == bid or is_writing_task_id(tid)):
        if k == "blueprint":
            k = "outline"
        if not tid:
            tid = bid
    key = normalize_document_registry_key(
        (registry_key or "").strip()
        or doc_registry_key(
            blueprint_id=bid, task_id=tid or (writing_task_id or ""), kind=k
        )
    )
    if not key:
        return {}
    normalize_session_document_keys(session)
    ff = dict(session.freeform or {})
    docs = session_documents_map(session)
    prev = dict(docs.get(key) or {})
    if writing_task_id is None:
        wid = str(prev.get("writing_task_id") or "").strip()
    else:
        wid = (writing_task_id or "").strip()
    cleaned_title = clean_document_display_title(title) if title else ""
    if clear_blueprint_id:
        entry_bid = ""
    else:
        entry_bid = bid or str(prev.get("blueprint_id") or "").strip()
    entry = {
        **prev,
        "kind": k,
        "title": cleaned_title or prev.get("title") or bid or tid or wid,
        "blueprint_id": entry_bid,
        "task_id": tid
        or (bid if k == "blueprint" else "")
        or str(prev.get("task_id") or "").strip(),
        "writing_task_id": wid,
        "updated_at": _utc_now(),
    }
    # 蓝本项：task_id 默认即蓝本 id，writing_task_id 另记 clone
    if k == "blueprint" and entry["blueprint_id"]:
        if not entry["task_id"] or entry["task_id"] == entry.get("writing_task_id"):
            entry["task_id"] = entry["blueprint_id"]
    docs[key] = entry
    # 上限，避免无限涨
    if len(docs) > 40:
        ordered = sorted(
            docs.items(),
            key=lambda kv: str(kv[1].get("updated_at") or ""),
            reverse=True,
        )
        docs = dict(ordered[:40])
    ff["session_documents"] = docs
    session.freeform = ff
    return entry

def focus_document_ids(session: ChatSession) -> tuple[str, str]:
    """返回 (task_id, blueprint_id) 焦点。"""
    tid = str(getattr(session, "task_id", "") or "").strip()
    bid = str(getattr(session, "blueprint_id", "") or "").strip()
    if not bid:
        wiz = dict((session.freeform or {}).get("blueprint_wizard") or {})
        bid = str(wiz.get("blueprint_id") or "").strip()
    return tid, bid

def _writing_copy_id_for_blueprint(session: ChatSession, blueprint_id: str) -> str:
    """登记表里该库蓝本对应的文档副本 id（一文档一副本）。"""
    bid = (blueprint_id or "").strip()
    if not bid:
        return ""
    focus = focus_document_meta(session)
    if str(focus.get("blueprint_id") or "").strip() == bid:
        wid = str(focus.get("writing_task_id") or "").strip()
        if wid:
            return wid
    for meta in session_documents_map(session).values():
        if str(meta.get("blueprint_id") or "").strip() != bid:
            continue
        wid = str(meta.get("writing_task_id") or "").strip()
        if wid:
            return wid
    sess_tid = str(getattr(session, "task_id", "") or "").strip()
    from report_writing.services.task_ids import is_writing_task_id

    if sess_tid and is_writing_task_id(sess_tid) and str(
        getattr(session, "blueprint_id", "") or ""
    ).strip() == bid:
        return sess_tid
    return ""


def resolve_task_id(
    session: ChatSession,
    payload: dict[str, Any] | None = None,
    *,
    require: bool = False,
) -> str:
    """解析落库目标任务 id（对话变更优先文档副本 w*，避免误写库蓝本）。

    顺序：payload.writing_task_id → payload.task_id（若为 bp* 则映射副本）→
    payload.blueprint_id 映射副本 → 焦点 writing_task_id / session.task_id。
    """
    from report_writing.services.task_ids import is_blueprint_task_id, is_writing_task_id

    body = dict(payload or {})
    payload_wid = str(body.get("writing_task_id") or "").strip()
    if payload_wid and is_writing_task_id(payload_wid):
        return payload_wid

    payload_tid = str(body.get("task_id") or "").strip()
    payload_bid = str(body.get("blueprint_id") or "").strip()
    focus_tid, focus_bid = focus_document_ids(session)
    focus_meta = focus_document_meta(session)
    focus_wid = str(focus_meta.get("writing_task_id") or "").strip()

    if payload_tid:
        if is_writing_task_id(payload_tid):
            return payload_tid
        if is_blueprint_task_id(payload_tid) or payload_tid == focus_bid or payload_tid == payload_bid:
            wid = _writing_copy_id_for_blueprint(session, payload_tid)
            if wid:
                return wid
        return payload_tid

    if payload_bid:
        wid = _writing_copy_id_for_blueprint(session, payload_bid)
        if wid:
            return wid
        return payload_bid

    if focus_wid and is_writing_task_id(focus_wid):
        return focus_wid
    if focus_tid and is_writing_task_id(focus_tid):
        return focus_tid
    if focus_tid:
        wid = _writing_copy_id_for_blueprint(session, focus_tid) if focus_tid == focus_bid else ""
        if wid:
            return wid
        return focus_tid
    if focus_bid:
        wid = _writing_copy_id_for_blueprint(session, focus_bid)
        if wid:
            return wid
        return focus_bid
    if require:
        raise ValueError("需要 task_id 或 blueprint_id")
    return ""

def resolve_blueprint_id(
    session: ChatSession,
    payload: dict[str, Any] | None = None,
) -> str:
    body = dict(payload or {})
    payload_bid = str(body.get("blueprint_id") or "").strip()
    if payload_bid:
        return payload_bid
    payload_tid = str(body.get("task_id") or "").strip()
    focus_tid, focus_bid = focus_document_ids(session)
    if payload_tid and focus_bid and payload_tid == focus_bid:
        return focus_bid
    if payload_tid and focus_tid and payload_tid == focus_tid and focus_bid:
        return focus_bid
    # 无 payload 蓝本：焦点蓝本
    if not payload_tid and focus_bid:
        return focus_bid
    return ""

def bind_session_focus_to_task(
    session: ChatSession,
    task_id: str,
    *,
    blueprint_id: str = "",
    writing_task_id: str | None = None,
    title: str = "",
    kind: str = "",
) -> None:
    """变更成功后对齐焦点，并 upsert 登记表（不碰其它文档条目的 writing_task_id）。

    writing_task_id:
      - None：保留本登记项原有写作副本（默认；避免蓝本焦点把资料包所在 clone 冲掉）
      - ""：显式清空
      - 非空：绑定写作副本
    """
    tid = (task_id or "").strip()
    if not tid:
        return
    bid = (blueprint_id or "").strip()
    from report_writing.services.task_ids import is_writing_task_id

    if is_writing_task_id(tid):
        wid = (writing_task_id or "").strip() or tid
        # 写作 clone：焦点 task_id=clone；蓝本 id 单独保留
        session.task_id = tid
        if bid:
            session.blueprint_id = bid
        upsert_session_document(
            session,
            kind="blueprint" if bid else "writing",
            task_id=bid or tid,
            blueprint_id=bid,
            writing_task_id=wid,
            title=title,
        )
        return

    # 蓝本或自由大纲任务
    session.task_id = tid
    if bid:
        session.blueprint_id = bid
    elif not str(session.blueprint_id or "").strip():
        # 自由大纲：清空蓝本焦点，避免串到旧蓝本
        pass
    k = (kind or "").strip() or (
        "blueprint" if bid and bid == tid else "outline" if not bid else "blueprint"
    )
    if k == "blueprint" or (bid and bid == tid):
        upsert_session_document(
            session,
            kind="blueprint",
            task_id=tid,
            blueprint_id=bid or tid,
            # 默认保留已有写作副本；资料包挂在 clone 上，勿因回落蓝本焦点而消失
            writing_task_id=writing_task_id,
            title=title,
        )
    else:
        upsert_session_document(
            session,
            kind=k if k in {"outline", "writing", "task"} else "outline",
            task_id=tid,
            blueprint_id=bid,
            writing_task_id=writing_task_id,
            title=title,
        )

def filter_pending_by_task(
    pending: list[dict[str, Any]],
    task_id: str,
    *,
    blueprint_id: str = "",
) -> list[dict[str, Any]]:
    """待收入要素：只保留目标任务（及同蓝本 writing clone 可选）。"""
    tid = (task_id or "").strip()
    bid = (blueprint_id or "").strip()
    if not tid and not bid:
        return list(pending or [])
    allowed = {x for x in (tid, bid) if x}
    out: list[dict[str, Any]] = []
    for row in pending or []:
        if not isinstance(row, dict):
            continue
        row_tid = str(row.get("task_id") or "").strip()
        if row_tid in allowed:
            out.append(row)
    return out

def _norm_title(text: str) -> str:
    t = (text or "").strip().casefold()
    for ch in ("「", "」", "《", "》", '"', "'", " ", "\t", "　", "文档", "蓝本", "报告", "-", "—", "_", "·"):
        t = t.replace(ch, "")
    return t

def _title_match_cores(title: str) -> list[str]:
    """标题全文 + 去后缀核心（「安全预评价-A」→「安全预评价」），便于口语点名。"""
    raw = (title or "").strip()
    cores: list[str] = []
    full = _norm_title(raw)
    if full:
        cores.append(full)
    for sep in ("-", "—", "_", "·", "（", "("):
        if sep in raw:
            head = _norm_title(raw.split(sep, 1)[0])
            if head and head not in cores:
                cores.append(head)
            break
    return cores

def session_documents_brief(session: ChatSession) -> str:
    """给意图模型的短列表。"""
    docs = session_documents_map(session)
    if not docs:
        return ""
    lines: list[str] = []
    for key, meta in docs.items():
        title = str(meta.get("title") or "").strip() or key
        kind = str(meta.get("kind") or "").strip() or "?"
        bid = str(meta.get("blueprint_id") or "").strip()
        tid = str(meta.get("task_id") or "").strip()
        wid = str(meta.get("writing_task_id") or "").strip()
        lines.append(f"- {key}｜{title}｜kind={kind}｜bp={bid or '—'}｜task={tid or '—'}｜write={wid or '—'}")
    return "\n".join(lines[:20])

def match_session_document(
    session: ChatSession,
    message: str,
    *,
    title_hint: str = "",
) -> dict[str, Any] | None:
    """从用户话里匹配登记表中的文档（按标题包含 / 提示名）。"""
    docs = session_documents_map(session)
    if not docs:
        return None
    text = (message or "").strip()
    hint = (title_hint or "").strip()
    norm_msg = _norm_title(text)
    norm_hint = _norm_title(hint)
    best: tuple[int, str, dict[str, Any]] | None = None
    for key, meta in docs.items():
        title = str(meta.get("title") or "").strip()
        if not title:
            continue
        cores = _title_match_cores(title)
        if not cores:
            continue
        score = 0
        for nt in cores:
            if len(nt) < 2:
                continue
            if norm_hint and (nt in norm_hint or norm_hint in nt):
                score = max(score, 100 + len(nt))
            if nt in norm_msg:
                score = max(score, 80 + len(nt))
            # 「那份安全预评价」类：标题前缀弱匹配
            if score < 40 and len(nt) >= 4:
                prefix = nt[: min(6, len(nt))]
                if prefix and prefix in norm_msg:
                    score = max(score, 40 + len(prefix))
        if score <= 0:
            continue
        # 不要把「当前焦点」误当成唯一命中：多文档时取分最高
        if best is None or score > best[0]:
            best = (score, key, {**meta, "registry_key": key})
    if best is None or best[0] < 40:
        return None
    return best[2]

def focus_document_key(session: ChatSession) -> str:
    ff = dict(session.freeform or {})
    key = normalize_document_registry_key(str(ff.get("focus_document_key") or "").strip())
    if key:
        return key
    thread = normalize_document_registry_key(str(ff.get("document_thread_key") or "").strip())
    if thread:
        return thread
    pending = ff.get("pending_document_onboarding")
    onboarding_open = isinstance(pending, dict) or bool(
        str(ff.get("document_thread_since_index") or "").strip()
        and not ff.get("document_thread_key")
    )
    if onboarding_open:
        tid = str(getattr(session, "task_id", "") or "").strip()
        if tid:
            return f"task:{tid}"
        return ""
    tid, bid = focus_document_ids(session)
    from report_writing.services.task_ids import is_writing_task_id

    if bid and is_writing_task_id(bid):
        return f"task:{bid}"
    if bid:
        return f"bp:{bid}"
    if tid:
        return f"task:{tid}"
    return ""

def focus_document_meta(session: ChatSession) -> dict[str, Any]:
    """当前焦点文档元信息（供 prompt / turn_context）。"""
    key = focus_document_key(session)
    docs = session_documents_map(session)
    meta = dict(docs.get(key) or {}) if key else {}
    tid, bid = focus_document_ids(session)
    title = str(meta.get("title") or "").strip()
    if not title:
        title = str(meta.get("blueprint_id") or bid or meta.get("task_id") or tid or "").strip()
    return {
        "registry_key": key,
        "title": title,
        "kind": str(meta.get("kind") or "").strip(),
        "blueprint_id": str(meta.get("blueprint_id") or bid or "").strip(),
        "task_id": str(meta.get("task_id") or tid or "").strip(),
        "writing_task_id": str(meta.get("writing_task_id") or "").strip(),
    }

def focus_bound_blueprint_id(session: ChatSession) -> str:
    """仅当前焦点文档绑定的蓝本 id；不回落其它文档残留的 session.blueprint_id。"""
    meta = focus_document_meta(session)
    bid = str(meta.get("blueprint_id") or "").strip()
    if bid:
        return bid
    focus_tid = str(
        meta.get("writing_task_id") or meta.get("task_id") or session.task_id or ""
    ).strip()
    try:
        from report_writing.chat.intent_router import active_blueprint_wizard

        wiz = dict(active_blueprint_wizard(session) or {})
    except Exception:
        wiz = {}
    wiz_bid = str(wiz.get("blueprint_id") or "").strip()
    wiz_work = str(wiz.get("working_task_id") or wiz_bid or "").strip()
    if wiz_bid and focus_tid and wiz_work in {focus_tid, wiz_bid}:
        return wiz_bid
    # 焦点就是该蓝本槽
    key = str(meta.get("registry_key") or "").strip()
    if wiz_bid and key in {f"bp:{wiz_bid}", wiz_bid}:
        return wiz_bid
    return ""


def focus_outline_chapter_count(session: ChatSession, home: Path | None = None) -> int:
    """焦点文档当前章节数（无 home 时尽量从会话预览推断）。"""
    meta = focus_document_meta(session)
    tid = str(
        meta.get("writing_task_id") or meta.get("task_id") or session.task_id or ""
    ).strip()
    if home is not None and tid:
        try:
            from report_writing.chat.helpers import _load_task

            task = _load_task(home, tid)
            if task and task.outline and task.outline.chapters:
                return len(list(task.outline.chapters or []))
        except Exception:
            pass
    ff = dict(session.freeform or {})
    try:
        n = int(ff.get("chapter_count") or 0)
        if n > 0:
            return n
    except (TypeError, ValueError):
        pass
    chapters = ff.get("chapters")
    if isinstance(chapters, list) and chapters:
        return len(chapters)
    text = str(ff.get("outline_text") or "").strip()
    if text:
        lines = [ln for ln in text.splitlines() if str(ln or "").strip()]
        if len(lines) >= 2:
            return len(lines)
    return 0


def focus_needs_outline(session: ChatSession, home: Path | None = None) -> bool:
    """已定名且已选路，但焦点文档还没有可写目录。"""
    if focus_needs_doc_name(session) or focus_needs_entry_path(session):
        return False
    ob = focus_onboarding(session)
    ep = str(ob.get("entry_path") or "").strip()
    if focus_outline_chapter_count(session, home) > 0:
        return False
    if ep == "match":
        # 已选用蓝本但仍无目录 → 缺目录；尚未绑蓝本则由选蓝本流程处理
        return bool(focus_bound_blueprint_id(session))
    if ep in {"freeform", "template"}:
        return True
    return False


def focus_document_brief(session: ChatSession) -> str:
    """单行：当前焦点文档，给意图/写作模型。"""
    meta = focus_document_meta(session)
    key = str(meta.get("registry_key") or "").strip()
    title = str(meta.get("title") or "").strip()
    if not key and not title:
        return "（尚无焦点文档）"
    bid = str(meta.get("blueprint_id") or "").strip()
    tid = str(meta.get("task_id") or "").strip()
    wid = str(meta.get("writing_task_id") or "").strip()
    parts = [f"标题={title or '—'}", f"key={key or '—'}"]
    if bid:
        parts.append(f"bp={bid}")
    if tid:
        parts.append(f"task={tid}")
    if wid:
        parts.append(f"write={wid}")
    ob = focus_onboarding(session)
    if not ob.get("name_confirmed"):
        working = str(ob.get("title_working") or "").strip()
        parts.append(f"onboarding=工作名:{working}" if working else "onboarding=待定名")
    elif focus_needs_entry_path(session):
        parts.append("onboarding=待选线路")
    elif focus_needs_outline(session):
        parts.append("onboarding=待目录")
    else:
        ep = str(ob.get("entry_path") or "").strip()
        if ep:
            parts.append(f"entry_path={ep}")
    return "；".join(parts)

def document_title_for_key(session: ChatSession, key: str) -> str:
    k = (key or "").strip()
    if not k:
        return ""
    meta = session_documents_map(session).get(k) or {}
    title = str(meta.get("title") or "").strip()
    if title:
        return title
    if k.startswith("bp:") or k.startswith("task:"):
        return k.split(":", 1)[-1]
    return k


def _library_blueprint_id_from_entry(meta: dict[str, Any]) -> str:
    """登记表上的库蓝本 id（非写作副本 w*）。"""
    from report_writing.services.task_ids import is_blueprint_task_id, is_writing_task_id

    bid = str(meta.get("blueprint_id") or "").strip()
    if bid and is_blueprint_task_id(bid):
        return bid
    tid = str(meta.get("task_id") or "").strip()
    if tid and is_blueprint_task_id(tid) and not is_writing_task_id(tid):
        return tid
    return ""


def focus_document_prefers_freeform_path(session: ChatSession) -> bool:
    """当前焦点文档是否应按无蓝本线路处理（与全局 writing_path 应对齐）。"""
    from report_writing.services.task_ids import is_writing_task_id

    # 尚待选路：不得因空壳 w* 任务误判为已走无蓝本
    if focus_needs_entry_path(session) or focus_needs_doc_name(session):
        return False
    key = focus_document_key(session)
    if not key:
        return str((session.freeform or {}).get("writing_path") or "").strip() == "freeform"
    entry = dict(session_documents_map(session).get(key) or {})
    ob_path = str(focus_onboarding(session).get("entry_path") or "").strip()
    if ob_path == "match":
        return False
    if ob_path == "template":
        return False
    if ob_path == "freeform":
        return True
    if _library_blueprint_id_from_entry(entry):
        return False
    kind = str(entry.get("kind") or "").strip()
    if kind == "blueprint":
        return False
    wid = str(entry.get("writing_task_id") or "").strip()
    tid = str(entry.get("task_id") or "").strip()
    if is_writing_task_id(wid or tid):
        return True
    return kind in {"outline", "writing", "task"}


def collected_blueprint_id_for_focus(session: ChatSession) -> str:
    """收录 id 仅对「本焦点写作文档」生效，避免 Doc1 收录后切 Doc2 仍带 sync/collected。"""
    key = focus_document_key(session)
    docs = session_documents_map(session)
    entry = dict(docs.get(key) or {}) if key else {}
    per_doc = str(entry.get("collected_blueprint_id") or "").strip()
    if per_doc:
        return per_doc
    ff = dict(session.freeform or {})
    collected = str(
        ff.get("collected_blueprint_id") or ff.get("freeform_saved_blueprint_id") or ""
    ).strip()
    if not collected:
        return ""
    if key == f"bp:{collected}":
        return collected
    wid = str(entry.get("writing_task_id") or "").strip()
    tid = str(entry.get("task_id") or "").strip()
    from report_writing.services.task_ids import is_writing_task_id

    focus_write = wid or (tid if is_writing_task_id(tid) else "")
    sess_tid = str(session.task_id or "").strip()
    if focus_write and sess_tid == focus_write:
        for meta in docs.values():
            if not isinstance(meta, dict):
                continue
            if str(meta.get("collected_blueprint_id") or "").strip() != collected:
                continue
            if str(meta.get("writing_task_id") or "").strip() == focus_write:
                return collected
    return ""


def rebind_session_to_focus_document(
    session: ChatSession, home: Path | None = None
) -> None:
    """切焦点/切文档 tab：对齐 writing_path、题目、goal，避免线路与向导串台。"""
    from report_writing.chat.intent_router import (
        WRITING_PATH_BLUEPRINT,
        WRITING_PATH_FREEFORM,
        lock_writing_path,
        touch_session_plan,
    )

    key = focus_document_key(session)
    if not key:
        return
    docs = session_documents_map(session)
    entry = dict(docs.get(key) or {})

    if isinstance(entry.get("lane"), dict) and entry.get("lane"):
        hydrate_lane(session, key)
        ob = focus_onboarding(session)
        ep = str(ob.get("entry_path") or "").strip()
        if ep == "template":
            ff = dict(session.freeform or {})
            if str(ff.get("writing_path") or "") == WRITING_PATH_FREEFORM:
                ff.pop("writing_path", None)
                session.freeform = ff
        elif focus_needs_entry_path(session):
            ff = dict(session.freeform or {})
            ff.pop("writing_path", None)
            session.freeform = ff
        elif ep == "freeform" or focus_document_prefers_freeform_path(session):
            lock_writing_path(session, WRITING_PATH_FREEFORM)
            if not _library_blueprint_id_from_entry(entry):
                session.blueprint_id = ""
        elif ep == "match":
            lock_writing_path(session, WRITING_PATH_BLUEPRINT)
            touch_session_plan(session, goal="pick_blueprint")
        snap = entry.get("goal_snapshot")
        if isinstance(snap, dict) and any(snap.get(k) for k in ("doc_title", "doc_category", "summary")):
            try:
                from report_writing.chat.session_goal import merge_session_goal, sync_goal_into_freeform

                merge_session_goal(session, {k: v for k, v in snap.items() if v})
                sync_goal_into_freeform(session)
            except Exception:
                pass
        if home is not None and not focus_document_prefers_freeform_path(session):
            lib_bp = _library_blueprint_id_from_entry(focus_document_meta(session))
            if lib_bp:
                try:
                    from report_writing.chat.session_runtime import reconcile_chat_session

                    reconcile_chat_session(home, session)
                except Exception:
                    pass
        return

    restore_focus_document_line_snapshot(session, entry)

    ob = focus_onboarding(session)
    ff_sync = dict(session.freeform or {})
    titled = clean_document_display_title(
        str(entry.get("title") or ff_sync.get("title") or "")
    )
    if ob.get("name_confirmed"):
        ff_sync["writing_doc_named"] = True
        ff_sync["meta_confirmed"] = True
    else:
        ff_sync.pop("writing_doc_named", None)
        ff_sync.pop("meta_confirmed", None)
    if titled:
        ff_sync["title"] = titled
    session.freeform = ff_sync

    ep = str(ob.get("entry_path") or "").strip()
    kind = str(entry.get("kind") or "").strip()
    bare_outline = kind in {"outline", "writing", "task"} and not _library_blueprint_id_from_entry(
        entry
    )
    if focus_needs_entry_path(session):
        ff = dict(session.freeform or {})
        ff.pop("writing_path", None)
        session.freeform = ff
    elif ep == "template":
        ff = dict(session.freeform or {})
        if str(ff.get("writing_path") or "") == WRITING_PATH_FREEFORM:
            ff.pop("writing_path", None)
        session.freeform = ff
    elif focus_document_prefers_freeform_path(session) or bare_outline:
        # 无库蓝本的大纲/写作文档保持无蓝本，避免自动大纲被改判成选用线而藏掉「收录」
        lock_writing_path(session, WRITING_PATH_FREEFORM)
        ff = dict(session.freeform or {})
        ff.pop("blueprint_wizard", None)
        session.freeform = ff
        if not _library_blueprint_id_from_entry(entry):
            session.blueprint_id = ""
    else:
        lock_writing_path(session, WRITING_PATH_BLUEPRINT)
        touch_session_plan(session, goal="pick_blueprint")

    snap = entry.get("goal_snapshot")
    if isinstance(snap, dict) and any(snap.get(k) for k in ("doc_title", "doc_category", "summary")):
        try:
            from report_writing.chat.session_goal import merge_session_goal, sync_goal_into_freeform

            merge_session_goal(session, {k: v for k, v in snap.items() if v})
            sync_goal_into_freeform(session)
        except Exception:
            pass
    else:
        title = clean_document_display_title(str(entry.get("title") or ""))
        if title:
            ff = dict(session.freeform or {})
            ff["title"] = title
            session.freeform = ff
            try:
                from report_writing.chat.session_goal import merge_session_goal, sync_goal_into_freeform

                merge_session_goal(session, {"doc_title": title, "summary": title})
                sync_goal_into_freeform(session)
            except Exception:
                pass

    if home is not None and not focus_document_prefers_freeform_path(session):
        lib_bp = _library_blueprint_id_from_entry(focus_document_meta(session))
        if lib_bp:
            try:
                from report_writing.chat.session_runtime import reconcile_chat_session

                reconcile_chat_session(home, session)
            except Exception:
                pass


def _doc_line_snapshot_from_session(session: ChatSession) -> dict[str, Any]:
    ff = dict(session.freeform or {})
    rt = getattr(session, "runtime", None)
    workflows: dict[str, Any] = {}
    if isinstance(rt, dict):
        workflows = dict(rt.get("workflows") or {})
    return {
        "task_id": str(getattr(session, "task_id", "") or ""),
        "blueprint_id": str(getattr(session, "blueprint_id", "") or ""),
        "phase": str(getattr(session, "phase", "") or ""),
        "writing_path": str(ff.get("writing_path") or ""),
        "blueprint_wizard": dict(ff.get("blueprint_wizard") or {}),
        "plan": dict(ff.get("plan") or {}),
        "workflows_blueprint_wizard": dict(workflows.get("blueprint_wizard") or {}),
    }


def snapshot_focus_document_state(
    session: ChatSession, *, key: str | None = None
) -> None:
    """切走焦点前：把会话级写作/向导状态写入登记表项。"""
    k = normalize_document_registry_key((key or focus_document_key(session) or "").strip())
    if not k:
        return
    ff = dict(session.freeform or {})
    docs = session_documents_map(session)
    entry = dict(docs.get(k) or {})
    entry["doc_line_snapshot"] = _doc_line_snapshot_from_session(session)
    docs[k] = entry
    ff["session_documents"] = docs
    session.freeform = ff


def restore_focus_document_line_snapshot(
    session: ChatSession, entry: dict[str, Any]
) -> None:
    raw = entry.get("doc_line_snapshot")
    if not isinstance(raw, dict) or not raw:
        return
    session.task_id = str(raw.get("task_id") or "").strip()
    session.blueprint_id = str(raw.get("blueprint_id") or "").strip()
    ph = str(raw.get("phase") or "").strip()
    if ph:
        session.phase = ph
    ff = dict(session.freeform or {})
    wp = str(raw.get("writing_path") or "").strip()
    if wp:
        ff["writing_path"] = wp
    else:
        ff.pop("writing_path", None)
    wiz = dict(raw.get("blueprint_wizard") or {})
    if wiz:
        ff["blueprint_wizard"] = wiz
    else:
        ff.pop("blueprint_wizard", None)
    plan = dict(raw.get("plan") or {})
    if plan:
        ff["plan"] = plan
    else:
        ff.pop("plan", None)
    session.freeform = ff
    wf = dict(raw.get("workflows_blueprint_wizard") or {})
    if wf:
        rt = dict(getattr(session, "runtime", None) or {})
        if not isinstance(rt, dict):
            rt = {}
        wfs = dict(rt.get("workflows") or {})
        wfs["blueprint_wizard"] = wf
        rt["workflows"] = wfs
        session.runtime = rt


def set_focus_document_key(session: ChatSession, key: str) -> None:
    normalize_session_document_keys(session)
    k = normalize_document_registry_key((key or "").strip())
    ff = dict(session.freeform or {})
    if k:
        ff["focus_document_key"] = k
    else:
        ff.pop("focus_document_key", None)
    session.freeform = ff


def apply_focus_document_by_key(
    session: ChatSession,
    key: str,
    *,
    home: Path | None = None,
    announce: bool = True,
) -> dict[str, str] | None:
    """按登记表 key 切换焦点；可选追加 assistant 切换提示（持久化到会话）。"""
    from report_writing.domain.models import ChatMessage

    k = normalize_document_registry_key((key or "").strip())
    if not k:
        return None
    docs = session_documents_map(session)
    if k not in docs:
        return None
    old = focus_document_key(session)
    if k == old:
        title = document_title_for_key(session, k) or str(docs.get(k, {}).get("title") or k)
        reply = focus_document_action_reply({"registry_key": k, "title": title})
        reply = reply.replace("已切换到", "当前文档是", 1)
        return {
            "registry_key": k,
            "title": title,
            "task_id": str(session.task_id or ""),
            "blueprint_id": str(session.blueprint_id or ""),
            "reply": reply,
            "unchanged": "1",
            "previous_key": old or "",
        }
    if old and old != k:
        flush_lane(session, key=old)
        snapshot_focus_document_state(session, key=old)
    set_focus_document_key(session, k)
    hydrate_lane(session, k)
    rebind_session_to_focus_document(session, home=home)
    title = document_title_for_key(session, k) or str(docs.get(k, {}).get("title") or k)
    ids = {
        "registry_key": k,
        "title": title,
        "task_id": str(session.task_id or ""),
        "blueprint_id": str(session.blueprint_id or ""),
        "previous_key": old or "",
    }
    reply = focus_document_action_reply(ids)
    ids["reply"] = reply
    if announce:
        session.messages = list(session.messages or [])
        session.messages.append(ChatMessage(role="assistant", text=reply, document_key=k))
    return ids

def apply_matched_document_to_session(
    session: ChatSession,
    matched: dict[str, Any],
    *,
    home: Path | None = None,
) -> dict[str, str]:
    """把匹配到的文档设为焦点，返回供 payload/前端用的 id。"""
    key = str(matched.get("registry_key") or "").strip()
    bid = str(matched.get("blueprint_id") or "").strip()
    tid = str(matched.get("task_id") or "").strip()
    wid = str(matched.get("writing_task_id") or "").strip()
    title = str(matched.get("title") or "").strip()
    kind = str(matched.get("kind") or "").strip()
    if key:
        set_focus_document_key(session, key)
    if kind == "blueprint" or (bid and (not tid or tid == bid)):
        session.blueprint_id = bid or tid
        # 若该蓝本已有写作 clone，焦点 task 指向 clone，便于写正文
        session.task_id = wid or ""
        out = {
            "registry_key": key or f"bp:{bid or tid}",
            "blueprint_id": bid or tid,
            "task_id": wid or bid or tid,
            "title": title,
            "kind": "blueprint",
        }
        rebind_session_to_focus_document(session, home=home)
        return out
    session.task_id = tid or wid
    if bid:
        session.blueprint_id = bid
    out = {
        "registry_key": key or (f"task:{tid}" if tid else ""),
        "blueprint_id": bid,
        "task_id": tid or wid,
        "title": title,
        "kind": kind or "outline",
    }
    rebind_session_to_focus_document(session, home=home)
    return out


def enrich_payload_with_message_document(
    session: ChatSession,
    message: str,
    payload: dict[str, Any] | None = None,
    *,
    switch_focus: bool = True,
) -> dict[str, Any]:
    """若用户话点名了登记表中的文档，写入 task_id/blueprint_id；可切换焦点。"""
    body = dict(payload or {})
    if str(body.get("task_id") or "").strip() or str(body.get("blueprint_id") or "").strip():
        return body
    hint = str(
        body.get("document_title")
        or body.get("doc_title")
        or body.get("target_document")
        or body.get("blueprint_title")
        or ""
    ).strip()
    matched = match_session_document(session, message, title_hint=hint)
    if not matched:
        return body
    ids = apply_matched_document_to_session(session, matched) if switch_focus else {
        "registry_key": str(matched.get("registry_key") or ""),
        "blueprint_id": str(matched.get("blueprint_id") or "").strip(),
        "task_id": str(
            matched.get("writing_task_id") or matched.get("task_id") or matched.get("blueprint_id") or ""
        ).strip(),
        "title": str(matched.get("title") or "").strip(),
        "kind": str(matched.get("kind") or "").strip(),
    }
    if ids.get("task_id") and not body.get("task_id"):
        body["task_id"] = ids["task_id"]
    if ids.get("blueprint_id") and not body.get("blueprint_id"):
        body["blueprint_id"] = ids["blueprint_id"]
    if ids.get("title"):
        body["_focus_document_title"] = ids["title"]
    if ids.get("registry_key"):
        body["_focus_document_key"] = ids["registry_key"]
    return body

def try_switch_focus_from_message(
    session: ChatSession,
    message: str,
    *,
    title_hint: str = "",
    home: Path | None = None,
) -> dict[str, str] | None:
    """用户点名会话内其它文档时切换焦点；未命中或已是当前焦点返回 None。"""
    text = (message or "").strip()
    if not text:
        return None
    # 新开/再写一份：禁止误切回会话里已有文档（含「再以…为题写一份」等）
    if message_wants_new_document_strong(text) or message_looks_like_new_write_topic(text):
        return None
    matched = match_session_document(session, text, title_hint=title_hint)
    if not matched:
        return None
    key = str(matched.get("registry_key") or "").strip()
    cur = focus_document_key(session)
    if key and key == cur:
        return None
    return apply_matched_document_to_session(session, matched, home=home)


def focus_document_action_reply(ids: dict[str, str]) -> str:
    title = str(ids.get("title") or "").strip() or "该文档"
    return (
        f"已切换到「{title}」。"
        "文档窗口会跟着打开这份；可以说分析、配置或写某一章。"
    )

# 无模型时的窄兜底（生产路径优先 resolve_document_scope → LLM）。勿继续堆口语词表。
_STRONG_NEW_DOC_HINTS = (
    "另写",
    "另外写",
    "再写一份",
    "再写份",
    "再来一份",
    "再开一份",
    "新建一份",
    "再创建",
    "换写一份",
    "再帮我写",
    "再根据",
    "再按我的模版",
    "再按我的模板",
    "再给我写",
    "再给我写份",
    "再写一篇",
    "再写篇",
    "同时写",
    "另外一份",
    "重写一份",
    "重新写一份",
    "重写帮我写",
    "重新帮我写",
    "从头写一份",
    "从头帮我写",
    "重新开写",
    "重开一份",
    "再开一个文档",
    "新开一份",
    "新开一个文档",
    "新写一份",
    "新写一个",
)

# 弱「重写」：已定名焦点上禁止静默 continue，应追问新开/整份重写当前/继续
_WEAK_REWRITE_HINTS = (
    "重新写",
    "重写",
    "再写一遍",
    "重写一遍",
    "全部重写",
    "整份重写",
    "从头再写",
    "再写",
)

# 本章重写锚点（启发式）
_CHAPTER_REVISE_HINT = re.compile(
    r"(第\s*[\d一二三四五六七八九十百千零〇两\.．\-]+?\s*章|本章|这一章|这一节|该章)"
)

# LLM force_new / continue 直办置信门槛（与 TurnPlan HIGH_CONFIDENCE 对齐）
SCOPE_HIGH_CONFIDENCE = 0.72

_LIST_BLUEPRINTS_MESSAGE_RE = re.compile(
    r"^(看看)?(有哪些|有什么|列出|显示|展示).{0,10}蓝本",
    re.IGNORECASE,
)
_OPEN_BP_LIBRARY_RE = re.compile(
    r"(查看|打开|浏览).{0,6}蓝本库|蓝本库.{0,8}(有哪些|看看)",
    re.IGNORECASE,
)
_NAV_LIBRARY_RE = re.compile(
    r"(打开|查看|浏览).{0,6}(要素库|资料库)",
    re.IGNORECASE,
)
_USE_NAMED_BP_WRITE_RE = re.compile(
    r"使用.{1,48}(写一?份|帮我写|写一篇|写个|写份)|"
    r"选用.{1,48}(写一?份|帮我写|写一篇)|"
    r"使用已有.{0,12}蓝本",
    re.IGNORECASE,
)
_CHITCHAT_ONLY_RE = re.compile(
    r"^(你好|您好|hi|hello|在吗|谢谢|感谢|哈喽|嗨)[\s！!？?。~]*$",
    re.IGNORECASE,
)


def message_is_chitchat_only(text: str) -> bool:
    """纯寒暄/致谢，无写文档、定名、选路意图。"""
    t = (text or "").strip()
    if not t:
        return False
    return bool(_CHITCHAT_ONLY_RE.match(t))


def title_rejects_as_document_name(title: str, message: str = "") -> bool:
    """禁止把寒暄、确认语等升格为文档名。"""
    t = (title or "").strip()
    if not t:
        return True
    if message_is_chitchat_only(t):
        return True
    msg = (message or "").strip()
    if msg and message_is_chitchat_only(msg) and t == msg:
        return True
    from report_writing.chat.helpers import is_vague_freeform_meta

    if is_vague_freeform_meta(t, field=True):
        return True
    return False


def message_requests_list_blueprints(text: str) -> bool:
    """用户只想看可选蓝本列表（选型），不是开写。"""
    t = (text or "").strip()
    if not t:
        return False
    if t in {
        "看看有哪些蓝本",
        "有哪些蓝本",
        "列出蓝本",
        "列出可选蓝本",
        "看看蓝本",
    }:
        return True
    compact = t.replace(" ", "")
    return bool(_LIST_BLUEPRINTS_MESSAGE_RE.search(compact))


def message_skips_onboarding_hang(text: str) -> bool:
    """纯浏览/导航：未定妆名也可执行，勿挂起名/选路卡。"""
    t = (text or "").strip()
    if not t:
        return False
    from report_writing.chat.product_guide import message_requests_usage_guide

    if message_requests_usage_guide(t):
        return True
    if message_requests_list_blueprints(t):
        return True
    compact = t.replace(" ", "")
    if _OPEN_BP_LIBRARY_RE.search(compact):
        return True
    if _NAV_LIBRARY_RE.search(compact):
        return True
    if t in {"打开要素库", "查看要素库", "打开资料库", "查看资料库"}:
        return True
    return False


def message_wants_new_document_strong(text: str) -> bool:
    """无 invoke 时的窄兜底。有模型时请用 resolve_document_scope(..., invoke=)。"""
    t = (text or "").strip()
    if not t:
        return False
    return any(h in t for h in _STRONG_NEW_DOC_HINTS)


def message_looks_like_chapter_revise(text: str) -> bool:
    """本句像「重写某一章」，不是新开文档。"""
    t = (text or "").strip()
    if not t:
        return False
    if message_wants_new_document_strong(t):
        return False
    compact = t.replace(" ", "")
    if not _CHAPTER_REVISE_HINT.search(compact):
        return False
    return any(k in compact for k in ("重写", "退回", "改写", "重试", "再写"))


def message_looks_like_weak_rewrite(text: str) -> bool:
    """裸「重新写/重写」等弱句：已定名时应追问，不可静默 continue。"""
    t = (text or "").strip()
    if not t:
        return False
    if message_wants_new_document_strong(t):
        return False
    if message_looks_like_chapter_revise(t):
        return False
    compact = t.replace(" ", "")
    # 已带明确新写题材句时不当弱重写
    if message_looks_like_new_write_topic(t) and quick_title_from_write_message(t):
        return False
    return any(h in compact for h in _WEAK_REWRITE_HINTS)


def _focus_display_title(session: ChatSession) -> str:
    """当前焦点文档展示名（登记表 / 工作名 / freeform title）。"""
    meta = focus_document_meta(session)
    tip = clean_document_display_title(str(meta.get("title") or ""))
    if tip and tip not in {"未命名文档", "未命名"}:
        return tip
    working = clean_document_display_title(
        str(focus_onboarding(session).get("title_working") or "")
    )
    if working:
        return working
    if tip:
        return tip
    return clean_document_display_title(str((session.freeform or {}).get("title") or ""))


def message_looks_like_new_write_topic(message: str) -> bool:
    """本句像「再写一份题材」请求（结构启发，非口语意图表扩张）。"""
    compact = (message or "").replace(" ", "")
    if not compact:
        return False
    return any(
        k in compact
        for k in ("写一份", "写个", "写份", "帮我写", "写一篇", "想写", "帮我生成一份")
    )


def message_confirms_write_document_intent(
    message: str,
    *,
    session: ChatSession | None = None,
    invoke=None,
) -> bool:
    """用户本句已明确要开始写文档（非浏览/寒暄）。"""
    msg = (message or "").strip()
    if not msg:
        return False
    if message_skips_onboarding_hang(msg):
        return False
    if _CHITCHAT_ONLY_RE.match(msg):
        return False
    if message_wants_own_template(msg):
        return True
    if message_looks_like_new_write_topic(msg):
        return True
    if session is not None and message_wants_template_new_document(session, msg):
        return True
    if user_wants_existing_blueprint_match(msg, session=session, invoke=invoke):
        return True
    if message_wants_new_document_strong(msg):
        return True
    if explicit_doc_title_from_message(msg) and not re.search(
        r"(改个名字|改名叫|改名为|重命名|标题改)", msg.replace(" ", "")
    ):
        return True
    if message_declares_document_title(msg, ""):
        return True
    try:
        from report_writing.chat.freeform_outline import wants_generate_outline

        if wants_generate_outline(
            msg, invoke=invoke, phase=str(getattr(session, "phase", "") or ""), session=session
        ):
            return True
    except Exception:
        pass
    return False


def should_hang_onboarding_gap(
    session: ChatSession,
    message: str,
    *,
    invoke=None,
    tool: str = "",
) -> bool:
    """是否应挂定名/选路卡。默认不挂：gaps 只观察，由 Tool 能力检查按需追问。"""
    del invoke
    if message_skips_onboarding_hang(message):
        return False
    name = (tool or "").strip()
    if not name:
        return False
    from report_writing.chat.tool_capability import missing_for_tool

    return bool(missing_for_tool(session, name))


def ensure_write_onboarding_started(
    session: ChatSession,
    home: Path | None,
    message: str,
    *,
    invoke=None,
    emit=None,
) -> bool:
    """首句写意图：创建/对齐 onboarding lane。返回 True 表示本轮新开了 lane。"""
    msg = (message or "").strip()
    if not msg or home is None:
        return False
    if not message_confirms_write_document_intent(msg, session=session, invoke=invoke):
        return False
    wiz = dict((session.freeform or {}).get("blueprint_wizard") or {})
    wiz_step = str(wiz.get("step") or "").strip()
    if wiz_step in {"upload", "outline", "outline_confirm", "upload_done", "study"}:
        return False
    if is_writing_document_named(session) or focus_has_active_writing_context(session):
        blocked = silent_rename_blocked_topic(session, msg)
        if blocked:
            return False
    minted = False
    if onboarding_lane_active(session):
        prepare_observed_onboarding(session, home, message=msg, invoke=invoke)
    else:
        begin_document_onboarding(session, home, message=msg, force_new=True, emit=emit)
        prepare_observed_onboarding(session, home, message=msg, invoke=invoke)
        minted = True
    explicit = explicit_doc_title_from_message(msg)
    if explicit:
        confirm_writing_document_name(session, explicit, home=home, message=msg)
    elif not is_writing_document_named(session):
        working = quick_title_from_write_message(msg)
        if working:
            apply_working_title(session, working, home=home, message=msg)
    return minted


def ambiguous_named_doc_new_topic(
    session: ChatSession,
    message: str,
    *,
    title_hint: str = "",
) -> str:
    """已定名焦点 + 新写题材且未显式另开/改名 → 返回题材短名；否则空。

    用于 need_clarify，禁止静默改名。
    对比用焦点展示名（含无蓝本 outline 登记），不单靠蓝本 title_suggests。
    """
    msg = (message or "").strip()
    if not msg:
        return ""
    if not is_writing_document_named(session) and not focus_has_active_writing_context(
        session
    ):
        return ""
    if explicit_doc_title_from_message(msg):
        return ""
    if message_wants_new_document_strong(msg):
        return ""
    if not message_looks_like_new_write_topic(msg):
        return ""
    topic = clean_document_display_title(
        (title_hint or "").strip() or quick_title_from_write_message(msg)
    )
    if not topic:
        # 「关于…」类写句可能抽不出短题，仍用整句截断作题材提示
        topic = clean_document_display_title(msg[:40])
    cur = _focus_display_title(session)
    if not cur:
        # 活跃焦点但展示名缺失：仍追问，避免静默改名
        if is_writing_document_named(session) or focus_has_active_writing_context(session):
            return topic or msg[:40]
        return ""
    nc = _norm_title(cur)
    nh = _norm_title(topic)
    if nc and nh and (nh in nc or nc in nh):
        return ""
    # 题材与当前名明显不同 → 歧义
    if nh and nc and nh != nc:
        return topic
    if title_suggests_different_document(session, topic):
        return topic
    # 写句但题材抽空：仍追问
    if message_looks_like_new_write_topic(msg) and (not nh or nh != nc):
        return topic or msg[:40]
    return ""


def resolve_write_subject_vs_focus(
    session: ChatSession,
    message: str,
    *,
    title_hint: str = "",
    invoke=None,
) -> dict[str, Any]:
    """活跃焦点 + 写题材句：澄清 / 新开 / 继续 / 显式改名。"""
    msg = (message or "").strip()
    tip = clean_document_display_title((title_hint or "").strip())
    if not msg:
        return {"action": "noop", "topic": "", "reason": "empty_message"}
    explicit = explicit_doc_title_from_message(msg)
    if explicit:
        return {"action": "rename_ok", "topic": explicit, "reason": "explicit_doc_title"}
    topic = tip or clean_document_display_title(quick_title_from_write_message(msg))
    if topic and _topic_matches_current_doc_title(session, topic):
        return {"action": "continue", "topic": topic, "reason": "same_topic_as_focus"}
    write_like = message_looks_like_new_write_topic(msg)
    if not write_like and not topic:
        return {"action": "noop", "topic": "", "reason": "not_write_subject"}
    if not focus_has_active_writing_context(session) and not is_writing_document_named(session):
        return {"action": "noop", "topic": topic, "reason": "no_active_focus"}
    scope = resolve_document_scope(
        session, msg, title_hint=topic or tip, invoke=invoke
    )
    stitle = clean_document_display_title(
        str(scope.get("title") or topic or tip or "").strip()
    )
    action = str(scope.get("action") or "").strip()
    if scope.get("need_clarify") or action == "ask":
        return {
            "action": "clarify",
            "topic": stitle or topic,
            "reason": "document_scope_ask",
            "scope": scope,
        }
    if action == "force_new" or scope.get("force_new"):
        return {
            "action": "force_new",
            "topic": stitle or topic,
            "reason": "document_scope_force_new",
            "scope": scope,
        }
    if action == "continue":
        return {
            "action": "continue",
            "topic": stitle or topic,
            "reason": "document_scope_continue",
            "scope": scope,
        }
    return {
        "action": "clarify",
        "topic": stitle or topic,
        "reason": "document_scope_fallback",
        "scope": scope,
    }


def write_subject_on_active_doc_should_clarify(
    session: ChatSession,
    message: str,
    *,
    title_hint: str = "",
    invoke=None,
) -> bool:
    verdict = resolve_write_subject_vs_focus(
        session, message, title_hint=title_hint, invoke=invoke
    )
    return str(verdict.get("action") or "") == "clarify"


def resolve_document_scope(
    session: ChatSession,
    message: str,
    *,
    title_hint: str = "",
    invoke=None,
) -> dict[str, str | bool | float]:
    """判断本句是续写当前 / 新开文档 / 切换已有 / 退回重写本章 / 整份重写当前 / 需追问。

    有 invoke 时走 LLM（说法不固定按意思判）；失败或无模型再退回窄启发式。
    返回：force_new / action / title / source / need_clarify / confidence
    action 含：continue | force_new | switch_existing | chapter_revise | rewrite_current | ask
    """
    msg = (message or "").strip()
    tip = (title_hint or "").strip()
    named = is_writing_document_named(session)
    if msg and message_wants_new_document_strong(msg):
        title = (
            tip
            or clean_document_display_title(quick_title_from_write_message(msg))
            or _focus_display_title(session)
        )
        return {
            "force_new": True,
            "action": "force_new",
            "title": title,
            "source": "heuristic_strong",
            "need_clarify": False,
            "confidence": 0.95,
        }
    if named and msg and message_wants_template_new_document(session, msg):
        title = tip or _template_new_doc_topic(msg) or quick_title_from_write_message(msg)
        return {
            "force_new": True,
            "action": "force_new",
            "title": title,
            "source": "heuristic",
            "need_clarify": False,
            "confidence": 0.9,
        }
    if invoke is not None and msg:
        try:
            from report_writing.chat.intent_context import pack_classifier_context
            from report_writing.chat.intent_llm import (
                classify_document_scope_llm,
                extract_write_subject_title_llm,
            )

            ctx = pack_classifier_context(session)
            judged = classify_document_scope_llm(
                msg,
                dialogue_tail=list(ctx.get("dialogue_tail") or []),
                focus_document_brief=str(ctx.get("focus_document_brief") or ""),
                session_documents_brief=str(ctx.get("session_documents_brief") or ""),
                invoke=invoke,
            ) or {}
            action = str(judged.get("action") or "").strip()
            if action:
                conf = float(judged.get("confidence") or 0)
                title = str(judged.get("title") or "").strip() or tip
                if not title and action in {"force_new", "ask", "rewrite_current"}:
                    title = extract_write_subject_title_llm(msg, invoke=invoke) or tip
                # 低置信 force_new（且无强提示）→ 追问，避免误新开
                if (
                    action == "force_new"
                    and conf < SCOPE_HIGH_CONFIDENCE
                    and not message_wants_new_document_strong(msg)
                ):
                    action = "ask"
                # 低置信 continue + 弱重写/明显异题新写 → 追问
                active = focus_has_active_writing_context(session)
                if action == "continue" and (named or active) and conf < SCOPE_HIGH_CONFIDENCE:
                    if message_looks_like_weak_rewrite(msg) or ambiguous_named_doc_new_topic(
                        session, msg, title_hint=title
                    ):
                        action = "ask"
                if action == "continue" and active and message_looks_like_new_write_topic(msg):
                    amb = ambiguous_named_doc_new_topic(session, msg, title_hint=title)
                    if amb and not message_wants_new_document_strong(msg):
                        action = "ask"
                if (
                    action == "force_new"
                    and active
                    and not message_wants_new_document_strong(msg)
                    and ambiguous_named_doc_new_topic(session, msg, title_hint=title)
                ):
                    action = "ask"
                # 高置信异题新写：LLM 若仍 ask，但启发式已能判定明显不同 → 直办 force_new
                if action == "ask" and conf >= SCOPE_HIGH_CONFIDENCE:
                    obvious = ambiguous_named_doc_new_topic(session, msg, title_hint=title)
                    if (
                        obvious
                        and message_looks_like_new_write_topic(msg)
                        and message_wants_new_document_strong(msg)
                    ):
                        action = "force_new"
                        title = title or obvious
                        conf = max(conf, SCOPE_HIGH_CONFIDENCE)
                need = action == "ask"
                return {
                    "force_new": action == "force_new",
                    "action": action,
                    "title": title,
                    "source": "llm",
                    "need_clarify": need,
                    "confidence": conf,
                }
        except Exception:
            pass
    # 启发式兜底：仅覆盖极少数固定说法；口语变化依赖上面的 LLM
    if msg and message_looks_like_chapter_revise(msg):
        return {
            "force_new": False,
            "action": "chapter_revise",
            "title": tip or "",
            "source": "heuristic",
            "need_clarify": False,
            "confidence": 0.85,
        }
    strong = bool(msg and message_wants_new_document_strong(msg))
    title = tip or quick_title_from_write_message(msg)
    if strong:
        return {
            "force_new": True,
            "action": "force_new",
            "title": title,
            "source": "heuristic",
            "need_clarify": False,
            "confidence": 0.9,
        }
    # 已定名 + 弱「重新写」：必须追问（新开 / 整份重写当前 / 继续）
    if named and msg and message_looks_like_weak_rewrite(msg):
        return {
            "force_new": False,
            "action": "ask",
            "title": title or _focus_display_title(session),
            "source": "heuristic",
            "need_clarify": True,
            "confidence": 0.55,
        }
    # 活跃焦点 + 新写题材且与当前标题明显不同 → 默认追问（无强新开词）
    obvious_topic = ambiguous_named_doc_new_topic(session, msg, title_hint=title)
    if obvious_topic:
        if focus_has_active_writing_context(session) and not message_wants_new_document_strong(
            msg
        ):
            return {
                "force_new": False,
                "action": "ask",
                "title": obvious_topic,
                "source": "heuristic",
                "need_clarify": True,
                "confidence": 0.62,
            }
        return {
            "force_new": True,
            "action": "force_new",
            "title": obvious_topic,
            "source": "heuristic",
            "need_clarify": False,
            "confidence": 0.88,
        }
    if title and title_suggests_different_document(session, title):
        if (
            focus_has_active_writing_context(session)
            and message_looks_like_new_write_topic(msg)
            and not message_wants_new_document_strong(msg)
        ):
            return {
                "force_new": False,
                "action": "ask",
                "title": title,
                "source": "heuristic",
                "need_clarify": True,
                "confidence": 0.6,
            }
        # 未定名开场：不同题材可新开；已定名明显异题已在上面 force_new
        return {
            "force_new": True,
            "action": "force_new",
            "title": title,
            "source": "heuristic",
            "need_clarify": False,
            "confidence": 0.85,
        }
    return {
        "force_new": False,
        "action": "continue",
        "title": title,
        "source": "heuristic",
        "need_clarify": False,
        "confidence": 0.7,
    }

# 无模型时的窄兜底；生产请用 entry_intent.is_create_blueprint_intent(..., invoke=)。
_EXPLICIT_CREATE_BLUEPRINT_HINTS = (
    "创建蓝本",
    "新建蓝本",
    "建个蓝本",
    "建蓝本",
    "做个蓝本",
    "上传样板",
    "上传蓝本",
    "创建新的蓝本",
    "创建新蓝本",
    "开蓝本向导",
)


def message_explicitly_creates_blueprint(
    text: str,
    *,
    invoke=None,
    session=None,
) -> bool:
    """是否明确要创建蓝本。有 invoke 走入口分类器；无模型才用窄短语表。"""
    t = (text or "").strip()
    if not t:
        return False
    if invoke is not None:
        from report_writing.chat.entry_intent import is_create_blueprint_intent

        return is_create_blueprint_intent(t, invoke=invoke, session=session)
    return any(h in t for h in _EXPLICIT_CREATE_BLUEPRINT_HINTS)

def explicit_doc_title_from_message(text: str) -> str:
    """无模型时的窄兜底：定名「名字叫… / 就叫…」与改名「名字改一下叫做 / 改名叫…」。

    有 invoke 时优先 classify_explicit_doc_name_given_llm；本函数作确定性兜底。
    「写一份××」题材不算显式文档名。
    """
    t = (text or "").strip()
    if not t:
        return ""
    patterns = (
        # 就叫 X / 就叫做 X（澄清卡口语确认）
        r"就叫[做为]?\s*[「『\"']?([^「」『』\"'\n，,。；;]{2,40})[」』\"']?",
        # 名字改一下叫做 X / 名字叫做 X / 名字叫为 X
        r"名字(?:改(?:一?下|成|为))?(?:叫[做为]?|是|为|：|:)\s*[「『\"']?([^「」『』\"'\n，,。；;]{2,40})[」』\"']?",
        # 改个名字 X / 改名叫 X / 改名为 X / 标题改成 X
        r"(?:改个名字|改一下名(?:字)?|改名(?:为|叫)|重命名(?:为|成)?|标题改(?:成|为)|文档名改(?:成|为)|名字改(?:成|为))\s*[「『\"']?([^「」『』\"'\n，,。；;]{2,40})[」』\"']?",
        r"(?:标题|题目)(?:叫|是|为|：|:)\s*[「『\"']?([^「」『』\"'\n，,。；;]{2,40})[」』\"']?",
        # 以「X」为题 / 以 X 为题（显式定名，非纯题材）
        r"以\s*[「『\"']([^「」『』\"'\n]{2,40})[」』\"']\s*为题",
        r"以\s*([^「」『』\"'\n，,。；;\s]{2,40})\s*为题",
    )
    for p in patterns:
        m = re.search(p, t)
        if not m:
            continue
        title = m.group(1).strip().rstrip("。.!！?？、，,")
        title = clean_document_display_title(title)
        if 1 < len(title) <= 40:
            return title
    return ""


def _template_new_doc_topic(message: str) -> str:
    """「用我的模版写一份××」里的题材短名。"""
    tip = clean_document_display_title(quick_title_from_write_message(message))
    if tip:
        return tip
    compact = (message or "").strip()
    m = re.search(
        r"(?:给我|帮我|再)?(?:来|出|写)?一份\s*[「『\"']?(.+?)[」』\"']?\s*$",
        compact,
    )
    if not m:
        return ""
    title = strip_write_subject_tail(m.group(1).strip().rstrip("。.!！?？、，,"))
    if 1 < len(title) <= 40:
        return clean_document_display_title(title)
    return ""


def message_wants_template_new_document(session: ChatSession, message: str) -> bool:
    """已定名文档上「用我的模版写一份××」（题材不同）应新开，不走章节三选。"""
    msg = (message or "").strip()
    if not msg or not message_wants_own_template(msg):
        return False
    if not is_writing_document_named(session) and not focus_has_active_writing_context(
        session
    ):
        return False
    if message_wants_new_document_strong(msg):
        return True
    topic = _template_new_doc_topic(msg)
    cur = _focus_display_title(session)
    nh = _norm_title(topic)
    nc = _norm_title(cur)
    if nh and nc and nh != nc and nh not in nc and nc not in nh:
        return True
    if topic and title_suggests_different_document(session, topic):
        return True
    compact = msg.replace(" ", "")
    looks_new = message_looks_like_new_write_topic(msg) or any(
        k in compact for k in ("给我一份", "来一份", "出一份")
    )
    if looks_new and topic and (not nc or nh != nc):
        return True
    return False


def message_wants_own_template(text: str) -> bool:
    """用户要用自己的 Word/PDF 模版/样本（非库蓝本）。"""
    compact = (text or "").replace(" ", "")
    if not compact:
        return False
    markers = (
        "用我的模版",
        "用我的模板",
        "我有模版",
        "我有模板",
        "参考模版",
        "参考模板",
        "按模版写",
        "按模板写",
        "依据模版",
        "依据模板",
        "根据我的模版",
        "根据我的模板",
        "根据模版",
        "根据模板",
        "用模版",
        "用模板",
        "上传样板",
        "我的样板",
    )
    return any(m in compact for m in markers)


def topic_title_should_not_suggest_as_doc_name(message: str, tip: str) -> bool:
    """「写一份安全报告」抽出的题材 ≠ 用户已定文档名；勿当「就叫××」建议。

    显式「名字叫/改名叫」除外；调用方传入的人工 title_hint 若与题材不同则保留。
    """
    msg = (message or "").strip()
    tip_clean = clean_document_display_title(tip)
    if not msg or not tip_clean:
        return False
    if explicit_doc_title_from_message(msg):
        return False
    # 只认「写一份××」题材，不含「短名 + 用蓝本/生成目录」拆出的工作名
    subj = _write_share_subject_from_patterns(msg)
    if not subj:
        return False
    return clean_document_display_title(subj) == tip_clean


def title_is_write_subject_not_doc_name(message: str, title: str) -> bool:
    """结构化 title 若只是写作题材（非显式定名）→ 禁止静默定名。

    含：「写一份安全报告」「安全报告 用我的模版」等；有「名字叫…」则放行。
    """
    tip = clean_document_display_title(title)
    msg = (message or "").strip()
    if not tip or not msg:
        return False
    if explicit_doc_title_from_message(msg):
        return False
    # 用自己的模版且未显式起名：任何结构化 title 都当题材，勿静默定名
    if message_wants_own_template(msg):
        return True
    if topic_title_should_not_suggest_as_doc_name(msg, tip):
        return True
    # 「帮我写一份安全报告」无模版词：题材命中 tip 也禁止
    tip_compact = tip.replace(" ", "")
    compact = msg.replace(" ", "")
    if tip_compact and tip_compact in compact and any(
        k in compact for k in ("写一份", "写个", "写份", "帮我写", "写一篇", "想写")
    ):
        return True
    return False


def _topic_matches_current_doc_title(session: ChatSession, tip: str) -> bool:
    """题材与当前文档名实质相同（含互相包含）→ 不算「新题材改名」歧义。"""
    topic = clean_document_display_title(tip)
    if not topic:
        return False
    cur = clean_document_display_title(
        _focus_display_title(session) or str((session.freeform or {}).get("title") or "")
    )
    if not cur:
        return False
    nt, nc = _norm_title(topic), _norm_title(cur)
    return bool(nt and nc and (nt == nc or nt in nc or nc in nt))


def silent_rename_blocked_topic(
    session: ChatSession,
    message: str,
    *,
    title: str = "",
) -> str:
    """活跃/已定名焦点 + 写题材句且无显式改名 → 返回题材提示（应挂 document_scope）；否则空。"""
    msg = (message or "").strip()
    if not msg:
        return ""
    if not is_writing_document_named(session) and not focus_has_active_writing_context(
        session
    ):
        return ""
    named = explicit_doc_title_from_message(msg)
    if named:
        # 「再写一份X，名字叫做Y」是新文档，不是给当前文档改名
        if (
            message_wants_new_document_strong(msg)
            or message_looks_like_new_write_topic(msg)
        ) and not _topic_matches_current_doc_title(session, named):
            return named
        return ""
    tip = clean_document_display_title(title)
    # 刚定名后残留「写一份××」且××就是当前名：继续当前，勿再追问新开/改名
    if tip and _topic_matches_current_doc_title(session, tip):
        return ""
    if tip and title_is_write_subject_not_doc_name(msg, tip):
        return tip
    ambiguous = ambiguous_named_doc_new_topic(session, msg, title_hint=tip)
    if ambiguous:
        return ambiguous
    # 已定名仍用写句塞 title：即使题材抽不出对比，也禁止静默改名
    if tip and message_looks_like_new_write_topic(msg):
        if _topic_matches_current_doc_title(session, tip):
            return ""
        return tip
    return ""


_WRITE_SUBJECT_SPLIT_MARKERS = (
    "自动生成章节目录",
    "生成章节目录",
    "自动生成目录",
    "自动生成大纲",
    "自动大纲",
    "自动目录",
    "生成目录",
    "起草目录",
    "生成大纲",
    "我有模版",
    "我有模板",
    "参考模版",
    "参考模板",
    "用我的模版",
    "用我的模板",
    "用模版",
    "用模板",
    "按模版写",
    "按模板写",
    "用已有蓝本",
    "匹配蓝本",
    "用现在有的蓝本",
    "现有蓝本",
    "有的蓝本",
    "蓝本库",
    "不用这个蓝本",
    "不用蓝本",
    "先不用蓝本",
    "跳过蓝本",
    "无蓝本",
    "用蓝本",
)

_CHAPTER_COUNT_IN_TITLE_RE = re.compile(
    r"(?:\d+|[一二两三四五六七八九十])个?大?章节"
)
_TITLE_FILLER_TAIL_RE = re.compile(r"(?:就可以|就行了?|就好了?|就这样|吧)+$")
_LEADING_WRITE_VERB_RE = re.compile(
    r"^(?:请)?(?:帮我|给我)?(?:再)?(?:来|出)?(?:作|写|做)([A-Za-z0-9].+)$"
)


def title_contains_action_clause(title: str) -> bool:
    """标题里还夹着生成目录/章数/路径等动作 → 不能当文档名。"""
    compact = re.sub(r"\s+", "", title or "")
    if not compact:
        return False
    for marker in _WRITE_SUBJECT_SPLIT_MARKERS:
        if marker and marker.replace(" ", "") in compact:
            return True
    if _CHAPTER_COUNT_IN_TITLE_RE.search(compact):
        return True
    if any(x in compact for x in ("就可以", "就行了", "就好了")):
        return True
    return False


def _strip_leading_write_verb(name: str) -> str:
    """「作lzb述职报告」→「lzb述职报告」；不碰「作业指导书」。"""
    raw = (name or "").strip()
    m = _LEADING_WRITE_VERB_RE.match(raw)
    rest = (m.group(1) if m else "").strip()
    return rest or raw


def strip_write_subject_tail(title: str) -> str:
    """从「写一份×× 自动生成目录」里只留文档题材。"""
    name = clean_document_display_title(title)
    if not name:
        return ""
    earliest = -1
    for marker in sorted(_WRITE_SUBJECT_SPLIT_MARKERS, key=len, reverse=True):
        idx = name.find(marker)
        if idx > 0 and (earliest < 0 or idx < earliest):
            earliest = idx
    count_m = _CHAPTER_COUNT_IN_TITLE_RE.search(name)
    if count_m and count_m.start() > 0 and (earliest < 0 or count_m.start() < earliest):
        earliest = count_m.start()
    if earliest > 0:
        name = name[:earliest]
    name = name.strip(" ，,、；;。.!！?？")
    name = _TITLE_FILLER_TAIL_RE.sub("", name).strip(" ，,、；;")
    name = _strip_leading_write_verb(name)
    return clean_document_display_title(name)


def sanitize_candidate_document_title(title: str, message: str = "") -> str:
    """把误当标题的整句/动作尾巴收成短文档名；收不干净则空串。"""
    msg = (message or "").strip()
    explicit = explicit_doc_title_from_message(msg) if msg else ""
    if explicit:
        cleaned = strip_write_subject_tail(explicit)
        if cleaned and not title_contains_action_clause(cleaned):
            return cleaned
    raw = clean_document_display_title(title) or msg
    cleaned = strip_write_subject_tail(raw)
    if title_rejects_as_document_name(cleaned, message=msg):
        return ""
    if cleaned and not title_contains_action_clause(cleaned) and 1 < len(cleaned) <= 40:
        return cleaned
    if msg:
        alt = quick_title_from_write_message(msg)
        if alt and not title_contains_action_clause(alt):
            return alt
    return ""


_WRITE_SHARE_PATTERNS = (
    r"(?:重写|重新写|重新)?(?:另外|再|另)?(?:帮我|给我)?写一?份\s*[「『\"']?(.+?)[」』\"']?\s*$",
    r"(?:重写|重新写|重新)?(?:另外|再)?(?:帮我|给我)?写[一]?个\s*[「『\"']?(.+?)[」』\"']?\s*$",
    r"(?:另外|再)(?:开|来)一份\s*[「『\"']?(.+?)[」』\"']?\s*$",
    r"重写帮我写一份\s*[「『\"']?(.+?)[」』\"']?\s*$",
    r"(?:再给我写|再帮我写)一?份\s*[「『\"']?(.+?)[」』\"']?\s*$",
    r"写一?份\s*[「『\"']?(.+?)[」』\"']?\s*$",
)


def _write_share_subject_from_patterns(text: str) -> str:
    """仅从「写一份××」句式抽题材；不含多动作拆句。"""
    t = (text or "").strip()
    if not t:
        return ""
    for p in _WRITE_SHARE_PATTERNS:
        m = re.search(p, t)
        if not m:
            continue
        title = m.group(1).strip().rstrip("。.!！?？、，,")
        title = strip_write_subject_tail(title)
        if title_contains_action_clause(title):
            continue
        if 1 < len(title) <= 40:
            return title
    return ""


def _title_before_action_clause(text: str) -> str:
    """多动作一句：在生成目录/章数等标记前取短名。"""
    name = (text or "").strip()
    if not name:
        return ""
    earliest = -1
    for marker in sorted(_WRITE_SUBJECT_SPLIT_MARKERS, key=len, reverse=True):
        idx = name.find(marker)
        if idx > 0 and (earliest < 0 or idx < earliest):
            earliest = idx
    count_m = _CHAPTER_COUNT_IN_TITLE_RE.search(name)
    if count_m and count_m.start() > 0 and (earliest < 0 or count_m.start() < earliest):
        earliest = count_m.start()
    if earliest <= 0:
        return ""
    title = strip_write_subject_tail(name[:earliest])
    if title_contains_action_clause(title):
        return ""
    if 1 < len(title) <= 40:
        return title
    return ""


def quick_title_from_write_message(text: str) -> str:
    """无模型时的窄兜底标题候选。有 invoke 时优先 extract_write_subject_title_llm。"""
    t = (text or "").strip()
    if not t:
        return ""
    explicit = explicit_doc_title_from_message(t)
    if explicit:
        cleaned = strip_write_subject_tail(explicit)
        if cleaned and not title_contains_action_clause(cleaned):
            return cleaned
    patterned = _write_share_subject_from_patterns(t)
    if patterned:
        return patterned
    cut = _title_before_action_clause(t)
    if cut:
        return cut
    return ""

def stash_pending_entry(
    session: ChatSession,
    *,
    message: str = "",
    title: str = "",
) -> None:
    ff = {**(session.freeform or {})}
    prev = dict(ff.get("pending_entry") or {}) if isinstance(ff.get("pending_entry"), dict) else {}
    msg = (message or "").strip()[:500]
    tip = (title or "").strip()[:80]
    if msg:
        prev["message"] = msg
    if tip:
        prev["title"] = tip
    elif msg and not prev.get("title"):
        tip2 = quick_title_from_write_message(msg)
        if tip2:
            prev["title"] = tip2
    if prev:
        ff["pending_entry"] = prev
        session.freeform = ff

def pop_pending_entry(session: ChatSession) -> dict[str, str]:
    ff = {**(session.freeform or {})}
    raw = ff.pop("pending_entry", None)
    session.freeform = ff
    if not isinstance(raw, dict):
        return {}
    return {
        "message": str(raw.get("message") or "").strip(),
        "title": str(raw.get("title") or "").strip(),
    }

def peek_pending_entry(session: ChatSession) -> dict[str, str]:
    raw = (session.freeform or {}).get("pending_entry")
    if not isinstance(raw, dict):
        return {}
    return {
        "message": str(raw.get("message") or "").strip(),
        "title": str(raw.get("title") or "").strip(),
    }

def title_suggests_different_document(session: ChatSession, title_hint: str) -> bool:
    """标题提示与当前焦点蓝本明显不同 → 应新开文档而非续写当前向导。"""
    hint = (title_hint or "").strip()
    if not hint:
        return False
    nh = _norm_title(hint)
    if len(nh) < 2:
        return False
    _, bid = focus_document_ids(session)
    docs = session_documents_map(session)
    cur_title = ""
    if bid and f"bp:{bid}" in docs:
        cur_title = str(docs[f"bp:{bid}"].get("title") or "").strip()
    if not cur_title:
        wiz = dict((session.freeform or {}).get("blueprint_wizard") or {})
        cur_title = str(wiz.get("title") or "").strip()
    if not cur_title:
        return bool(bid)  # 已有焦点蓝本但标题不同意图 → 倾向新开
    nc = _norm_title(cur_title)
    if not nc:
        return True
    if nh in nc or nc in nh:
        return False
    return True
