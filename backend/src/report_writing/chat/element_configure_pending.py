"""对话里提议新增要素：先在前端配置确认，再写入任务。"""

from __future__ import annotations

import uuid
from typing import Any

from report_writing.domain.models import ChatActionPayload, ChatCard, ChatPayload, ChatSession


def enrich_element_draft_from_dialogue(
    draft: dict[str, Any],
    payload: ChatPayload,
    *,
    chapter_title: str = "",
) -> dict[str, Any]:
    """助手 payload 不全时，用对话字段补全分类/标签/作用说明与键值内容。"""
    payload = ChatActionPayload.parse(payload)
    name = str(draft.get("name") or "").strip()
    kind = str(draft.get("kind") or "kv").strip()
    if kind == "kv":
        pairs = list(draft.get("kv_pairs") or [])
        if pairs and not name:
            first_key = str(pairs[0].get("key") or "").strip()
            if first_key:
                name = first_key
                draft["name"] = name
        body = str(
            payload.get("content")
            or payload.get("body")
            or payload.get("text")
            or payload.get("description")
            or ""
        ).strip()
        if body and pairs:
            for i, row in enumerate(pairs):
                if not str(row.get("value") or "").strip():
                    pairs[i] = {**row, "value": body}
            draft["kv_pairs"] = pairs
            if len(pairs) == 1:
                draft["value"] = body
    if not str(draft.get("category") or "").strip() and name:
        draft["category"] = name
    purpose = str(draft.get("purpose") or payload.get("purpose") or "").strip()
    if not purpose:
        hint = str(payload.get("hint") or payload.get("user_note") or "").strip()
        if hint:
            purpose = hint
        elif name and chapter_title:
            purpose = f"用于「{chapter_title}」的{name}"
        elif name:
            purpose = f"写作要素：{name}"
    draft["purpose"] = purpose
    tags = draft.get("tags")
    if not isinstance(tags, list):
        tags = []
    if not tags:
        raw_tags = payload.get("tags")
        if isinstance(raw_tags, str):
            tags = [t.strip() for t in raw_tags.replace("，", ",").split(",") if t.strip()]
        elif isinstance(raw_tags, list):
            tags = [str(t).strip() for t in raw_tags if str(t).strip()]
    if not tags and draft.get("category"):
        tags = [str(draft["category"]).strip()]
    elif not tags and name:
        tags = [name]
    draft["tags"] = tags
    return draft


def list_configure_pending(session: ChatSession) -> list[dict[str, Any]]:
    raw = (session.freeform or {}).get("element_configure_pending")
    if not isinstance(raw, list):
        return []
    return [dict(x) for x in raw if isinstance(x, dict)]


