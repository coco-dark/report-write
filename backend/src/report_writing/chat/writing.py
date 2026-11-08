"""Chat orchestration helpers."""

from __future__ import annotations

import copy
import json
import logging
import re
import secrets
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable

from report_writing.services.chat_session import load_or_create, save_session
from report_writing.controller import (
    confirm_outline,
    ensure_task,
    outline_from_pasted_text,
    save_outline,
    save_spec_meta,
)
from report_writing.domain.models import (
    ChapterStatus,
    ChatActionPayload,
    ChatPayload,
    ChatCard,
    ChatMessage,
    ChatPhase,
    ChatRequest,
    ChatResponse,
    ChatSession,
    HumanDecision,
    OutlineNode,
    TaskStatus,
    WritingTask,
)
from report_writing.services.robots import (
    DEFAULT_CHAT_PROMPT,
    get_robot,
    match_blueprints_with_agent,
    resolve_prompt,
    search_blueprints,
)
from report_writing.services.store import TaskStore

_log = logging.getLogger("report_writing.chat")

InvokeFn = Callable[[str, str], str]
EmitFn = Callable[[dict[str, Any]], None]
StreamModelFn = Callable[[str, str], Iterable[str]]

def _normalize_writing_pace(value: Any) -> str:
    from report_writing.chat.helpers import WRITING_PACE_BATCH, WRITING_PACE_STEP
    text = str(value or "").strip().lower()
    if text in {WRITING_PACE_BATCH, "all", "full", "全部", "批量"}:
        return WRITING_PACE_BATCH
    if text in {WRITING_PACE_STEP, "chapter", "one", "逐章", "逐步"}:
        return WRITING_PACE_STEP
    return ""


def _session_writing_pace(session: ChatSession) -> str:
    return _normalize_writing_pace((session.freeform or {}).get("writing_pace"))


def writing_batch_scope(task_id: str, chapter_keys: list[str]) -> str:
    tid = str(task_id or "").strip()
    keys = sorted({str(k).strip() for k in (chapter_keys or []) if str(k).strip()})
    return f"{tid}|{','.join(keys)}"


def pace_resolved_for_scope(session: ChatSession, scope: str) -> bool:
    if not scope:
        return False
    ff = dict(session.freeform or {})
    if str(ff.get("writing_pace_scope") or "").strip() != scope:
        return False
    return bool(_normalize_writing_pace(ff.get("writing_pace")))


def remember_writing_pace_choice(session: ChatSession, *, scope: str, pace: str) -> None:
    ff = dict(session.freeform or {})
    ff["writing_pace"] = _normalize_writing_pace(pace) or WRITING_PACE_BATCH
    ff["writing_pace_scope"] = str(scope or "").strip()
    session.freeform = ff


def maybe_offer_writing_pace_clarify(
    session: ChatSession,
    chapter_keys: list[str],
    *,
    writing_pace: str = "",
    task_id_hint: str = "",
) -> tuple[str, list[ChatCard]] | None:
    """多章且本轮未选定节奏时返回澄清卡片。"""
    from report_writing.chat.clarify_flow import build_writing_pace_clarify

    keys = [str(k).strip() for k in (chapter_keys or []) if str(k).strip()]
    if len(keys) < 2:
        return None
    if _normalize_writing_pace(writing_pace):
        scope = writing_batch_scope(task_id_hint or session.task_id or session.blueprint_id, keys)
        remember_writing_pace_choice(session, scope=scope, pace=writing_pace)
        return None
    scope = writing_batch_scope(task_id_hint or session.task_id or session.blueprint_id, keys)
    if pace_resolved_for_scope(session, scope):
        return None
    return build_writing_pace_clarify(
        session,
        chapter_keys=keys,
        task_id=task_id_hint or str(session.task_id or "").strip(),
    )


def _chapter_needs_write(task: WritingTask, key: str) -> bool:
    chapter = next((c for c in (task.chapters or []) if c.key == key), None)
    if chapter is None:
        return True
    return chapter.status == ChapterStatus.PENDING


def _pending_write_keys(task: WritingTask) -> list[str]:
    return [key for key in _writing_chapter_keys(task) if _chapter_needs_write(task, key)]


def sync_session_phase_from_task(session: ChatSession, task: WritingTask) -> None:
    """会话 phase 跟任务真实流水线对齐：没有章在写就不要停在「写作中」。"""
    task.refresh_status()
    st = task.status.value if hasattr(task.status, "value") else str(task.status)
    if st == TaskStatus.RUNNING.value:
        session.phase = ChatPhase.WRITING.value
    elif st in {TaskStatus.AWAITING_CONFIRM.value, TaskStatus.COMPLETED.value}:
        session.phase = ChatPhase.CONFIRM_EXPORT.value
    elif st == TaskStatus.STUDYING.value:
        session.phase = ChatPhase.CREATE_HANDOFF.value
    else:
        session.phase = ChatPhase.READY_TO_WRITE.value


def _chapter_write_label(task: WritingTask, key: str) -> str:
    seq = ""
    title = ""
    if task.outline is not None:
        node = next((n for n in task.outline.chapters if n.key == key), None)
        if node is not None:
            seq = str(getattr(node, "sequence", "") or "").strip()
            title = str(getattr(node, "title", "") or "").strip()
    if not title:
        chapter = next((c for c in (task.chapters or []) if c.key == key), None)
        state_title = str(getattr(chapter, "title", "") or "").strip() if chapter else ""
        if state_title and state_title != key and "_" not in state_title:
            title = state_title
    title = title or "本章"
    return f"{seq} {title}".strip() if seq else title


def emit_chat_writing_step(
    emit: EmitFn | None,
    event: dict,
    *,
    task_id: str = "",
    chapter_title: str = "",
) -> dict:
    """首写 / 重写共用：step 事件 + 会话虚线过程行（检索/写作/检查/要点）。"""
    from report_writing.chat.helpers import _STEP_LABEL, _emit, _emit_process

    out = dict(event or {})
    if task_id and not out.get("task_id"):
        out["task_id"] = task_id
    title = str(out.get("title") or chapter_title or out.get("chapter") or "").strip()
    if chapter_title and (not title or title == out.get("chapter") or "_" in title):
        title = chapter_title
        out["title"] = title
    _emit(emit, out)
    if out.get("type") != "step":
        return out
    step = str(out.get("step") or "")
    status = str(out.get("status") or "")
    label = _STEP_LABEL.get(step, step or "步骤")
    status_cn = {"running": "进行中", "done": "完成", "skipped": "跳过", "error": "失败"}.get(
        status, status
    )
    result = out.get("result") if isinstance(out.get("result"), dict) else {}
    note = str((result or {}).get("note") or "").strip()
    tip = f"　{label} · {status_cn}" + (f" · {title}" if title else "")
    if note:
        tip = f"{tip} {note}"
    _emit_process(
        emit,
        tip,
        step=step,
        status=status,
        chapter=out.get("chapter") or "",
        task_id=out.get("task_id") or task_id or "",
    )
    if step == "search" and status in {"done", "skipped"}:
        from report_writing.chat.helpers import emit_search_element_lines

        emit_search_element_lines(emit, result or {})
    if step == "write" and status == "running":
        from report_writing.chat.helpers import emit_write_points_lines

        emit_write_points_lines(emit, result or {})
    return out


