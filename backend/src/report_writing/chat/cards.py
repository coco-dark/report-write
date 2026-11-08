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
    MAX_REVISIONS,
    WRITE_SCORE_PASS,
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


def stamp_chat_card(card: ChatCard) -> ChatCard:
    if not (getattr(card, "card_id", "") or "").strip():
        card.card_id = f"cc{secrets.token_hex(6)}"
    return card


def turn_has_outline_preview_cards(cards: list[Any] | None = None) -> bool:
    return any(
        str(getattr(c, "type", "") or "") == "outline_preview" for c in (cards or [])
    )


def collapse_duplicate_turn_cards(
    cards: list[Any], session: Any = None
) -> list[Any]:
    """同一回合去重：书写方式/跳转蓝本各一张；选蓝本阶段只保留一套；定名卡只保留一张。"""
    seen: set[str] = set()
    out: list[Any] = []
    seen_clarify_source: set[str] = set()
    outline_last: dict[str, Any] = {}
    outline_keys: list[str] = []
    picking = turn_has_blueprint_pick_cards(cards, session=session)
    defer_name = turn_should_defer_doc_name_card(cards, session=session)
    for card in cards or []:
        kind = str(getattr(card, "type", "") or "")
        payload = getattr(card, "payload", None) or {}
        if kind == "outline_preview":
            tid = str(
                payload.get("task_id") or payload.get("blueprint_id") or ""
            ).strip() or "_"
            if tid not in outline_last:
                outline_keys.append(tid)
            outline_last[tid] = card
            continue
        if kind in {"writing_pace", "handoff"}:
            if kind in seen:
                continue
            seen.add(kind)
        if kind == "intent_clarify":
            src = str(payload.get("source") or "").strip()
            if src == "doc_name":
                if "doc_name" in seen_clarify_source:
                    continue
                seen_clarify_source.add("doc_name")
            if picking and src in {"doc_name", "entry_branch", "entry_intent"}:
                continue
            if defer_name and src == "doc_name":
                continue
            if src == "entry_branch" and "entry_branch" in seen_clarify_source:
                continue
            if src:
                seen_clarify_source.add(src)
        if picking and kind == "skip_blueprint" and any(
            str(getattr(x, "type", "") or "") == "skip_blueprint" for x in out
        ):
            continue
        out.append(card)
    for tid in outline_keys:
        out.append(outline_last[tid])
    return out


def turn_has_blueprint_pick_cards(cards: list[Any] | None = None, session: Any = None) -> bool:
    """本回合已在选库蓝本（match_first：此阶段勿再叠定名/入口三选卡）。"""
    from report_writing.domain.models import ChatPhase

    if session is not None:
        phase = str(getattr(session, "phase", "") or "").strip()
        bid = str(getattr(session, "blueprint_id", "") or "").strip()
        if phase == ChatPhase.BLUEPRINT_SELECT.value and not bid:
            return True
    for card in cards or []:
        kind = str(getattr(card, "type", "") or "")
        if kind in {"blueprint_list", "skip_blueprint"}:
            return True
        payload = getattr(card, "payload", None) or {}
        if kind != "intent_clarify":
            continue
        src = str(payload.get("source") or "").strip()
        cand = payload.get("candidate") if isinstance(payload.get("candidate"), dict) else {}
        cname = str((cand or {}).get("name") or "").strip()
        if src == "action_slots" and cname == "select_blueprint":
            return True
        q = str(payload.get("question") or "").strip()
        # 选「哪一份蓝本」，不是入口「走哪条路」
        if ("哪份" in q or "哪一本" in q) and "蓝本" in q:
            return True
    return False


_DEFER_DOC_NAME_CARD_TYPES = frozenset(
    {
        "blueprint_list",
        "skip_blueprint",
        "doc_window_hint",
        "materials_pack_gate",
        "blueprint_wizard",
        "chapter_picker",
        "handoff",
        "progress",
    }
)


def turn_should_defer_doc_name_card(
    cards: list[Any] | None = None, session: Any = None
) -> bool:
    """同屏已有选章/资料包/向导等主流程卡时，勿再叠定名澄清卡（选用后再起名）。"""
    if turn_has_blueprint_pick_cards(cards, session=session):
        return True
    for card in cards or []:
        if str(getattr(card, "type", "") or "") in _DEFER_DOC_NAME_CARD_TYPES:
            return True
    return False


InvokeFn = Callable[[str, str], str]
EmitFn = Callable[[dict[str, Any]], None]
StreamModelFn = Callable[[str, str], Iterable[str]]

def _outline_chapter_picker_items(task: WritingTask) -> list[dict[str, Any]]:
    """整棵大纲进勾选树：可写已分析章可选；纯目录作父节点；未分析禁用。"""
    from report_writing.chat.gates import partition_blueprint_chapters
    parts = partition_blueprint_chapters(task)
    analyzed_keys = {n.key for n in parts["analyzed"]}
    unanalyzed_keys = {n.key for n in parts["unanalyzed"]}
    rows: list[dict[str, Any]] = []
    for node in list(task.outline.chapters or []) if task.outline else []:
        key = str(getattr(node, "key", "") or "").strip()
        if not key:
            continue
        structure_only = bool(getattr(node, "structure_only", False))
        selectable = (not structure_only) and key in analyzed_keys
        rows.append(
            {
                "key": key,
                "sequence": str(getattr(node, "sequence", "") or ""),
                "title": str(getattr(node, "title", "") or key),
                "parent": str(getattr(node, "parent", "") or ""),
                "structure_only": structure_only,
                "analyzed": key in analyzed_keys or structure_only,
                "unanalyzed": key in unanalyzed_keys,
                "selectable": selectable,
            }
        )
    return rows


