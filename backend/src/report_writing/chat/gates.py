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
    ChatCard,
    ChatMessage,
    ChatPhase,
    ChatRequest,
    ChatResponse,
    ChatSession,
    HumanDecision,
    OutlineNode,
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

def is_chapter_analyzed(node) -> bool:
    """章节是否已完成分析配置（可进入写作）。纯目录节点视为无需分析。"""
    if node is None:
        return False
    if bool(getattr(node, "structure_only", False)):
        return True
    if bool(getattr(node, "fixed_output", False)) and (getattr(node, "reference_text", None) or "").strip():
        return True
    if (node.points or []) or (node.keywords or []):
        return True
    if (getattr(node, "describes", None) or "").strip():
        return True
    if (getattr(node, "reference_text", None) or "").strip():
        return True
    if getattr(node, "content_blocks", None):
        return True
    return False


def partition_blueprint_chapters(task: WritingTask | None) -> dict[str, list]:
    """拆分蓝本章节：已分析 / 未分析（需写作） / 纯目录。"""
    chapters = list(task.outline.chapters or []) if task and task.outline else []
    analyzed: list = []
    unanalyzed: list = []
    structure: list = []
    for node in chapters:
        if bool(getattr(node, "structure_only", False)):
            structure.append(node)
        elif is_chapter_analyzed(node):
            analyzed.append(node)
        else:
            unanalyzed.append(node)
    return {"analyzed": analyzed, "unanalyzed": unanalyzed, "structure": structure}


def is_blueprint_valid(task: WritingTask | None) -> bool:
    """蓝本是否具备可用目录（有章节即可；确认目录与学习在向导/蓝本页继续）。"""
    if task is None or task.outline is None:
        return False
    chapters = list(task.outline.chapters or [])
    return bool(chapters)


def _filter_outline_by_selected_keys(chapters: list, selected_keys: list[str]) -> list:
    """保留勾选章及其祖先节点（含纯目录），不把未选的兄弟章带上。"""
    keyset = {str(k).strip() for k in selected_keys if str(k).strip()}
    if not keyset:
        return []
    by_key = {str(getattr(n, "key", "") or ""): n for n in chapters if getattr(n, "key", None)}
    keep: set[str] = set()
    for key in keyset:
        cur = key
        seen: set[str] = set()
        while cur and cur in by_key and cur not in seen:
            keep.add(cur)
            seen.add(cur)
            cur = str(getattr(by_key[cur], "parent", "") or "").strip()
    return [n for n in chapters if str(getattr(n, "key", "") or "") in keep]


def _analysis_gate_if_needed(
    session: ChatSession,
    task: WritingTask,
    *,
    set_phase: bool = True,
) -> tuple[str, list[ChatCard]] | None:
    """若存在未分析的可写章节，提示去文档小窗分析/选章（不下发目录树）。"""
    from report_writing.chat.cards import _doc_window_hint_card
    from report_writing.chat.helpers import _chapter_label, _join_labels

    parts = partition_blueprint_chapters(task)
    unanalyzed = parts["unanalyzed"]
    analyzed = parts["analyzed"]
    if not unanalyzed:
        return None
    source_bp = str(getattr(task, "source_blueprint_id", "") or "").strip()
    session_bp = str(session.blueprint_id or "").strip()
    tid = str(task.id or "").strip()
    if session_bp and not session_bp.lower().startswith("w"):
        blueprint_id = session_bp
    elif source_bp and not source_bp.lower().startswith("w"):
        blueprint_id = source_bp
    elif tid and not tid.lower().startswith("w"):
        blueprint_id = tid
    else:
        blueprint_id = session_bp or source_bp or tid
    title = task.title or blueprint_id
    un_labels = [_chapter_label(n) for n in unanalyzed[:12]]
    label_text = _join_labels(un_labels) + ("…" if len(unanalyzed) > 8 else "")
    if set_phase:
        session.phase = ChatPhase.CREATE_HANDOFF.value
    if not analyzed:
        hint = f"还有 {len(unanalyzed)} 章未分析（{label_text}），请在右侧文档窗口完成分析。"
        return (
            f"「{title}」里还有 {len(unanalyzed)} 章没分析呢（{label_text}）。"
            "请在右侧文档窗口打开目录、完成分析后再开写。",
            [
                _doc_window_hint_card(
                    session,
                    task_id=blueprint_id,
                    blueprint_id=blueprint_id,
                    title="打开文档窗口分析",
                    hint=hint,
                    action="analyze",
                )
            ],
        )
    hint = (
        f"已分析 {len(analyzed)} 章、未分析 {len(unanalyzed)} 章（{label_text}）。"
        "请在右侧文档窗口查看目录并选择要写的章节。"
    )
    return (
        f"「{title}」有 {len(unanalyzed)} 章还没分析（{label_text}），"
        f"已经分析好的有 {len(analyzed)} 章。"
        "请在右侧文档窗口补分析，或先选已分析章节开写。",
        [
            _doc_window_hint_card(
                session,
                task_id=blueprint_id,
                blueprint_id=blueprint_id,
                title="打开文档窗口选章",
                hint=hint,
                action="select_write",
            )
        ],
    )