def _run_writing_keys(
    home: Path,
    session: ChatSession,
    writing: WritingTask,
    keys: list[str],
    *,
    emit: EmitFn | None = None,
    model=None,
) -> tuple[WritingTask, list[dict]]:
    from report_writing.chat.cards import _chapter_progress_row, _progress_and_confirm_cards
    from report_writing.chat.helpers import WRITING_PACE_STEP, _emit, _emit_process
    events: list[dict] = []
    run_keys = [str(k).strip() for k in (keys or []) if str(k).strip()]
    total = len(run_keys) or 1

    def on_event(event: dict) -> None:
        events.append(
            emit_chat_writing_step(emit, event, task_id=str(getattr(writing, "id", "") or ""))
        )

    from report_writing.services.writing_cancel import is_writing_cancelled

    try:
        from report_writing.chat.session_goal import apply_session_goal_to_task
        from report_writing.services.pipeline import run_chapters
        from report_writing.services.store import TaskStore

        # 开写时强制注入一次；之后用户清空补充提示词不再被重试写回
        if apply_session_goal_to_task(session, writing, force=True):
            TaskStore(home / "tasks" / writing.id).save(writing)
            from report_writing.services.doc_outline_cache import invalidate_doc_outline_cache

            invalidate_doc_outline_cache(writing.id)
        from report_writing.chat.writing_brief import attach_dialogue_content_basis

        attach_dialogue_content_basis(session, writing)
        TaskStore(home / "tasks" / writing.id).save(writing)

        for key in run_keys:
            if is_writing_cancelled(session.id):
                from report_writing.services.task_chapters import sync_confirmed_chapters

                try:
                    sync_confirmed_chapters(writing)
                except Exception as error:
                    _log.warning("pause sync chapters failed: %s", error)
                session.phase = ChatPhase.CONFIRM_EXPORT.value
                cards_now = _progress_and_confirm_cards(writing, pace=_session_writing_pace(session))
                paused = {
                    "type": "writing_paused",
                    "task_id": writing.id,
                    "cards": [c.to_dict() for c in cards_now],
                }
                events.append(paused)
                _emit(emit, paused)
                _emit_process(emit, "写作已暂停；已确认的章节已写入数据库。")
                break
            index = run_keys.index(key) + 1
            label = _chapter_write_label(writing, key)
            start_ev = {
                "type": "chapter_start",
                "task_id": writing.id,
                "chapter": key,
                "title": label,
                "index": index,
                "total": total,
            }
            events.append(start_ev)
            _emit(emit, start_ev)

            from report_writing.chat.writing_supplement_check import (
                load_registry,
                save_registry,
                snapshot_chapter_gaps_at_write,
            )

            reg = load_registry(session)
            snapshot_chapter_gaps_at_write(reg, key)
            save_registry(session, reg)

            writing = run_chapters(
                home / "tasks" / writing.id,
                chapter_keys=[key],
                robot_id=session.robot_id or "",
                on_event=on_event,
                model=model,
                force_rerun=True,
            )
            # 兜底：写完仍停在待确认/待人工时，有正文则直接落库为已确认
            try:
                chapter_state = writing.chapter(key)
            except KeyError:
                chapter_state = next((c for c in (writing.chapters or []) if c.key == key), None)
            if chapter_state is not None and chapter_state.status in {
                ChapterStatus.AWAITING_CONFIRM,
                ChapterStatus.NEEDS_HUMAN,
            }:
                try:
                    from report_writing.services.pipeline import confirm_chapter, seal_written_chapter

                    if seal_written_chapter(chapter_state, note="写完自动保存"):
                        from report_writing.services.store import TaskStore

                        writing.refresh_status()
                        TaskStore(home / "tasks" / writing.id).save(writing)
                        try:
                            from report_writing.services.task_chapters import sync_confirmed_chapters

                            sync_confirmed_chapters(writing)
                        except Exception:
                            pass
                    else:
                        template = (
                            Path(writing.template_path)
                            if writing.template_path
                            else home / "tasks" / writing.id / "outline-template.json"
                        )
                        dataset = (
                            Path(writing.dataset_dir)
                            if writing.dataset_dir
                            else home / "tasks" / writing.id
                        )
                        writing = confirm_chapter(
                            home / "tasks" / writing.id,
                            template,
                            dataset,
                            key,
                            HumanDecision.APPROVE,
                            text="",
                            comment="写完自动保存",
                        )
                except Exception as auto_err:
                    _log.warning("auto-confirm chapter %s failed: %s", key, auto_err)
            try:
                chapter_state = writing.chapter(key)
            except KeyError:
                chapter_state = next((c for c in (writing.chapters or []) if c.key == key), None)
            if chapter_state is None:
                _emit_process(emit, f"⚠ 「{label}」未找到章节状态，跳过汇总")
                continue
            row = _chapter_progress_row(writing, chapter_state)
            cards_now = _progress_and_confirm_cards(writing, pace=_session_writing_pace(session))
            from report_writing.chat.cards import _chapter_draft_text

            draft_html = (_chapter_draft_text(chapter_state) or "").strip()
            done_ev = {
                "type": "chapter_done",
                "task_id": writing.id,
                "chapter": key,
                "title": label,
                "index": index,
                "total": total,
                "status": row.get("status"),
                "status_label": row.get("status_label"),
                "has_draft": row.get("has_draft"),
                "draft_preview": (row.get("draft_preview") or draft_html)[:1800],
                "draft_html": draft_html,
                "cards": [c.to_dict() for c in cards_now],
            }
            events.append(done_ev)
            _emit(emit, done_ev)

        done = {"type": "run_done", "task_id": writing.id, "status": str(writing.status)}
        events.append(done)
        _emit(emit, done)
        if len(run_keys) > 1:
            _emit_process(emit, "各章已按顺序写完，可在右侧文档窗口查看正文。")
    except Exception as error:
        _log.warning("run_chapters from chat: %s", error)
        err = {"type": "error", "detail": str(error)}
        events.append(err)
        _emit(emit, err)
        _emit_process(emit, f"写作过程出错：{error}")
    return writing, events