def _chapter_picker_payload(session: ChatSession, task: WritingTask) -> dict[str, Any]:
    """对话卡片只带元数据；章节树由前端组件按 blueprint_id 懒加载，避免 SSE/会话被 200+ 章撑死。"""
    from report_writing.chat.gates import partition_blueprint_chapters
    from report_writing.chat.helpers import _chapter_label
    parts = partition_blueprint_chapters(task)
    analyzed = parts["analyzed"]
    unanalyzed = parts["unanalyzed"]
    # 库蓝本源：勿用写作副本 task.id(w*) 冒充 blueprint_id
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
    un_labels = [_chapter_label(n) for n in unanalyzed[:12]]
    return {
        "blueprint_id": blueprint_id,
        "analyzed_count": len(analyzed),
        "unanalyzed_count": len(unanalyzed),
        "unanalyzed_titles": un_labels,
        "chapters_lazy": True,
        "default_selected_keys": [],
    }


def _doc_window_hint_card(
    session: ChatSession,
    *,
    task_id: str = "",
    blueprint_id: str = "",
    title: str = "在文档窗口继续",
    hint: str = "",
    action: str = "open_doc_window",
    auto_open: bool = True,
) -> ChatCard:
    """轻量引导打开文档小窗；不下发章节树。"""
    bid = str(blueprint_id or session.blueprint_id or "").strip()
    tid = str(task_id or session.task_id or bid or "").strip()
    return ChatCard(
        type="doc_window_hint",
        title=title,
        payload={
            "task_id": tid,
            "blueprint_id": bid,
            "hint": str(hint or "").strip(),
            "action": str(action or "open_doc_window").strip() or "open_doc_window",
            "auto_open": bool(auto_open),
        },
    )


def _offer_chapter_picker_cards(session: ChatSession, task: WritingTask) -> list[ChatCard]:
    """选章/分析改由文档小窗承接；会话只给轻量打开提示。"""
    payload = _chapter_picker_payload(session, task)
    if int(payload.get("analyzed_count") or 0) <= 0 and not payload.get("chapters"):
        # 全未分析：仍引导去小窗做分析配置
        analyzed = int(payload.get("analyzed_count") or 0)
        unanalyzed = int(payload.get("unanalyzed_count") or 0)
        if unanalyzed <= 0 and analyzed <= 0:
            return []
    bid = str(payload.get("blueprint_id") or session.blueprint_id or getattr(task, "id", "") or "")
    analyzed = int(payload.get("analyzed_count") or 0)
    unanalyzed = int(payload.get("unanalyzed_count") or 0)
    if analyzed <= 0:
        hint = f"还有 {unanalyzed} 章未分析，请在右侧文档窗口查看目录并完成分析。"
        title = "打开文档窗口分析"
        action = "analyze"
    else:
        hint = (
            f"已分析 {analyzed} 章、未分析 {unanalyzed} 章。"
            "请在右侧文档窗口查看目录，选择要写的章节。"
        )
        title = "打开文档窗口选章"
        action = "select_write"
    return [
        _doc_window_hint_card(
            session,
            task_id=bid,
            blueprint_id=bid,
            title=title,
            hint=hint,
            action=action,
            auto_open=True,
        )
    ]


def _offer_writing_pace_cards(session: ChatSession, *, extra: dict[str, Any] | None = None) -> list[ChatCard]:
    """兼容旧调用名：不再出节奏二选一，改为「开始写作」(batch 依次写、章间不确认)。"""
    from report_writing.chat.helpers import WRITING_PACE_BATCH

    payload = ChatActionPayload.parse(extra)
    updates: dict[str, Any] = {}
    if session.blueprint_id and "blueprint_id" not in payload:
        updates["blueprint_id"] = session.blueprint_id
    if session.task_id and "task_id" not in payload:
        updates["task_id"] = session.task_id
    keys = list((session.freeform or {}).get("write_chapter_keys") or [])
    if keys and "write_chapter_keys" not in payload:
        updates["write_chapter_keys"] = keys
        updates["analyzed_only"] = True
        if "chapter_count" not in payload:
            updates["chapter_count"] = len(keys)
    updates["writing_pace"] = WRITING_PACE_BATCH
    payload = payload.with_updates(**updates)
    return [
        ChatCard(
            type="start_writing",
            title="开始写作",
            payload=payload,
        )
    ]


def _offer_start_writing(session: ChatSession, home: Path | None = None) -> tuple[str, list[ChatCard]]:
    from report_writing.chat.gates import is_blueprint_valid
    from report_writing.chat.helpers import _load_task
    session.phase = ChatPhase.READY_TO_WRITE.value
    name = session.blueprint_id or session.task_id or "当前文档"
    keys = list((session.freeform or {}).get("write_chapter_keys") or [])
    if not keys and home is not None and session.blueprint_id:
        task = _load_task(home, session.blueprint_id)
        if task is not None and is_blueprint_valid(task):
            return (
                f"「{name}」这边已经就绪了。请在右侧文档窗口勾选要写的章节，点「写作」即可依次开写。",
                _offer_chapter_picker_cards(session, task),
            )
    if keys:
        n = len(keys)
        tip = f"「{name}」已选好 {n} 章。" if n > 1 else f"「{name}」就写这一章。"
        return (
            f"{tip}章节请在右侧文档窗口查看，将按勾选顺序依次书写。",
            [],
        )
    return (
        f"「{name}」这边已经就绪了。请在右侧文档窗口勾选章节后点「写作」。",
        [_skip_blueprint_card()] if not session.blueprint_id else [],
    )