def _ensure_analysis_choice_cards(
    home: Path,
    session: ChatSession,
    cards: list[ChatCard],
) -> list[ChatCard]:
    """会话不再夹带章节目录树；需要时只补一张打开文档窗提示。"""
    from report_writing.chat.cards import _doc_window_hint_card
    from report_writing.chat.helpers import _load_task

    strip_types = {"chapter_picker", "analysis_gate", "write_analyzed_only", "handoff"}
    types = {getattr(c, "type", "") for c in (cards or [])}
    if "element_configure_pending" in types or "blueprint_wizard" in types:
        return [
            c
            for c in (cards or [])
            if getattr(c, "type", "") not in {"chapter_picker", "analysis_gate"}
        ]
    # 查看要素/资料库：本回合只展示资源列表
    if "nav_action" in types:
        return [c for c in (cards or []) if getattr(c, "type", "") not in strip_types]
    out = [c for c in (cards or []) if getattr(c, "type", "") not in {"chapter_picker", "analysis_gate"}]
    if list((session.freeform or {}).get("write_chapter_keys") or []):
        return out
    if (session.phase or "") in {
        ChatPhase.READY_TO_WRITE.value,
        ChatPhase.WRITING.value,
        ChatPhase.CONFIRM_EXPORT.value,
    }:
        return out
    blueprint_id = (session.blueprint_id or "").strip()
    if not blueprint_id:
        return out
    task = _load_task(home, blueprint_id)
    if task is None or not is_blueprint_valid(task):
        return out
    gate = _analysis_gate_if_needed(session, task, set_phase=False)
    if gate is None:
        return out
    _reply, gate_cards = gate
    if any(getattr(c, "type", "") == "doc_window_hint" for c in out):
        return out
    # 去掉旧 handoff，改用轻量小窗提示
    cleaned = [c for c in out if getattr(c, "type", "") not in {"handoff"}]
    return cleaned + list(gate_cards or [_doc_window_hint_card(session, blueprint_id=blueprint_id)])


def _parse_chapter_keys(payload: ChatActionPayload | dict | None) -> list[str]:
    from report_writing.domain.models import ChatActionPayload

    payload = ChatActionPayload.parse(payload)
    raw = payload.get("chapter_keys")
    if raw is None:
        raw = payload.get("write_chapter_keys")
    if isinstance(raw, str):
        text = raw.strip()
        if text.startswith("["):
            try:
                raw = json.loads(text)
            except json.JSONDecodeError:
                raw = [part.strip() for part in re.split(r"[,，\s]+", text) if part.strip()]
        else:
            raw = [part.strip() for part in re.split(r"[,，\s]+", text) if part.strip()]
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        key = str(item or "").strip()
        if key and key not in seen:
            seen.add(key)
            out.append(key)
    return out