def _finish_writing_round(
    home: Path,
    session: ChatSession,
    writing: WritingTask,
    events: list[dict],
    *,
    pace: str,
) -> tuple[str, list[ChatCard], list[dict]]:
    from report_writing.chat.helpers import WRITING_PACE_STEP
    from report_writing.chat.resource_readiness import export_phase_cards, robot_export_ready
    from report_writing.chat.cards import _progress_and_confirm_cards
    from report_writing.services.robots import get_robot

    robot = get_robot(home, session.robot_id) if session.robot_id else None
    writing.refresh_status()
    cards = _progress_and_confirm_cards(writing, pace=pace)
    sync_session_phase_from_task(session, writing)
    if writing.status == TaskStatus.COMPLETED or (
        writing.chapters and all(c.status in {ChapterStatus.CONFIRMED, ChapterStatus.SKIPPED} for c in writing.chapters)
    ):
        cards.extend(export_phase_cards(home, writing.id, robot))
        return "各章都确认好了，可以在输入框上方「下载 Word」导出。", cards, events

    awaiting = any(c.status == ChapterStatus.AWAITING_CONFIRM for c in (writing.chapters or []))
    needs_human = any(c.status == ChapterStatus.NEEDS_HUMAN for c in (writing.chapters or []))
    pending_left = len(_pending_write_keys(writing))

    if pace == WRITING_PACE_STEP:
        if awaiting or needs_human:
            left = f"还剩 {pending_left} 章没写。" if pending_left else "这是最后一章了。"
            return (
                f"这一章写好了。{left}可在右侧文档窗口查看正文。",
                cards,
                events,
            )
        if pending_left:
            return f"本章写作完成。可重写本章，或继续写作（还剩 {pending_left} 章）。", cards, events
        return "各章都写过了，可在右侧文档窗口查看正文。", cards, events

    if needs_human:
        reply = "全部写完一轮了。有几章需要人工处理，请在右侧文档窗口查看。"
    else:
        reply = (
            f"写作任务 {writing.id} 已按章依次写完，可在右侧文档窗口查看正文。"
            + (" 中途出了点问题，可稍后重试单章。" if events and any(e.get("type") == "error" for e in events) else "")
        )
    return reply, cards, events


def _maybe_continue_step_writing(
    home: Path,
    session: ChatSession,
    task: WritingTask,
    *,
    emit: EmitFn | None = None,
    prefix: str = "",
    model=None,
) -> tuple[str, list[ChatCard], list[dict]]:
    """逐步模式下，确认当前章后自动写下一章。"""
    from report_writing.chat.cards import _progress_and_confirm_cards
    from report_writing.chat.helpers import WRITING_PACE_STEP, _emit_process
    from report_writing.chat.resource_readiness import export_phase_cards, robot_export_ready
    from report_writing.services.robots import get_robot

    robot = get_robot(home, session.robot_id) if session.robot_id else None
    if _session_writing_pace(session) != WRITING_PACE_STEP:
        cards = _progress_and_confirm_cards(task, pace=_session_writing_pace(session))
        if task.chapters and all(c.status in {ChapterStatus.CONFIRMED, ChapterStatus.SKIPPED} for c in task.chapters):
            cards.extend(export_phase_cards(home, task.id, robot))
            return f"{prefix}各章都齐了，可以导出了。".strip(), cards, []
        return prefix.strip() or "好的。", cards, []

    pending = _pending_write_keys(task)
    if not pending:
        cards = _progress_and_confirm_cards(task, pace=WRITING_PACE_STEP)
        session.phase = ChatPhase.CONFIRM_EXPORT.value
        if task.chapters and all(c.status in {ChapterStatus.CONFIRMED, ChapterStatus.SKIPPED} for c in task.chapters):
            cards.extend(export_phase_cards(home, task.id, robot))
            return f"{prefix}各章都齐了，可以导出了。".strip(), cards, []
        return f"{prefix}没有待写章节了，看看进度卡片吧。".strip(), cards, []

    session.phase = ChatPhase.WRITING.value
    _emit_process(emit, f"你已确认上一章，开始写下一章「{_chapter_write_label(task, pending[0])}」…")
    writing, events = _run_writing_keys(
        home, session, task, pending[:1], emit=emit, model=model
    )
    reply, cards, events = _finish_writing_round(home, session, writing, events, pace=WRITING_PACE_STEP)
    if prefix:
        reply = f"{prefix}{reply}"
    return reply, cards, events


def _resolve_write_chapter_scope(task: WritingTask, selected: list[str]) -> list[str]:
    """校验勾选章并返回应写入 task 的 scope（不改动 outline 树）。"""
    from report_writing.chat.chapter_resolve import normalize_chapter_key_tokens
    from report_writing.chat.gates import _filter_outline_by_selected_keys

    raw = [str(k).strip() for k in (selected or []) if str(k).strip()]
    keys = normalize_chapter_key_tokens(task, raw) or raw
    if not keys:
        raise ValueError("请先勾选要写的章节")
    if task.outline is None or not list(task.outline.chapters or []):
        raise ValueError("文档副本还没有目录")
    kept = _filter_outline_by_selected_keys(list(task.outline.chapters or []), keys)
    if not any(not bool(getattr(n, "structure_only", False)) for n in kept):
        raise ValueError("没有可写作的已分析章节")
    return keys


def _writing_chapter_keys(task: WritingTask) -> list[str]:
    """按大纲顺序返回本轮应写章节 key（优先 outline，否则 task.chapters）。"""
    base: list[str] = []
    if task.outline and task.outline.chapters:
        outline_keys = [
            n.key
            for n in task.outline.chapters
            if n.key and not bool(getattr(n, "structure_only", False))
        ]
        present = {c.key for c in (task.chapters or []) if c.key}
        ordered = [k for k in outline_keys if k in present]
        if ordered:
            base = ordered
        elif outline_keys:
            base = outline_keys
    if not base:
        base = [c.key for c in (task.chapters or []) if c.key]
    scope = [str(k).strip() for k in (getattr(task, "write_chapter_scope", None) or []) if str(k).strip()]
    if not scope:
        return base
    scope_set = set(scope)
    return [k for k in base if k in scope_set]