def _blueprint_list_card(items) -> ChatCard:
    rows = [
        {
            "id": item.id,
            "title": item.title,
            "category": item.category,
            "tags": list(item.tags or []),
            "chapter_count": item.chapter_count,
            "outline_confirmed": item.outline_confirmed,
            "description": item.description,
            "enabled": bool(getattr(item, "enabled", True)),
        }
        for item in items
    ]
    return ChatCard(
        type="blueprint_list",
        title="请选择蓝本" if len(rows) > 1 else "点击选用蓝本",
        payload={
            "interactive": True,
            "layout": "table" if len(rows) > 1 else "list",
            "hint": (
                "匹配到多份启用中的蓝本，请点选确认用哪一份；也可改为无蓝本（依据模版或自动大纲）"
                if len(rows) > 1
                else "请点「选用」确认是否使用该蓝本；也可改为无蓝本（依据模版或自动大纲）"
            ),
            "items": rows,
        },
    )


def _skip_blueprint_card() -> ChatCard:
    return ChatCard(type="skip_blueprint", title="跳过蓝本", payload={})


def _handoff_card(session: ChatSession, *, blueprint_id: str = "", title: str = "去配置蓝本") -> ChatCard:
    return ChatCard(
        type="handoff",
        title=title,
        payload={
            "session_id": session.id,
            "blueprint_id": blueprint_id or session.blueprint_id or "",
            "path": "/",
            "query": {
                "from": "chat",
                "session_id": session.id,
                "blueprint_id": blueprint_id or session.blueprint_id or "",
            },
        },
    )


def _nav_action_card(
    *,
    target: str,
    title: str,
    hint: str = "",
    items: list[dict] | None = None,
    total: int | None = None,
    empty_hint: str = "",
    sections: list[dict] | None = None,
) -> ChatCard:
    """浏览/跳转：elements=要素库，materials=资料库，blueprints=蓝本库；items/sections 为卡片内列表摘要。"""
    tid = str(target or "").strip()
    action = ""
    if tid == "elements":
        action = "open_element_library"
    elif tid in {"blueprints", "blueprint"}:
        action = "open_blueprint_library"
        tid = "blueprints"
    elif tid in {"materials", "knowledge"}:
        action = "open_materials_library" if tid == "materials" else "open_knowledge"
        tid = "materials"
    rows = [dict(x) for x in (items or []) if isinstance(x, dict)]
    section_rows: list[dict] = []
    for sec in sections or []:
        if not isinstance(sec, dict):
            continue
        sec_items = [dict(x) for x in (sec.get("items") or []) if isinstance(x, dict)]
        section_rows.append(
            {
                "key": str(sec.get("key") or "").strip(),
                "title": str(sec.get("title") or "").strip(),
                "items": sec_items,
            }
        )
    return ChatCard(
        type="nav_action",
        title=title,
        payload={
            "target": tid,
            "hint": hint,
            "action": action,
            "items": rows,
            "sections": section_rows,
            "total": int(total if total is not None else len(rows)),
            "empty_hint": str(empty_hint or "").strip(),
        },
    )


def _associate_elements_card(
    *,
    task_id: str,
    chapter_key: str,
    chapter_label: str = "",
    auto_open: bool = True,
    extra: dict | None = None,
) -> ChatCard:
    payload = {
        "task_id": str(task_id or "").strip(),
        "chapter_key": str(chapter_key or "").strip(),
        "chapter_label": str(chapter_label or "").strip(),
        "action": "associate_elements",
        "auto_open": bool(auto_open),
    }
    for key in (
        "associateScope",
        "pointItemId",
        "pointIndex",
        "pointSlot",
        "pointItems",
        "sequence",
        "title",
        "chapter_title",
    ):
        if extra and extra.get(key) not in (None, ""):
            payload[key] = extra.get(key)
    return ChatCard(
        type="associate_elements",
        title="关联要素",
        payload=payload,
    )


def _associate_element_knowledge_card(
    *,
    task_id: str = "",
    chapter_key: str = "",
    chapter_label: str = "",
    element_id: str = "",
    element_name: str = "",
) -> ChatCard:
    return ChatCard(
        type="associate_element_knowledge",
        title="关联知识库",
        payload={
            "task_id": str(task_id or "").strip(),
            "chapter_key": str(chapter_key or "").strip(),
            "chapter_label": str(chapter_label or "").strip(),
            "element_id": str(element_id or "").strip(),
            "element_name": str(element_name or "").strip(),
            "action": "associate_element_knowledge",
        },
    )


def _export_card(task_id: str) -> ChatCard:
    return ChatCard(
        type="export",
        title="渲染并下载 Word",
        payload={
            "task_id": task_id,
            "export_path": f"/api/writing/tasks/{task_id}/export",
            "download_path": f"/api/writing/tasks/{task_id}/render/download",
            "pdf_download_path": f"/api/writing/tasks/{task_id}/render/download/pdf",
            "hint": "默认按大纲生成目录并转 Word；渲染模版与 Word 样式均按当前机器人绑定展示。",
        },
    )


def _cards_with_export_card(cards: Iterable[ChatCard], task_id: str) -> list[ChatCard]:
    """不再附加对话内导出卡片；导出改走输入区底部「下载 Word」。"""
    _ = (task_id or "").strip()
    return list(cards or [])