def payload_to_draft(
    payload: ChatPayload,
    *,
    task_elements: list | None = None,
) -> dict[str, Any]:
    payload = ChatActionPayload.parse(payload)
    kind = str(payload.get("kind") or "kv").strip()
    if kind == "rag_search":
        kind = "file"
    if kind not in {"kv", "fixed", "file", "file_slot", "llm_generate", "node_ref", "image"}:
        kind = "kv"
    name = str(payload.get("name") or "").strip()
    value = str(payload.get("value") or "").strip()
    query = str(payload.get("query") or "").strip()
    prompt = str(payload.get("prompt") or "").strip()
    kv_pairs = payload.get("kv_pairs")
    if not isinstance(kv_pairs, list):
        kv_pairs = []
    if kind == "kv" and not kv_pairs:
        key = str(payload.get("key") or name or "").strip()
        if key or value:
            kv_pairs = [{"key": key or "键", "value": value}]
        else:
            kv_pairs = [{"key": "", "value": ""}]
    from report_writing.chat.element_ref_resolve import resolve_ref_element_name

    ref_key = str(payload.get("ref_key") or "").strip()
    ref_name = str(
        payload.get("ref_element_name") or payload.get("ref_name") or payload.get("ref_target") or ""
    ).strip()
    ref_picks: list[dict[str, Any]] = []
    if kind == "node_ref":
        if not ref_key.startswith("el-"):
            lookup = ref_name or ref_key
            if lookup:
                ref_res = resolve_ref_element_name(lookup, task_elements)
                ref_key = str(ref_res.get("ref_key") or "").strip()
                ref_picks = list(ref_res.get("ref_picks") or [])

    file_slots = payload.get("file_slots") if isinstance(payload.get("file_slots"), list) else []
    files = payload.get("files") if isinstance(payload.get("files"), list) else []

    return {
        "name": name,
        "kind": kind,
        "value": value,
        "query": query,
        "prompt": prompt,
        "kv_pairs": kv_pairs,
        "knowledge_folder_ids": [],
        "file_slots": file_slots,
        "files": files,
        "ref_key": ref_key,
        "ref_element_picks": ref_picks,
        "ref_kind": "element",
        "ref_field": "result",
        "category": str(payload.get("category") or "").strip(),
        "tags": (
            [str(t).strip() for t in payload.get("tags") if str(t).strip()]
            if isinstance(payload.get("tags"), list)
            else []
        ),
        "purpose": str(payload.get("purpose") or "").strip(),
        "required": True,
        "image_source": str(payload.get("image_source") or "upload").strip() or "upload",
        "image_path": str(payload.get("image_path") or "").strip(),
    }


def payload_to_draft_enriched(
    payload: ChatPayload,
    *,
    task_elements: list | None = None,
    chapter_title: str = "",
) -> dict[str, Any]:
    draft = payload_to_draft(payload, task_elements=task_elements)
    return enrich_element_draft_from_dialogue(draft, payload, chapter_title=chapter_title)


def queue_configure_pending(
    session: ChatSession,
    *,
    task_id: str,
    chapter_key: str,
    chapter_title: str,
    draft: dict[str, Any],
    agent_hint: str = "",
    point_index: Any = None,
    slot_label: str = "",
) -> dict[str, Any]:
    entry = {
        "pending_id": f"pec-{uuid.uuid4().hex[:10]}",
        "task_id": (task_id or "").strip(),
        "chapter_key": (chapter_key or "").strip(),
        "chapter_title": (chapter_title or "").strip(),
        "draft": dict(draft),
        "agent_hint": (agent_hint or "").strip(),
    }
    if point_index is not None:
        entry["point_index"] = point_index
    slot = str(slot_label or "").strip()
    if slot:
        entry["slot_label"] = slot
    ff = dict(session.freeform or {})
    pending = list_configure_pending(session)
    pending.append(entry)
    ff["element_configure_pending"] = pending[-20:]
    session.freeform = ff
    return entry


def dismiss_configure_pending(session: ChatSession, pending_ids: list[str] | None = None) -> int:
    ff = dict(session.freeform or {})
    pending = list_configure_pending(session)
    if not pending_ids:
        n = len(pending)
        ff["element_configure_pending"] = []
        session.freeform = ff
        return n
    ids = {str(x).strip() for x in pending_ids if str(x).strip()}
    kept = [p for p in pending if str(p.get("pending_id") or "") not in ids]
    removed = len(pending) - len(kept)
    ff["element_configure_pending"] = kept
    session.freeform = ff
    return removed


def configure_pending_card(session: ChatSession) -> ChatCard | None:
    pending = list_configure_pending(session)
    if not pending:
        return None
    return ChatCard(
        type="element_configure_pending",
        title="确认要素配置",
        payload={
            "items": pending,
            "count": len(pending),
            "hint": "助手已根据对话预填各字段，请核对后保存；仅需改错或补漏，不必从零填写。",
        },
    )


def merge_cards_with_configure(session: ChatSession, cards: list[ChatCard]) -> list[ChatCard]:
    extra = configure_pending_card(session)
    if extra is None:
        return list(cards or [])
    out = list(cards or [])
    if any(c.type == "element_configure_pending" for c in out):
        return out
    out.append(extra)
    return out