def _apply_chapter_config(task: WritingTask, chapter_key: str, payload: ChatPayload, *, model=None) -> None:
    from report_writing.domain.models import ChatActionPayload

    payload = ChatActionPayload.parse(payload)
    from report_writing.chat.helpers import _split_lines
    if task.outline is None:
        raise ValueError("任务没有大纲，无法配置章节")
    node = next((item for item in task.outline.chapters if item.key == chapter_key), None)
    if node is None:
        raise ValueError(f"章节不存在：{chapter_key}")

    if "points" in payload or "points_text" in payload:
        raw_points = payload.get("points", payload.get("points_text"))
        node.points = _split_lines(raw_points)
    if "point_items" in payload and isinstance(payload.get("point_items"), list):
        from report_writing.domain.point_items_sync import points_from_point_items

        node.point_items = [dict(x) for x in payload["point_items"] if isinstance(x, dict)]
        if node.point_items:
            node.points = points_from_point_items(node.point_items)
    elif "points" in payload or "points_text" in payload:
        from report_writing.domain.point_items_sync import sync_point_items_from_points

        node.point_items = sync_point_items_from_points(
            list(node.points or []),
            list(getattr(node, "point_items", None) or []),
        )
    if "keywords" in payload or "keywords_text" in payload:
        raw_keywords = payload.get("keywords", payload.get("keywords_text"))
        node.keywords = _split_lines(raw_keywords)
    if "describes" in payload:
        node.describes = str(payload.get("describes") or "").strip()
    if "aux_prompt" in payload:
        node.aux_prompt = str(payload.get("aux_prompt") or "").strip()
    if "reference_text" in payload:
        node.reference_text = str(payload.get("reference_text") or "").strip()
    if "fixed_output" in payload:
        node.fixed_output = bool(payload.get("fixed_output"))
    presence_raw = str(payload.get("presence") or "").strip()
    if presence_raw:
        node.presence = presence_raw
    compose_raw = str(payload.get("compose") or "").strip()
    if compose_raw:
        node.compose = compose_raw
    from report_writing.domain.chapter_compose import (
        apply_compose_fields,
        compose_mode,
        uses_slot_reference,
    )
    from report_writing.domain.content_blocks import ensure_html_fragment

    apply_compose_fields(node)
    if uses_slot_reference(compose_mode(node)) and (node.reference_text or "").strip():
        node.reference_text = ensure_html_fragment(node.reference_text)
    if payload.get("extracts"):
        from report_writing.domain.chapter_compose import extracts_from_slots

        node.extracts = extracts_from_slots(payload.get("extracts"))
    if "search_terms_separate" in payload:
        node.search_terms_separate = bool(payload.get("search_terms_separate"))
    if "structure_only" in payload:
        node.structure_only = bool(payload.get("structure_only"))
        if node.structure_only:
            # 目录节点：清空写作配置，避免误触发解析/检索
            node.points = []
            node.point_items = []
            node.keywords = []
            node.content_blocks = []
            node.fixed_output = False
            node.presence = "always"
            node.compose = "hybrid"
            if not (node.describes or "").strip():
                node.describes = f"目录节点「{node.title or chapter_key}」，正文由子节展开，本章不单独撰写。"
    if "element_ids" in payload:
        raw_ids = payload.get("element_ids") or []
        if isinstance(raw_ids, str):
            raw_ids = _split_lines(raw_ids)
        parsed_ids = [str(item).strip() for item in raw_ids if str(item).strip()]
        # 空列表不覆盖：切会话后 chapterEdits 未 hydrate 时重试会把已关联冲掉
        if parsed_ids:
            existing = [str(x).strip() for x in (node.element_ids or []) if str(x).strip()]
            existing_set = set(existing)
            if existing and any(eid not in existing_set for eid in parsed_ids):
                from report_writing.domain.elements import union_element_ids

                node.element_ids = union_element_ids(existing, parsed_ids)
            else:
                node.element_ids = parsed_ids
        elif bool(payload.get("clear_element_ids")):
            node.element_ids = []
    if "elements" in payload:
        from report_writing.domain.elements import save_elements
        from report_writing.domain.models import ElementNode

        raw_elements = payload.get("elements")
        # 空列表不覆盖：前端未 hydrate taskElementNodes 时曾把已有要素整表清空
        if isinstance(raw_elements, list) and raw_elements:
            parsed = [
                item if isinstance(item, ElementNode) else ElementNode.model_validate(item)
                for item in raw_elements
            ]
            existing = {
                str(getattr(el, "id", "") or "").strip(): el
                for el in list(getattr(task, "element_nodes", None) or [])
                if str(getattr(el, "id", "") or "").strip()
            }
            merged: list = []
            seen: set[str] = set()
            for item in parsed:
                eid = str(getattr(item, "id", "") or "").strip()
                old = existing.get(eid)
                if old is not None:
                    if not list(getattr(item, "file_slots", None) or []) and list(
                        getattr(old, "file_slots", None) or []
                    ):
                        item.file_slots = list(old.file_slots)
                    if not list(getattr(item, "files", None) or []) and list(
                        getattr(old, "files", None) or []
                    ):
                        item.files = list(old.files)
                if eid:
                    seen.add(eid)
                merged.append(item)
            for eid, old in existing.items():
                if eid not in seen:
                    merged.append(old)
            save_elements(task, merged)
    if "title" in payload:
        title = str(payload.get("title") or "").strip()
        if title:
            node.title = title
    if "sequence" in payload:
        node.sequence = str(payload.get("sequence") or "").strip()
    if "parent" in payload or "parent_sequence" in payload:
        parent_raw = str(payload.get("parent") or payload.get("parent_key") or "").strip()
        parent_seq = str(payload.get("parent_sequence") or "").strip()
        parent_node = None
        if parent_raw or parent_seq:
            for item in task.outline.chapters:
                if parent_raw and item.key == parent_raw:
                    parent_node = item
                    break
                if (parent_raw or parent_seq) and str(item.sequence or "").strip() in {
                    parent_raw,
                    parent_seq,
                }:
                    parent_node = item
                    break
        if parent_raw == "" and parent_seq == "":
            node.parent = ""
            node.parent_sequence = ""
        elif parent_node is not None:
            node.parent = parent_node.key
            node.parent_sequence = str(parent_node.sequence or "")
            node.level = int(parent_node.level or 1) + 1
        else:
            if parent_raw:
                node.parent = parent_raw
            if parent_seq:
                node.parent_sequence = parent_seq
    if "level" in payload:
        try:
            node.level = max(1, int(payload.get("level") or node.level or 1))
        except (TypeError, ValueError):
            pass
    if "content_blocks" in payload and isinstance(payload.get("content_blocks"), list):
        from report_writing.domain.models import ContentBlock

        blocks = []
        for item in payload.get("content_blocks") or []:
            if isinstance(item, ContentBlock):
                blocks.append(item)
            elif isinstance(item, dict):
                blocks.append(ContentBlock.model_validate(item))
        node.content_blocks = blocks

    for ch in task.chapters or []:
        if ch.key == chapter_key:
            if node.title:
                ch.title = node.title
            break

    from report_writing.controller import sync_structure_only_chapter_status
    from report_writing.domain.elements import sync_brief_from_outline

    sync_brief_from_outline(task)
    if not bool(getattr(node, "structure_only", False)):
        from report_writing.domain.point_items_sync import normalize_chapter_point_items_on_node

        normalize_chapter_point_items_on_node(task, node)
    if task.brief is not None:
        try:
            req = task.brief.chapter(chapter_key)
        except KeyError:
            req = None
        if req is not None:
            req.points = list(node.points or [])
            req.generate = "\n".join(node.points or [])
            req.keywords = list(node.keywords or [])
            req.describes = (node.describes or "").strip()
            req.aux_prompt = (node.aux_prompt or "").strip()
            req.reference_text = (node.reference_text or "").strip()
            req.fixed_output = bool(node.fixed_output)
            req.structure_only = bool(node.structure_only)
            from report_writing.domain.chapter_compose import copy_compose_fields

            copy_compose_fields(req, node)
            req.search_terms_separate = bool(getattr(node, "search_terms_separate", False))
            req.knowledge_folder_ids = []
            from report_writing.domain.point_items_sync import (
                element_ids_for_chapter_node,
                elements_by_id_from_task,
            )

            req.element_ids = element_ids_for_chapter_node(
                node, elements_by_id=elements_by_id_from_task(task)
            )
    sync_structure_only_chapter_status(task)