def _chapter_draft_text(chapter) -> str:
    """合并章节正文为 HTML（与 pipeline._draft_text 一致）。"""
    official = (getattr(chapter, "official_text", None) or "").strip()
    if official:
        return official
    if chapter.draft and getattr(chapter.draft, "paragraphs", None):
        from report_writing.domain.content_blocks import ensure_html_fragment

        parts = [
            ensure_html_fragment(p.text)
            for p in chapter.draft.paragraphs
            if getattr(p, "text", None)
        ]
        return "\n".join(parts)
    return ""


def _chapter_display_body(chapter) -> tuple[str, list[str]]:
    """展示用正文：去掉来源/资料缺口；缺口单独返回。"""
    from report_writing.agents.write import _merge_uncovered, _sanitize_visible_body

    raw = _chapter_draft_text(chapter)
    cleaned, extracted = _sanitize_visible_body(raw)
    stored = []
    if chapter.draft and getattr(chapter.draft, "uncovered_points", None):
        stored = list(chapter.draft.uncovered_points or [])
    return cleaned, _merge_uncovered(stored, extracted)


def _chapter_config_snapshot(task: WritingTask, chapter_key: str) -> dict[str, Any]:
    node = None
    if task.outline is not None:
        node = next((item for item in task.outline.chapters if item.key == chapter_key), None)
    req = None
    if task.brief is not None:
        try:
            req = task.brief.chapter(chapter_key)
        except KeyError:
            req = None
    src = node or req
    if src is None:
        return {
            "key": chapter_key,
            "title": "",
            "sequence": "",
            "parent": "",
            "parent_sequence": "",
            "level": 1,
            "points": [],
            "point_items": [],
            "keywords": [],
            "describes": "",
            "aux_prompt": "",
            "reference_text": "",
            "fixed_output": False,
            "presence": "always",
            "compose": "hybrid",
            "compose_label": "",
            "structure_only": False,
            "element_ids": [],
            "extracts": [],
            "content_blocks_count": 0,
        }
    point_items = list(getattr(src, "point_items", None) or [])
    from report_writing.domain.compose_analysis import COMPOSE_LABELS

    compose = str(getattr(src, "compose", "") or "hybrid")
    return {
        "key": str(getattr(src, "key", "") or chapter_key),
        "title": str(getattr(src, "title", "") or "").strip(),
        "sequence": str(getattr(src, "sequence", "") or "").strip(),
        "parent": str(getattr(src, "parent", "") or "").strip(),
        "parent_sequence": str(getattr(src, "parent_sequence", "") or "").strip(),
        "level": int(getattr(src, "level", 1) or 1),
        "points": list(getattr(src, "points", None) or [])[:20],
        # 进度卡只保留要点关联摘要，避免整段 reference_text / 长 point_items 把会话撑爆
        "point_items": [
            {
                "id": str((row or {}).get("id") or f"p{i}"),
                "text": str((row or {}).get("text") or "")[:200],
                **(
                    {"element_id": eid, "element_name": str((row or {}).get("element_name") or "")[:80]}
                    if (eid := str((row or {}).get("element_id") or "").strip())
                    else {}
                ),
                **(
                    {"slot": slot}
                    if (slot := str((row or {}).get("slot") or "").strip())
                    else {}
                ),
                **(
                    {"needs_supplement": True}
                    if (row or {}).get("needs_supplement") and not str((row or {}).get("element_id") or "").strip()
                    else {}
                ),
            }
            for i, row in enumerate(point_items[:20])
            if isinstance(row, dict) and str((row or {}).get("text") or "").strip()
        ],
        "keywords": list(getattr(src, "keywords", None) or [])[:12],
        "describes": ((getattr(src, "describes", "") or "").strip())[:400],
        "aux_prompt": ((getattr(src, "aux_prompt", "") or "").strip())[:240],
        "reference_text": "",
        "fixed_output": bool(getattr(src, "fixed_output", False)),
        "presence": str(getattr(src, "presence", "") or "always"),
        "compose": compose,
        "compose_label": str(COMPOSE_LABELS.get(compose, compose) or compose).split("：")[0],
        "structure_only": bool(getattr(src, "structure_only", False)),
        "element_ids": list(getattr(src, "element_ids", None) or [])[:32],
        "extracts": [],
        "content_blocks_count": len(list(getattr(src, "content_blocks", None) or [])),
    }


def _slim_element_nodes_for_progress(nodes: list[Any] | None) -> list[dict[str, Any]]:
    """进度卡内嵌要素：去掉长 result / 附件细节，只保留展示与关联所需字段。"""
    out: list[dict[str, Any]] = []
    for item in nodes or []:
        if item is None:
            continue
        if isinstance(item, dict):
            eid = str(item.get("id") or "").strip()
            if not eid:
                continue
            result = str(item.get("result") or item.get("value") or "").strip()
            out.append(
                {
                    "id": eid,
                    "name": str(item.get("name") or eid).strip() or eid,
                    "kind": str(item.get("kind") or "").strip(),
                    "query": str(item.get("query") or "").strip()[:120],
                    "result": result[:240],
                    "required": bool(item.get("required", True)),
                    "library_item_id": str(item.get("library_item_id") or "").strip(),
                    "knowledge_folder_ids": list(item.get("knowledge_folder_ids") or [])[:8],
                }
            )
            continue
        eid = str(getattr(item, "id", "") or "").strip()
        if not eid:
            continue
        result = str(getattr(item, "result", "") or getattr(item, "value", "") or "").strip()
        out.append(
            {
                "id": eid,
                "name": str(getattr(item, "name", "") or eid).strip() or eid,
                "kind": str(getattr(item, "kind", "") or "").strip(),
                "query": str(getattr(item, "query", "") or "").strip()[:120],
                "result": result[:240],
                "required": bool(getattr(item, "required", True)),
                "library_item_id": str(getattr(item, "library_item_id", "") or "").strip(),
                "knowledge_folder_ids": list(getattr(item, "knowledge_folder_ids", None) or [])[:8],
            }
        )
    return out[:64]