def _apply_add_chapter_element(task: WritingTask, chapter_key: str, payload: ChatPayload):
    """为本章新增要素节点并建立关联（不落库，由调用方 save）。"""
    from report_writing.domain.models import ChatActionPayload

    payload = ChatActionPayload.parse(payload)
    from report_writing.domain.elements import new_element_id, save_elements, sync_brief_from_outline
    from report_writing.domain.models import ElementNode

    if task.outline is None:
        raise ValueError("任务没有大纲，无法添加要素")
    node = next((item for item in task.outline.chapters if item.key == chapter_key), None)
    if node is None:
        raise ValueError(f"章节不存在：{chapter_key}")

    kind = str(payload.get("kind") or "kv").strip()
    if kind == "rag_search":
        kind = "file"
    if kind not in {"kv", "fixed", "file", "file_slot", "llm_generate", "node_ref", "image"}:
        kind = "kv"
    element_id = (
        str(payload.get("element_id") or payload.get("id") or "").strip()
        or new_element_id()
    )
    from report_writing.domain.point_items_sync import human_label
    from report_writing.domain.models import KeyValueEntry, ElementAttachedFile, FileSlotDef

    default_name = "名称" if kind == "kv" else "未命名要素"
    element_name = human_label(
        payload.get("name"),
        payload.get("slot"),
        payload.get("slot_label"),
        payload.get("element_name"),
        payload.get("category"),
        default_name,
    ) or default_name

    kv_raw = payload.get("kv_pairs")
    kv_pairs: list[KeyValueEntry] = []
    if isinstance(kv_raw, list):
        for item in kv_raw:
            if isinstance(item, dict):
                kv_pairs.append(
                    KeyValueEntry(
                        key=str(item.get("key") or "").strip(),
                        value=str(item.get("value") or "").strip(),
                    )
                )
    raw_tags = payload.get("tags") if isinstance(payload.get("tags"), list) else []
    attached_files: list[ElementAttachedFile] = []
    for item in payload.get("files") or []:
        try:
            attached_files.append(
                item if isinstance(item, ElementAttachedFile) else ElementAttachedFile.model_validate(item)
            )
        except Exception:
            continue
    file_slots: list[FileSlotDef] = []
    for item in payload.get("file_slots") or []:
        try:
            file_slots.append(item if isinstance(item, FileSlotDef) else FileSlotDef.model_validate(item))
        except Exception:
            continue
    element = ElementNode(
        id=element_id,
        name=element_name,
        kind=kind,
        value=str(payload.get("value") or "").strip(),
        kv_pairs=kv_pairs,
        query=str(payload.get("query") or "").strip(),
        prompt=str(payload.get("prompt") or "").strip(),
        ref_key=str(payload.get("ref_key") or "").strip(),
        ref_kind=str(payload.get("ref_kind") or "element").strip() or "element",
        ref_field=str(payload.get("ref_field") or "result").strip() or "result",
        knowledge_folder_ids=[
            str(item).strip()
            for item in (payload.get("knowledge_folder_ids") or [])
            if str(item).strip()
        ],
        category=str(payload.get("category") or "").strip(),
        tags=[str(t).strip() for t in raw_tags if str(t).strip()],
        purpose=str(payload.get("purpose") or "").strip(),
        image_source=str(payload.get("image_source") or "upload").strip() or "upload",
        image_path=str(payload.get("image_path") or "").strip(),
        files=attached_files,
        file_slots=file_slots,
    )
    if kind == "kv" and kv_pairs:
        rows = [(p.key, p.value) for p in kv_pairs if (p.key or "").strip()]
        if rows and not (element.name or "").strip():
            element.name = rows[0][0]
        if len(rows) == 1:
            element.value = rows[0][1]
        elif rows:
            element.value = "\n".join(f"{k}：{v}" if v else k for k, v in rows)
    merged: dict[str, ElementNode] = {item.id: item for item in (task.element_nodes or []) if item.id}
    merged[element.id] = element
    save_elements(task, list(merged.values()))
    ids = list(node.element_ids or [])
    if element.id not in ids:
        ids.append(element.id)
    node.element_ids = ids
    sync_brief_from_outline(task)
    if task.brief is not None:
        try:
            req = task.brief.chapter(chapter_key)
        except KeyError:
            req = None
        if req is not None:
            req.element_ids = list(node.element_ids or [])
    _link_new_element_to_chapter_points(task, node, element, payload)
    return element


def _link_new_element_to_chapter_points(
    task: WritingTask,
    node: Any,
    element: Any,
    payload: ChatPayload,
) -> None:
    """新增要素后：写入章 element_ids 并回写 point_items 行关联。"""
    from report_writing.domain.models import ChatActionPayload

    payload = ChatActionPayload.parse(payload)
    from report_writing.domain.point_items_sync import (
        human_label,
        is_element_id_token,
        points_from_point_items,
        sync_point_items_from_points,
    )

    prev_items = [
        dict(x) for x in list(getattr(node, "point_items", None) or []) if isinstance(x, dict)
    ]
    if not prev_items:
        prev_items = sync_point_items_from_points(list(getattr(node, "points", None) or []), [])
    pi_raw = payload.get("point_index")
    try:
        point_index = int(pi_raw) if pi_raw is not None else -1
    except (TypeError, ValueError):
        point_index = -1
    slot_hint = human_label(
        payload.get("slot"),
        payload.get("slot_label"),
        payload.get("element_name"),
        getattr(element, "name", ""),
    )
    if point_index >= 0:
        while len(prev_items) <= point_index:
            prev_items.append({"id": f"p{len(prev_items)}", "text": ""})
        row = dict(prev_items[point_index])
        row["element_id"] = element.id
        ename = human_label(getattr(element, "name", ""), slot_hint, row.get("slot"))
        if ename:
            row["element_name"] = ename
        if slot_hint and not str(row.get("slot") or "").strip():
            row["slot"] = slot_hint
        row["needs_supplement"] = False
        prev_items[point_index] = row
    elif slot_hint and prev_items:
        for i, row in enumerate(prev_items):
            if str(row.get("element_id") or "").strip():
                continue
            slot = human_label(row.get("slot"), row.get("element_name")) or str(row.get("text") or "")
            if is_element_id_token(slot):
                slot = ""
            if slot == slot_hint or slot_hint in slot or slot in slot_hint:
                patched = dict(row)
                patched["element_id"] = element.id
                patched["element_name"] = human_label(
                    getattr(element, "name", ""), slot_hint, patched.get("slot")
                ) or slot_hint
                patched["needs_supplement"] = False
                prev_items[i] = patched
                break
    by_id = {item.id: item for item in (task.element_nodes or []) if getattr(item, "id", None)}
    eid = str(getattr(element, "id", "") or "").strip()
    point_linked = point_index >= 0 or any(
        str(r.get("element_id") or "").strip() == eid for r in prev_items if isinstance(r, dict)
    )
    if point_linked:
        node.point_items = prev_items
        node.points = points_from_point_items(prev_items)
    elif eid:
        ids = [str(x).strip() for x in (node.element_ids or []) if str(x).strip()]
        if eid not in ids:
            node.element_ids = [*ids, eid]
        if prev_items:
            node.point_items = prev_items
            node.points = points_from_point_items(prev_items)
    if task.brief is not None:
        try:
            req = task.brief.chapter(str(getattr(node, "key", "") or ""))
        except KeyError:
            req = None
        if req is not None:
            from report_writing.domain.point_items_sync import element_ids_for_chapter_node

            req.points = list(node.points or [])
            req.element_ids = element_ids_for_chapter_node(node, elements_by_id=by_id)