def slim_progress_card_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    """裁掉历史进度卡里的整章 draft_text / 冗长 evidence / 完整 element_nodes。"""
    if not isinstance(payload, dict):
        return {}
    chapters_in = payload.get("chapters")
    chapters: list[Any] = list(chapters_in) if isinstance(chapters_in, list) else []
    # 历史卡可能塞过未写章；落库/回放时一并剔掉
    chapters, _ = _written_progress_chapters(
        [ch for ch in chapters if isinstance(ch, dict)]
    )
    slim_chapters: list[dict[str, Any]] = []
    for ch in chapters:
        if not isinstance(ch, dict):
            continue
        next_ch = dict(ch)
        preview = str(next_ch.get("draft_preview") or next_ch.get("draft_text") or "")[:1800]
        next_ch["draft_preview"] = preview
        next_ch.pop("draft_text", None)
        evidence = next_ch.get("evidence")
        if isinstance(evidence, list):
            next_ch["evidence"] = [
                {
                    **(row if isinstance(row, dict) else {}),
                    "excerpt": str((row or {}).get("excerpt") or "")[:180]
                    if isinstance(row, dict)
                    else "",
                    "query": str((row or {}).get("query") or "")[:80] if isinstance(row, dict) else "",
                }
                for row in evidence[:8]
                if isinstance(row, dict)
            ]
        results = next_ch.get("element_results")
        if isinstance(results, list):
            next_ch["element_results"] = [
                {
                    **(row if isinstance(row, dict) else {}),
                    "value": str((row or {}).get("value") or "")[:240] if isinstance(row, dict) else "",
                    "query": str((row or {}).get("query") or "")[:120] if isinstance(row, dict) else "",
                }
                for row in results[:12]
                if isinstance(row, dict)
            ]
        uncovered = next_ch.get("uncovered_points")
        if isinstance(uncovered, list):
            next_ch["uncovered_points"] = uncovered[:12]
        steps = next_ch.get("steps")
        if isinstance(steps, list):
            next_ch["steps"] = steps[-8:]
        cfg = next_ch.get("config")
        if isinstance(cfg, dict):
            next_ch["config"] = {
                **cfg,
                "reference_text": "",
                "describes": str(cfg.get("describes") or "")[:2500],
                "aux_prompt": str(cfg.get("aux_prompt") or "")[:240],
                "points": list(cfg.get("points") or [])[:20],
                "point_items": [
                    {
                        "id": str((row or {}).get("id") or f"p{i}"),
                        "text": str((row or {}).get("text") or "")[:200],
                        **(
                            {
                                "element_id": eid,
                                "element_name": str((row or {}).get("element_name") or "")[:80],
                            }
                            if (eid := str((row or {}).get("element_id") or "").strip())
                            else {}
                        ),
                        **(
                            {"slot": slot}
                            if (slot := str((row or {}).get("slot") or "").strip())
                            else {}
                        ),
                    }
                    for i, row in enumerate(list(cfg.get("point_items") or [])[:20])
                    if isinstance(row, dict)
                ],
                "extracts": [],
            }
        slim_chapters.append(next_ch)
    next_payload = dict(payload)
    next_payload["chapters"] = slim_chapters
    nodes = payload.get("element_nodes")
    if isinstance(nodes, list):
        next_payload["element_nodes"] = _slim_element_nodes_for_progress(nodes)
    return next_payload


def slim_session_progress_cards(session: Any) -> bool:
    """就地瘦身会话消息里的 progress 卡；有裁剪返回 True。"""
    from report_writing.domain.models import ChatCard

    changed = False
    messages = list(getattr(session, "messages", None) or [])
    if not messages:
        return False
    new_messages = []
    for msg in messages:
        cards = list(getattr(msg, "cards", None) or [])
        if not cards:
            new_messages.append(msg)
            continue
        next_cards: list[Any] = []
        msg_changed = False
        for card in cards:
            ctype = getattr(card, "type", None) if not isinstance(card, dict) else card.get("type")
            if ctype != "progress":
                next_cards.append(card)
                continue
            raw = getattr(card, "payload", None) if not isinstance(card, dict) else card.get("payload")
            before = raw if isinstance(raw, dict) else {}
            after = slim_progress_card_payload(before)
            if after != before:
                msg_changed = True
                if isinstance(card, ChatCard):
                    next_cards.append(card.model_copy(update={"payload": after}))
                elif isinstance(card, dict):
                    next_cards.append({**card, "payload": after})
                else:
                    next_cards.append(card)
            else:
                next_cards.append(card)
        if msg_changed:
            changed = True
            new_messages.append(msg.model_copy(update={"cards": next_cards}))
        else:
            new_messages.append(msg)
    if changed:
        session.messages = new_messages
    return changed