def _apply_dissociate_chapter_element(
    task: WritingTask, chapter_key: str, element_id: str
) -> str:
    """取消单章与要素的关联；保留 element_nodes 条目。"""
    ck = (chapter_key or "").strip()
    eid = (element_id or "").strip()
    if task.outline is None:
        raise ValueError("任务没有大纲")
    node = next((item for item in task.outline.chapters if item.key == ck), None)
    if node is None:
        raise ValueError(f"章节不存在：{ck}")
    ids = [str(x).strip() for x in (node.element_ids or []) if str(x).strip()]
    if eid not in ids:
        raise ValueError("该章未关联此要素")
    node.element_ids = [x for x in ids if x != eid]
    from report_writing.domain.elements import sync_brief_from_outline

    sync_brief_from_outline(task)
    if task.brief is not None:
        try:
            req = task.brief.chapter(ck)
            req.element_ids = list(node.element_ids or [])
        except KeyError:
            pass
    label = f"{node.sequence or ''} {node.title or ''}".strip() or ck
    return label


def _apply_remove_document_element(task: WritingTask, element_id: str) -> None:
    """从本文档删除要素节点并清除各章 element_ids；不调用要素库删除。"""
    eid = (element_id or "").strip()
    if not eid:
        raise ValueError("需要 element_id")
    before = list(getattr(task, "element_nodes", None) or [])
    if not any(str(getattr(el, "id", "") or "").strip() == eid for el in before):
        raise ValueError("要素不存在或已删除")
    task.element_nodes = [
        el for el in before if str(getattr(el, "id", "") or "").strip() != eid
    ]
    if task.outline is not None:
        for ch in task.outline.chapters or []:
            ids = [str(x).strip() for x in (ch.element_ids or []) if str(x).strip()]
            ch.element_ids = [x for x in ids if x != eid]
    from report_writing.domain.elements import save_elements, sync_brief_from_outline

    save_elements(task, list(task.element_nodes or []))
    sync_brief_from_outline(task)


def _prepare_export_word(home: Path, session: ChatSession) -> tuple[str, list[ChatCard]]:
    """用户要导出：引导到输入区底部下载，不在对话里塞导出卡片。"""
    from report_writing.chat.cards import _progress_and_confirm_cards
    from report_writing.chat.helpers import _load_task
    from report_writing.domain.models import ChatPhase

    if not (session.task_id or "").strip():
        return ("还没有写作任务，先选蓝本开写或确认章节后再导出。", [])
    task = _load_task(home, session.task_id)
    if task is None:
        session.task_id = ""
        return ("写作任务找不到了，请重新开写后再导出。", [])
    cards = _progress_and_confirm_cards(task, pace=_session_writing_pace(session))
    pending = sum(
        1
        for c in (task.chapters or [])
        if c.status not in {ChapterStatus.CONFIRMED, ChapterStatus.SKIPPED}
    )
    session.phase = ChatPhase.CONFIRM_EXPORT.value
    if pending:
        return (
            "可以导出 Word。请在输入框上方点「下载 Word」；"
            "若只想导出已确认的正文，打开「仅已确认」。",
            cards,
        )
    return ("可以导出了。请在输入框上方点「下载 Word」。", cards)


def writing_status_facts(task: WritingTask | None) -> dict[str, Any]:
    """给总控的进度事实，不是对用户的口头套话。"""
    if task is None:
        return {
            "inflight": [],
            "written": 0,
            "awaiting": 0,
            "pending": 0,
            "task_status": "",
        }

    def _st(ch):
        return ch.status.value if hasattr(ch.status, "value") else ch.status

    inflight = [
        _chapter_write_label(task, c.key)
        for c in (task.chapters or [])
        if _st(c)
        in {
            ChapterStatus.SEARCHING.value,
            ChapterStatus.WRITING.value,
            ChapterStatus.CHECKING.value,
        }
    ]
    awaiting = [
        c
        for c in (task.chapters or [])
        if _st(c)
        in {ChapterStatus.AWAITING_CONFIRM.value, ChapterStatus.NEEDS_HUMAN.value}
    ]
    done = [
        c
        for c in (task.chapters or [])
        if _st(c) in {ChapterStatus.CONFIRMED.value, ChapterStatus.SKIPPED.value}
    ]
    st = task.status.value if hasattr(task.status, "value") else str(task.status or "")
    return {
        "inflight": inflight,
        "written": len(awaiting) + len(done),
        "awaiting": len(awaiting),
        "pending": len(_pending_write_keys(task)),
        "task_status": st,
    }


def _writing_status(home: Path, session: ChatSession) -> tuple[str, list[ChatCard]]:
    from report_writing.chat.cards import _cards_with_export_card, _offer_start_writing, _progress_and_confirm_cards
    from report_writing.chat.helpers import _load_task
    from report_writing.chat.resource_readiness import export_phase_cards, robot_export_ready
    from report_writing.services.robots import get_robot
    if not session.task_id:
        if session.blueprint_id:
            return _offer_start_writing(session, home)
        session.phase = ChatPhase.INTENT.value
        return ("咱们还没开写。跟我说想写什么，或挑一份蓝本就行。", [])
    task = _load_task(home, session.task_id)
    if task is None:
        session.task_id = ""
        if session.blueprint_id:
            return _offer_start_writing(session, home)
        session.phase = ChatPhase.INTENT.value
        return ("这份写作任务找不到了，咱们重新选个蓝本，或再说说需求吧。", [])
    robot = get_robot(home, session.robot_id) if session.robot_id else None
    task.refresh_status()
    sync_session_phase_from_task(session, task)
    cards = _progress_and_confirm_cards(task, pace=_session_writing_pace(session))
    if task.chapters and all(c.status in {ChapterStatus.CONFIRMED, ChapterStatus.SKIPPED} for c in task.chapters):
        cards.extend(export_phase_cards(home, task.id, robot))
        session.phase = ChatPhase.CONFIRM_EXPORT.value
    # 不对用户写死套话：进度数据在 cards / writing_status_facts，口头由总控组织
    return "", cards


def clone_blueprint_to_document_copy(
    home: Path,
    blueprint_id: str,
    *,
    document_title: str = "",
    chapter_keys: list[str] | None = None,
    project_folder_ids: list[str] | None = None,
    project_folder_label: str = "",
    require_outline: bool = False,
    prepare: bool = True,
) -> WritingTask:
    """从库蓝本建「一文档一副本」（w*）。对话内变更只打副本，勿写回库除非显式 sync。

    require_outline=False：允许空壳蓝本（新建向导 meta 后立刻建副本）。
    """
    from report_writing.chat.gates import is_blueprint_valid
    from report_writing.chat.helpers import _load_task

    source = _load_task(home, blueprint_id)
    if source is None:
        raise ValueError(f"蓝本不存在：{blueprint_id}")
    if require_outline and not is_blueprint_valid(source):
        raise ValueError("蓝本无效，无法开写")
    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    task_id = f"w{stamp}{secrets.token_hex(2)}"
    dest = home / "tasks" / task_id
    ensure_task(dest, biz_type=source.biz_type or "002")
    data = source.to_dict()
    data["id"] = task_id
    tip = (document_title or "").strip() or (source.title or blueprint_id)
    data["title"] = tip
    data["description"] = f"文档工作副本（源蓝本 {blueprint_id}）"
    data["source_blueprint_id"] = blueprint_id
    # 重置运行态，保留大纲与要点（空壳则无目录）
    data["steps"] = []
    data["status"] = "studying"
    data["chapters"] = []
    data["brief"] = None
    data["brief_confirmed"] = False
    cloned = WritingTask.from_dict(data)
    if cloned.outline is not None:
        cloned.outline = type(cloned.outline).from_dict(copy.deepcopy(cloned.outline.to_dict()))
        if chapter_keys is not None:
            cloned.write_chapter_scope = _resolve_write_chapter_scope(cloned, chapter_keys)
    from report_writing.domain.elements import strip_project_scoped_folders_on_clone

    strip_project_scoped_folders_on_clone(cloned)
    # 资料包按文档绑定：副本默认未绑，不继承库蓝本
    cloned.materials_pack_id = ""
    folders = [str(x).strip() for x in (project_folder_ids or []) if str(x).strip()]
    if folders:
        cloned.project_folder_ids = folders
        cloned.project_folder_label = (project_folder_label or "").strip()
    TaskStore(dest).save(cloned)
    try:
        from report_writing.services.writing_task_catalog import save_writing_task

        save_writing_task(cloned)
    except Exception:
        pass
    if (
        prepare
        and not cloned.outline_confirmed
        and cloned.outline
        and cloned.outline.chapters
    ):
        confirm_outline(dest)
        cloned = _load_task(home, task_id) or cloned
        return _prepare_writing_task(home, cloned)
    return cloned


def clone_blueprint_to_writing_task(
    home: Path,
    blueprint_id: str,
    *,
    chapter_keys: list[str] | None = None,
    project_folder_ids: list[str] | None = None,
    project_folder_label: str = "",
) -> WritingTask:
    """开写时从库蓝本建写作任务（要求已有目录）。优先改用已有文档副本。"""
    return clone_blueprint_to_document_copy(
        home,
        blueprint_id,
        chapter_keys=chapter_keys,
        project_folder_ids=project_folder_ids,
        project_folder_label=project_folder_label,
        require_outline=True,
        prepare=True,
    )


def _outline_chapter_count(task: WritingTask | None) -> int:
    if task is None or task.outline is None:
        return 0
    return len(list(task.outline.chapters or []))


def _save_writing_copy(home: Path, task: WritingTask) -> None:
    tid = str(getattr(task, "id", "") or "").strip()
    if not tid:
        return
    TaskStore(home / "tasks" / tid).save(task)
    try:
        from report_writing.services.writing_task_catalog import save_writing_task

        save_writing_task(task)
    except Exception:
        pass
    try:
        from report_writing.services.doc_outline_cache import invalidate_doc_outline_cache

        invalidate_doc_outline_cache(tid)
    except Exception:
        pass


def _copy_sample_ref_from_source(copy: WritingTask, source: WritingTask) -> bool:
    """把源蓝本样本元数据抄到副本上；副本自己已有有效样本文件时不覆盖。"""
    if copy is None or source is None:
        return False
    if Path(str(getattr(copy, "sample_path", "") or "")).is_file():
        return False
    changed = False
    src_path = str(getattr(source, "sample_path", "") or "")
    if src_path != str(getattr(copy, "sample_path", "") or ""):
        copy.sample_path = src_path
        changed = True
    src_name = str(getattr(source, "sample_name", "") or "")
    if src_name != str(getattr(copy, "sample_name", "") or ""):
        copy.sample_name = src_name
        changed = True
    src_chars = int(getattr(source, "sample_chars", 0) or 0)
    if src_chars != int(getattr(copy, "sample_chars", 0) or 0):
        copy.sample_chars = src_chars
        changed = True
    src_ready = bool(getattr(source, "sample_ready", False)) or bool(src_path)
    if bool(getattr(copy, "sample_ready", False)) != src_ready:
        copy.sample_ready = src_ready
        changed = True
    return changed


def _merge_missing_elements_from_source(copy: WritingTask, source: WritingTask) -> bool:
    """蓝本有、副本缺的要素节点/章关联补上；已有关联不覆盖。"""
    from copy import deepcopy

    from report_writing.domain.elements import union_element_ids

    changed = False
    if copy is None or source is None:
        return False
    have = {
        str(getattr(item, "id", "") or "").strip(): item
        for item in list(getattr(copy, "element_nodes", None) or [])
        if str(getattr(item, "id", "") or "").strip()
    }
    extra = []
    for item in list(getattr(source, "element_nodes", None) or []):
        eid = str(getattr(item, "id", "") or "").strip()
        if not eid or eid in have:
            continue
        extra.append(item.model_copy(deep=True) if hasattr(item, "model_copy") else deepcopy(item))
        have[eid] = extra[-1]
    if extra:
        copy.element_nodes = list(getattr(copy, "element_nodes", None) or []) + extra
        changed = True
    src_chapters = list(getattr(getattr(source, "outline", None), "chapters", None) or [])
    dst_chapters = list(getattr(getattr(copy, "outline", None), "chapters", None) or [])
    if src_chapters and dst_chapters:
        src_by = {str(n.key or "").strip(): n for n in src_chapters if str(n.key or "").strip()}
        for node in dst_chapters:
            src = src_by.get(str(node.key or "").strip())
            if src is None:
                continue
            dst_ids = [str(x).strip() for x in (node.element_ids or []) if str(x).strip()]
            if dst_ids:
                continue
            src_ids = union_element_ids(getattr(src, "element_ids", None))
            if src_ids:
                node.element_ids = src_ids
                changed = True
    return changed


def hydrate_empty_copy_from_blueprint(
    home: Path,
    copy: WritingTask,
    blueprint_id: str,
) -> WritingTask:
    """空壳副本选用蓝本时，把库蓝本目录抄到这份已定名文档上。已有目录不覆盖。"""
    from report_writing.chat.helpers import _load_task

    from copy import deepcopy

    bid = (blueprint_id or "").strip()
    if not bid or copy is None:
        return copy
    source = _load_task(home, bid)
    changed = False
    if not str(getattr(copy, "source_blueprint_id", "") or "").strip():
        copy.source_blueprint_id = bid
        changed = True
    if source is not None:
        changed = _copy_sample_ref_from_source(copy, source) or changed
        changed = _merge_missing_elements_from_source(copy, source) or changed
    # 副本上已经有章节：保留用户改过的目录，不拿库蓝本盖回去；样本引用仍可补上
    if _outline_chapter_count(copy) > 0:
        if changed:
            _save_writing_copy(home, copy)
        return copy
    if _outline_chapter_count(source) <= 0:
        if changed:
            _save_writing_copy(home, copy)
        return copy
    copy.outline = type(source.outline).from_dict(deepcopy(source.outline.to_dict()))
    copy.outline_confirmed = bool(getattr(source, "outline_confirmed", False))
    copy.sample_ready = bool(getattr(source, "sample_ready", False)) or bool(
        getattr(copy, "sample_path", "") or ""
    )
    copy.source_blueprint_id = bid
    if not list(getattr(copy, "element_nodes", None) or []):
        nodes = []
        for item in list(getattr(source, "element_nodes", None) or []):
            if hasattr(item, "model_copy"):
                nodes.append(item.model_copy(deep=True))
            else:
                nodes.append(deepcopy(item))
        copy.element_nodes = nodes
    from report_writing.domain.elements import strip_project_scoped_folders_on_clone

    strip_project_scoped_folders_on_clone(copy)
    _save_writing_copy(home, copy)
    return _load_task(home, str(copy.id or "")) or copy