def _chapter_element_results_snapshot(task: WritingTask, chapter_key: str) -> list[dict[str, Any]]:
    """本章关联要素解析结果，供进度卡 / 文档窗展示「获取了哪些要素」。"""
    req = None
    if task.brief is not None:
        try:
            req = task.brief.chapter(chapter_key)
        except KeyError:
            req = None
    rows = list(getattr(req, "element_results", None) or []) if req is not None else []
    by_id = {
        str(getattr(el, "id", "") or "").strip(): el
        for el in list(getattr(task, "element_nodes", None) or [])
        if str(getattr(el, "id", "") or "").strip()
    }
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in rows:
        if not isinstance(item, dict):
            continue
        eid = str(item.get("id") or "").strip()
        value = str(item.get("value") or "").strip()
        if not value and eid and eid in by_id:
            value = str(getattr(by_id[eid], "result", "") or "").strip()
        name = str(item.get("name") or getattr(by_id.get(eid), "name", "") or eid or "要素").strip() or "要素"
        row = {
            "id": eid,
            "name": name,
            "kind": str(item.get("kind") or getattr(by_id.get(eid), "kind", "") or "").strip(),
            "query": str(item.get("query") or "").strip()[:160],
            "value": value[:240],
            "ok": bool(value),
            "slot_missing": list(item.get("slot_missing") or [])[:4],
        }
        key = eid or f"name:{name}"
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    # brief 尚未带 element_results 时，用章 element_ids + 要素节点 result 兜底
    if not out:
        node = None
        if task.outline is not None:
            node = next(
                (item for item in task.outline.chapters if str(getattr(item, "key", "") or "") == chapter_key),
                None,
            )
        from report_writing.domain.point_items_sync import element_ids_for_chapter_node

        for eid in element_ids_for_chapter_node(node, elements_by_id=by_id) if node is not None else []:
            el = by_id.get(eid)
            if el is None:
                continue
            value = str(getattr(el, "result", "") or "").strip()
            out.append(
                {
                    "id": eid,
                    "name": str(getattr(el, "name", "") or eid).strip() or eid,
                    "kind": str(getattr(el, "kind", "") or "").strip(),
                    "query": str(getattr(el, "query", "") or "").strip()[:120],
                    "value": value[:240],
                    "ok": bool(value),
                    "slot_missing": [],
                }
            )
    return out[:12]


def _chapter_progress_row(task: WritingTask, chapter) -> dict[str, Any]:
    from report_writing.chat.helpers import _CHAPTER_STATUS_LABEL, _STEP_LABEL
    status = str(chapter.status.value if hasattr(chapter.status, "value") else chapter.status)
    evidence = list(chapter.evidence or [])
    checks = list(chapter.checks or [])
    draft, uncovered_points = _chapter_display_body(chapter)
    steps = []
    for event in task.steps or []:
        chapter_key = getattr(event, "chapter", "") or ""
        if chapter_key and chapter_key != chapter.key:
            continue
        if not chapter_key and getattr(event, "title", "") not in {chapter.title, chapter.key}:
            # 无章节键的全局步骤不塞进单章详情
            continue
        step = str(getattr(event, "step", "") or "")
        estatus = str(getattr(event, "status", "") or "")
        result = getattr(event, "result", None) or {}
        note = ""
        if isinstance(result, dict):
            note = str(result.get("note") or result.get("reason") or result.get("message") or "")
        steps.append(
            {
                "step": step,
                "step_label": _STEP_LABEL.get(step, step or "步骤"),
                "status": estatus,
                "status_label": {"running": "进行中", "done": "完成", "skipped": "跳过", "error": "失败"}.get(
                    estatus, estatus
                ),
                "note": note[:240],
            }
        )
    structure_only = False
    sequence = ""
    if task.outline is not None:
        node = next((item for item in task.outline.chapters if item.key == chapter.key), None)
        if node is not None:
            sequence = str(getattr(node, "sequence", "") or "")
            structure_only = bool(getattr(node, "structure_only", False))
    # 目录节点仍可改「是否目录」；其它已确认/跳过只读
    if structure_only:
        editable = True
        status_label = "目录"
    else:
        editable = status not in {
            ChapterStatus.CONFIRMED.value,
            ChapterStatus.SKIPPED.value,
        }
        status_label = _CHAPTER_STATUS_LABEL.get(status, status)
    is_running = status in {
        ChapterStatus.SEARCHING.value,
        ChapterStatus.WRITING.value,
        ChapterStatus.CHECKING.value,
    }
    check_rows = []
    for item in checks[-3:]:
        action = getattr(item, "action", "")
        action_s = str(action.value if hasattr(action, "value") else action or "")
        check_rows.append(
            {
                "action": action_s,
                "reason": (getattr(item, "reason", "") or "")[:160],
                "score": int(getattr(item, "score", 0) or 0),
                "below_threshold": bool(getattr(item, "below_threshold", False)),
                "comments": [
                    {
                        "index": int(getattr(c, "index", 0) or 0),
                        "message": str(getattr(c, "message", "") or "")[:120],
                    }
                    for c in list(getattr(item, "comments", None) or [])[:4]
                ],
            }
        )
    last_check = check_rows[-1] if check_rows else {}
    revision_n = int(chapter.revision_count or 0)
    return {
        "key": chapter.key,
        "title": chapter.title or chapter.key,
        "sequence": sequence,
        "status": status,
        "status_label": status_label,
        "is_running": is_running,
        "structure_only": structure_only,
        "human_note": (chapter.human_note or "").strip(),
        "revision_count": revision_n,
        "max_revisions": int(MAX_REVISIONS),
        "write_score_pass": int(WRITE_SCORE_PASS),
        "rewritten": revision_n > 0,
        "check_action": str(last_check.get("action") or ""),
        "check_score": int(last_check.get("score") or 0),
        "check_below_threshold": bool(last_check.get("below_threshold")),
        "check_reason": str(last_check.get("reason") or "")[:160],
        "evidence_count": len(evidence),
        "evidence": [
            {
                "id": getattr(item, "id", "") or "",
                "point": getattr(item, "point", "") or "",
                "source_file": getattr(item, "source_file", "") or "",
                "location": getattr(item, "location", "") or "",
                "query": (getattr(item, "query", "") or "")[:80],
                "excerpt": (getattr(item, "excerpt", "") or "")[:180],
                "adopted": bool(getattr(item, "adopted", False)),
            }
            for item in evidence[:8]
        ],
        "element_results": _chapter_element_results_snapshot(task, chapter.key),
        "checks": check_rows,
        # 正文真值走 draft-preview API；进度卡只留短预览，避免会话 JSON 膨胀拖慢加载/对话
        "draft_preview": draft[:1800],
        "has_draft": bool(draft.strip()),
        "uncovered_points": uncovered_points[:12],
        "editable": editable,
        "config": _chapter_config_snapshot(task, chapter.key),
        "steps": steps[-8:],
    }


def _written_progress_chapters(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """进度卡只输出正在写 / 已写过的章，不带未开写、下一章占位。"""
    if not rows:
        return [], 0
    writing_now = {
        ChapterStatus.SEARCHING.value,
        ChapterStatus.WRITING.value,
        ChapterStatus.CHECKING.value,
    }
    visible: list[dict[str, Any]] = []
    for row in rows:
        if row.get("structure_only"):
            continue
        r = dict(row)
        status = str(r.get("status") or "")
        touched = bool(
            r.get("has_draft")
            or r.get("steps")
            or r.get("checks")
            or int(r.get("revision_count") or 0) > 0
            or int(r.get("check_score") or 0) > 0
        )
        # 正在写：直接露出；已写完：必须有正文/步骤/检查痕迹，避免整本大纲混进进度
        if r.get("is_running") or status in writing_now:
            visible.append(r)
        elif touched and status in {
            ChapterStatus.AWAITING_CONFIRM.value,
            ChapterStatus.NEEDS_HUMAN.value,
            ChapterStatus.CONFIRMED.value,
        }:
            visible.append(r)
        elif touched and status not in {"", ChapterStatus.PENDING.value, ChapterStatus.SKIPPED.value}:
            visible.append(r)
    order = {str(r.get("key") or ""): i for i, r in enumerate(rows)}
    visible.sort(key=lambda r: order.get(str(r.get("key") or ""), 10_000))
    collapsed = max(0, len([r for r in rows if not r.get("structure_only")]) - len(visible))
    return visible, collapsed


def _visible_progress_chapters(rows: list[dict[str, Any]], *, pace: str) -> tuple[list[dict[str, Any]], int]:
    """不论逐步/批量：进度只展示本轮已写或正在写的章。"""
    del pace  # 展示范围不再随 pace 扩大到未写章
    return _written_progress_chapters(rows)


def _pick_focus_chapter_key(all_rows: list[dict[str, Any]], visible: list[dict[str, Any]]) -> str:
    """Sticky confirm: running chapter, else latest chapter needing user review (outline order)."""
    for row in visible:
        if row.get("is_running"):
            return str(row.get("key") or "")
    # 待确认与待人工都算「要人看」；取大纲里最靠后的一章（刚写完的），避免旧章待确认挡住新章正文。
    needs_review = [
        str(r.get("key") or "")
        for r in all_rows
        if str(r.get("status") or "")
        in {
            ChapterStatus.AWAITING_CONFIRM.value,
            ChapterStatus.NEEDS_HUMAN.value,
        }
        and not r.get("structure_only")
    ]
    if needs_review:
        return needs_review[-1]
    for row in visible:
        if row.get("is_next") or str(row.get("status") or "") == ChapterStatus.PENDING.value:
            if row.get("structure_only"):
                continue
            return str(row.get("key") or "")
    for row in visible:
        if row.get("editable") and not row.get("structure_only"):
            return str(row.get("key") or "")
    return ""


def _progress_and_confirm_cards(task: WritingTask, *, pace: str = "") -> list[ChatCard]:
    from report_writing.chat.helpers import WRITING_PACE_STEP, _TASK_STATUS_LABEL
    from report_writing.chat.writing import _normalize_writing_pace, _pending_write_keys
    task_status = str(task.status.value if hasattr(task.status, "value") else task.status)
    all_rows = [_chapter_progress_row(task, c) for c in (task.chapters or [])]
    pace_norm = _normalize_writing_pace(pace)
    pending_write = len(_pending_write_keys(task))
    confirmed_n = sum(1 for c in all_rows if c.get("status") == ChapterStatus.CONFIRMED.value)
    needs_human_n = sum(1 for c in all_rows if c.get("status") == ChapterStatus.NEEDS_HUMAN.value)
    awaiting_n = sum(1 for c in all_rows if c.get("status") == ChapterStatus.AWAITING_CONFIRM.value)
    chapters, collapsed = _visible_progress_chapters(all_rows, pace=pace_norm)
    # 待确认：已有正文等你拍板，不含尚未开写的排队章
    pending_confirm = sum(
        1
        for c in all_rows
        if not c.get("structure_only")
        and str(c.get("status") or "")
        in {
            ChapterStatus.AWAITING_CONFIRM.value,
            ChapterStatus.NEEDS_HUMAN.value,
        }
    )
    focus = _pick_focus_chapter_key(all_rows, chapters)
    from report_writing.chat.freeform_points import count_chapters_missing_points

    missing_pts = count_chapters_missing_points(task)
    source_bp = str(getattr(task, "source_blueprint_id", None) or "").strip()
    if not source_bp:
        # clone 时 description 形如「从蓝本 {id} clone」
        m = re.search(r"从蓝本\s+(\S+)\s+clone", str(getattr(task, "description", "") or ""))
        if m:
            source_bp = str(m.group(1) or "").strip()
    doc_title = str(getattr(task, "title", "") or "").strip()
    # 写作 clone 标题常带「（对话写作）」后缀，文档窗展示用干净名
    if doc_title.endswith("（对话写作）"):
        doc_title = doc_title[: -len("（对话写作）")].strip() or doc_title
    return [
        ChatCard(
            type="progress",
            title="写作进度",
            payload={
                "task_id": task.id,
                "blueprint_id": source_bp,
                "title": doc_title,
                "chapters_missing_points": missing_pts,
                "can_generate_points": missing_pts > 0,
                "status": task_status,
                "status_label": _TASK_STATUS_LABEL.get(task_status, task_status),
                "pending_count": pending_confirm,
                "pending_write_count": pending_write,
                "total_count": len(all_rows),
                "shown_count": len(chapters),
                "collapsed_count": collapsed,
                "writing_pace": pace_norm,
                "can_confirm_all": pending_confirm > 0 and pace_norm != WRITING_PACE_STEP,
                "can_confirm_one": pending_confirm > 0,
                "focus_chapter_key": focus,
                # 进度卡只留要素摘要；完整节点走任务 API，避免会话 JSON / SSE 膨胀
                "element_nodes": _slim_element_nodes_for_progress(task.element_nodes or []),
                "stats": {
                    "needs_human": needs_human_n,
                    "awaiting_confirm": awaiting_n,
                    "confirmed": confirmed_n,
                    "pending_write": pending_write,
                },
                "chapters": chapters,
            },
        )
    ]


def _resource_choice_card(
    resource: str,
    *,
    title: str,
    message: str,
    yes_label: str = "是",
    no_label: str = "否",
    optional: bool = False,
) -> ChatCard:
    return ChatCard(
        type="resource_choice",
        title=title,
        payload={
            "resource": resource,
            "message": message,
            "yes_label": yes_label,
            "no_label": no_label,
            "optional": bool(optional),
        },
    )


def _knowledge_empty_card(*, folder_names: list[str], folder_ids: list[str]) -> ChatCard:
    """兼容旧调用；知识库已下线，改引导资料库。"""
    _ = (folder_names, folder_ids)
    return ChatCard(
        type="knowledge_empty",
        title="请使用资料库",
        payload={
            "message": "知识库已下线。资料库是放资料包的地方；要素「文件」可上传并手动解析。",
            "folder_names": [],
            "folder_ids": [],
            "page": "materials",
        },
    )


def _blueprint_wizard_card(
    *,
    blueprint_id: str,
    step: str,
    title: str = "在对话中创建蓝本",
    extra: dict[str, Any] | None = None,
) -> ChatCard:
    from report_writing.chat.wizard_state import (
        SELECTED_WIZARD_STEPS,
        WIZARD_MODE_SELECTED,
        _wizard_card_title,
        is_selected_wizard_mode,
    )

    mode = str((extra or {}).get("mode") or "").strip()
    selected = mode == WIZARD_MODE_SELECTED or is_selected_wizard_mode(extra)
    create_steps = [
        {"id": "meta", "label": "填写名称"},
        {"id": "upload", "label": "上传样板"},
        {"id": "outline", "label": "提取目录"},
        {"id": "outline_confirm", "label": "确认目录"},
        {"id": "study", "label": "分析要点"},
    ]
    payload = ChatActionPayload.parse(
        {
            "blueprint_id": blueprint_id,
            "step": step,
            "steps": list(SELECTED_WIZARD_STEPS) if selected else create_steps,
        }
    )
    if extra:
        payload = payload.with_updates(**{k: v for k, v in extra.items() if k != "steps"})
    if selected:
        payload = payload.with_updates(mode=WIZARD_MODE_SELECTED, steps=list(SELECTED_WIZARD_STEPS))
    payload = payload.with_updates(blueprint_id=blueprint_id or str(payload.get("blueprint_id") or ""))
    pinned = (step or "").strip() or ("outline_confirm" if selected else "meta")
    # 已有蓝本 id 后不得再发「填写名称」卡（会与「已建好」文案打架并形成步骤回退）
    if payload["blueprint_id"] and pinned in {"", "meta"}:
        pinned = "outline_confirm" if selected else "upload"
        if not selected and (
            not (title or "").strip() or "填写名称" in (title or "") or title.endswith("· 名称")
        ):
            title = "创建蓝本 · 上传样板"
    if selected and (
        not (title or "").strip()
        or "创建蓝本" in (title or "")
        or title in {"在对话中创建蓝本", "蓝本创建向导"}
    ):
        title = _wizard_card_title(pinned, mode=WIZARD_MODE_SELECTED)
    payload = payload.with_updates(step=pinned)
    return stamp_chat_card(ChatCard(type="blueprint_wizard", title=title, payload=payload))


def _template_wizard_card(
    resource: str,
    *,
    step: str,
    item_id: str = "",
    title: str,
    message: str = "",
) -> ChatCard:
    return ChatCard(
        type="template_wizard",
        title=title,
        payload={
            "resource": resource,
            "step": step,
            "item_id": item_id,
            "message": message,
        },
    )