def ensure_session_document_copy(
    home: Path,
    session: ChatSession,
    blueprint_id: str,
    *,
    document_title: str = "",
    reuse_writing_task_id: str | None = None,
    registry_key: str = "",
    require_outline: bool = False,
) -> WritingTask:
    """确保当前会话文档已有 w* 副本并登记；已有且可加载则复用。"""
    from report_writing.chat.helpers import _load_task
    from report_writing.chat.session_documents import (
        focus_document_key,
        session_documents_map,
        set_focus_document_key,
        upsert_session_document,
    )
    from report_writing.services.task_ids import is_writing_task_id

    bid = (blueprint_id or "").strip()
    if not bid:
        raise ValueError("需要 blueprint_id")
    wid = (reuse_writing_task_id or "").strip()
    if not wid:
        # 只用登记表里写明的 blueprint_id。
        # focus_document_meta 会把空蓝本回落成 session.blueprint_id，
        # 选用刚把会话蓝本设上时，会把「尚未绑库」的空壳误当成这份蓝本的副本。
        key = focus_document_key(session)
        raw = dict((session_documents_map(session).get(key) or {}) if key else {})
        raw_bid = str(raw.get("blueprint_id") or "").strip()
        focus_wid = str(raw.get("writing_task_id") or raw.get("task_id") or "").strip()
        if focus_wid and is_writing_task_id(focus_wid):
            if raw_bid == bid:
                wid = focus_wid
            elif not raw_bid:
                bound = _load_task(home, focus_wid)
                src = str(getattr(bound, "source_blueprint_id", "") or "").strip() if bound else ""
                # 刚定名、还没有目录的文档：就绑这一份，下面把蓝本目录抄进来
                if bound is not None and not src and _outline_chapter_count(bound) == 0:
                    wid = focus_wid
    if not wid:
        sess_tid = str(getattr(session, "task_id", "") or "").strip()
        if sess_tid and is_writing_task_id(sess_tid):
            existing = _load_task(home, sess_tid)
            if existing is not None and str(
                getattr(existing, "source_blueprint_id", "") or ""
            ).strip() == bid:
                wid = sess_tid
    if wid and is_writing_task_id(wid):
        existing = _load_task(home, wid)
        if existing is not None:
            existing = hydrate_empty_copy_from_blueprint(home, existing, bid)
            wid = str(existing.id or wid)
            session.blueprint_id = bid
            session.task_id = wid
            tip = (document_title or "").strip()
            upsert_session_document(
                session,
                kind="blueprint",
                task_id=bid,
                blueprint_id=bid,
                writing_task_id=wid,
                title=tip or str(getattr(existing, "title", "") or ""),
                registry_key=registry_key,
            )
            if registry_key:
                set_focus_document_key(session, registry_key)
            return existing

    tip = (document_title or "").strip()
    cloned = clone_blueprint_to_document_copy(
        home,
        bid,
        document_title=tip,
        require_outline=require_outline,
        prepare=False,
    )
    session.blueprint_id = bid
    session.task_id = cloned.id
    upsert_session_document(
        session,
        kind="blueprint",
        task_id=bid,
        blueprint_id=bid,
        writing_task_id=cloned.id,
        title=tip or str(getattr(cloned, "title", "") or ""),
        registry_key=registry_key,
    )
    if registry_key:
        set_focus_document_key(session, registry_key)
    else:
        set_focus_document_key(session, f"bp:{bid}")
    return cloned


def apply_write_chapter_filter(
    home: Path,
    writing: WritingTask,
    chapter_keys: list[str],
) -> WritingTask:
    """在已有文档副本上记录本轮写作 scope（不裁剪 outline，文档窗仍显示完整目录）。"""
    writing.write_chapter_scope = _resolve_write_chapter_scope(writing, chapter_keys)
    path = home / "tasks" / writing.id
    TaskStore(path).save(writing)
    return _prepare_writing_task(home, writing)


def sync_document_copy_to_library(
    home: Path,
    writing_task_id: str,
) -> tuple[str, WritingTask]:
    """显式同步：把文档副本的大纲/要点/要素写回源库蓝本（唯一允许写库的路径）。"""
    from report_writing.chat.helpers import _load_task
    from report_writing.services.blueprint_catalog import save_blueprint_task
    from report_writing.services.task_ids import is_writing_task_id

    tid = (writing_task_id or "").strip()
    if not tid or not is_writing_task_id(tid):
        raise ValueError("需要文档写作副本 id")
    writing = _load_task(home, tid)
    if writing is None:
        raise ValueError("写作副本不存在")
    bid = str(getattr(writing, "source_blueprint_id", "") or "").strip()
    if not bid:
        raise ValueError("该副本没有源蓝本，无法同步到蓝本库")
    library = _load_task(home, bid)
    if library is None:
        raise ValueError(f"源蓝本不存在：{bid}")

    data = library.to_dict()
    src = writing.to_dict()
    # 模板内容：大纲 / 要素 / 样本元信息；不覆盖库 id、不带写作运行态
    for key in (
        "outline",
        "outline_confirmed",
        "element_nodes",
        "sample_ready",
        "sample_name",
        "sample_chars",
        "sample_path",
        "category",
        "biz_type",
    ):
        if key in src:
            data[key] = copy.deepcopy(src.get(key))
    # 库标题保持库侧名称；描述可注明已同步
    data["description"] = (
        (library.description or "").strip()
        or f"已从文档副本 {tid} 同步"
    )
    data["steps"] = []
    data["status"] = "studying"
    data["chapters"] = []
    data["brief"] = None
    data["brief_confirmed"] = False
    data["materials_pack_id"] = str(getattr(library, "materials_pack_id", "") or "")
    data["project_folder_ids"] = list(getattr(library, "project_folder_ids", None) or [])
    updated = WritingTask.from_dict(data)
    bp_dir = home / "tasks" / bid
    TaskStore(bp_dir).save(updated)
    if updated.outline and updated.outline.chapters and not updated.outline_confirmed:
        confirm_outline(bp_dir)
        updated = TaskStore(bp_dir).load()
    save_blueprint_task(updated)
    return bid, updated


def _prepare_writing_task(home: Path, task: WritingTask) -> WritingTask:
    from report_writing.controller import _ensure_brief_ready, _ensure_outline_template

    path = home / "tasks" / task.id
    store = TaskStore(path)
    task = store.load()
    if task.outline and not task.outline_confirmed:
        confirm_outline(path)
        task = store.load()
    _ensure_brief_ready(task)
    _ensure_outline_template(task, path)
    store.save(task)
    return task


