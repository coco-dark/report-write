"""对话内管理本文档要素：取消章节关联、从文档删除（不删要素库）。"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from report_writing.domain.models import ChatActionPayload, ChatPayload, ChatSession, ElementNode, WritingTask


def extract_element_name_hint(text: str) -> str:
    """无模型时从口语抽要素名（删除/取消关联/移除）。"""
    t = (text or "").strip()
    if not t:
        return ""
    patterns = (
        r"(?:把|将)?(.+?)(?:从|在).{0,12}(?:章|节).{0,8}(?:取消关联|解除关联|去掉关联|移除关联|删关联)",
        r"(?:取消|解除|去掉|移除).{0,6}(?:关联|绑定).{0,8}(.+?)(?:要素|元素)?(?:$|，|。)",
        r"(?:删除|删掉|移除|去掉|清除)(?:文档里的|本任务的|本章的|这个)?(.+?)(?:要素|元素)?(?:$|，|。)",
        r"(.+?)(?:要素|元素).{0,6}(?:删除|删掉|移除|去掉)",
    )
    for pat in patterns:
        m = re.search(pat, t)
        if not m:
            continue
        name = (m.group(1) or "").strip().strip("「」\"'《》")
        name = re.sub(r"^(把|将|的|这|那|一个|这个|那个)", "", name).strip()
        if name in {"它", "这个", "那个", "该", "全部", "所有"}:
            continue
        if name.endswith("要素") and len(name) > 2:
            name = name[:-2].strip()
        if name:
            return name
    return ""


def match_task_elements(
    task: WritingTask | None,
    *,
    name_hint: str = "",
    element_id: str = "",
    chapter_key: str = "",
) -> list[ElementNode]:
    if task is None:
        return []
    eid = (element_id or "").strip()
    nodes = list(getattr(task, "element_nodes", None) or [])
    if eid:
        return [e for e in nodes if str(getattr(e, "id", "") or "").strip() == eid]
    ck = (chapter_key or "").strip()
    allowed: set[str] | None = None
    if ck and getattr(task, "outline", None):
        for ch in list(task.outline.chapters or []):
            if str(getattr(ch, "key", "") or "").strip() == ck:
                allowed = {
                    str(x).strip()
                    for x in (getattr(ch, "element_ids", None) or [])
                    if str(x).strip()
                }
                break
    if allowed is not None:
        nodes = [e for e in nodes if str(getattr(e, "id", "") or "").strip() in allowed]
    hint = (name_hint or "").strip()
    if not hint:
        return nodes
    from report_writing.services.element_library import normalize_element_name

    key = normalize_element_name(hint).casefold()
    exact = [
        e
        for e in nodes
        if normalize_element_name(str(getattr(e, "name", "") or "")).casefold() == key
    ]
    if exact:
        return exact
    return [
        e
        for e in nodes
        if key in normalize_element_name(str(getattr(e, "name", "") or "")).casefold()
    ]


def chapters_linking_element(task: WritingTask | None, element_id: str) -> list[tuple[str, str]]:
    eid = (element_id or "").strip()
    if not task or not eid or not getattr(task, "outline", None):
        return []
    rows: list[tuple[str, str]] = []
    for ch in list(task.outline.chapters or []):
        ids = [str(x).strip() for x in (getattr(ch, "element_ids", None) or []) if str(x).strip()]
        if eid not in ids:
            continue
        label = f"{getattr(ch, 'sequence', '') or ''} {getattr(ch, 'title', '') or ''}".strip()
        rows.append((str(getattr(ch, "key", "") or "").strip(), label or str(getattr(ch, "key", "") or "")))
    return rows


def _resolve_name_hint(body: ChatPayload, message: str, *, invoke=None) -> str:
    payload = ChatActionPayload.parse(body)
    hint = str(payload.get("element_name") or payload.get("name") or "").strip()
    if hint:
        return hint
    if invoke is not None:
        try:
            from report_writing.chat.collect_element_library import resolve_collect_element_name

            names = []
            return resolve_collect_element_name(message, candidate_names=names, invoke=invoke) or ""
        except Exception:
            pass
    return extract_element_name_hint(message)


def _element_disambiguate_card(
    session: ChatSession,
    tool: str,
    body: ChatPayload,
    matches: list[ElementNode],
    *,
    task: WritingTask | None,
    message: str,
) -> tuple[str, list[Any]]:
    from report_writing.chat.action_slots import build_action_slot_clarify

    payload = ChatActionPayload.parse(body).as_dict()
    rows = [
        (str(getattr(el, "id", "") or "").strip(), str(getattr(el, "name", "") or "").strip())
        for el in matches
        if str(getattr(el, "id", "") or "").strip()
    ]
    reply, cards = build_action_slot_clarify(
        session,
        tool,
        payload,
        ["element_id"],
        task=task,
        message=message,
        blueprints=rows,
    )
    return reply, list(cards or [])


def dissociate_chapter_element(
    home: Path,
    session: ChatSession,
    message: str,
    payload: ChatPayload = None,
    *,
    invoke=None,
) -> tuple[str, list[Any]]:
    """仅取消本章与要素的关联，不删要素节点、不碰要素库。"""
    from report_writing.chat.helpers import _load_task
    from report_writing.chat.writing import _apply_dissociate_chapter_element
    from report_writing.services.store import TaskStore

    body = ChatActionPayload.parse(payload).as_dict()
    task_id = str(body.get("task_id") or getattr(session, "task_id", "") or "").strip()
    if not task_id:
        return "还没有写作任务，先选定蓝本或确认大纲后再改要素。", []
    task = _load_task(home, task_id)
    if task is None:
        return "写作任务找不到了，请重新开写后再操作。", []

    from report_writing.chat.writing_control_route import _resolve_associate_chapter_key

    chapter_key = str(body.get("chapter_key") or "").strip()
    if not chapter_key:
        chapter_key = _resolve_associate_chapter_key(home, session, message, body, task)
    element_id = str(body.get("element_id") or "").strip()
    name_hint = _resolve_name_hint(body, message, invoke=invoke)
    matches = match_task_elements(
        task, name_hint=name_hint, element_id=element_id, chapter_key=chapter_key
    )

    if not element_id and name_hint and len(matches) > 1:
        body.setdefault("task_id", task_id)
        if chapter_key:
            body["chapter_key"] = chapter_key
        return _element_disambiguate_card(
            session, "dissociate_chapter_element", body, matches, task=task, message=message
        )

    if not matches:
        if name_hint:
            return f"没找到与「{name_hint}」关联的要素，请说清章节号或要素全名。", []
        return "请说明要取消哪一章、哪个要素的关联。", []

    el = matches[0]
    eid = str(getattr(el, "id", "") or "").strip()
    linked = chapters_linking_element(task, eid)

    if not chapter_key:
        if len(linked) == 1:
            chapter_key = linked[0][0]
        elif len(linked) > 1:
            from report_writing.chat.clarify_flow import begin_clarify

            body = {**body, "task_id": task_id, "element_id": eid, "element_name": el.name}
            options = []
            for k, lbl in linked:
                args = {**body, "chapter_key": k, "element_id": eid}
                options.append(
                    {
                        "id": f"ch_{k}",
                        "label": lbl or k,
                        "slots": {
                            "tool": "dissociate_chapter_element",
                            "chapter_key": k,
                            "element_id": eid,
                            "args": args,
                        },
                    }
                )
            return begin_clarify(
                session,
                source="action_slots",
                question=f"「{el.name or eid}」关联在多个章节，你想取消哪一章的关联？",
                options=options,
                candidate={
                    "kind": "control_tool",
                    "name": "dissociate_chapter_element",
                    "payload": body,
                },
                phase_hint="action_slots",
            )
        else:
            return f"「{el.name or eid}」当前没有挂在任何章节上。", []

    if not chapter_key:
        from report_writing.chat.action_slots import build_action_slot_clarify, missing_action_slots

        body.setdefault("task_id", task_id)
        miss = missing_action_slots("dissociate_chapter_element", body, session=session, task=task)
        return build_action_slot_clarify(
            session,
            "dissociate_chapter_element",
            body,
            miss or ["chapter_key"],
            task=task,
            message=message,
        )

    store = TaskStore(home / "tasks" / task_id)
    task = store.load()
    try:
        label = _apply_dissociate_chapter_element(task, chapter_key, eid)
    except ValueError as err:
        return str(err), []
    store.save(task)
    from report_writing.chat.session_runtime import clear_leftover_study_triggers

    clear_leftover_study_triggers(session)
    return (
        f"已取消「{label}」与要素「{el.name or eid}」的关联；要素仍保留在本文档，可再关联其它章。"
        "（不会删除个人要素库里的条目。）",
        [],
    )


def remove_document_element(
    home: Path,
    session: ChatSession,
    message: str,
    payload: ChatPayload = None,
    *,
    invoke=None,
) -> tuple[str, list[Any]]:
    """从当前写作文档移除要素（含各章关联）；不删除要素库。"""
    from report_writing.chat.element_configure_pending import dismiss_configure_pending
    from report_writing.chat.helpers import _load_task
    from report_writing.chat.writing import _apply_remove_document_element
    from report_writing.services.store import TaskStore

    body = ChatActionPayload.parse(payload).as_dict()
    task_id = str(body.get("task_id") or getattr(session, "task_id", "") or "").strip()
    if not task_id:
        return "还没有写作任务，先选定蓝本或确认大纲后再删要素。", []
    task = _load_task(home, task_id)
    if task is None:
        return "写作任务找不到了，请重新开写后再操作。", []

    element_id = str(body.get("element_id") or "").strip()
    name_hint = _resolve_name_hint(body, message, invoke=invoke)
    matches = match_task_elements(task, name_hint=name_hint, element_id=element_id)

    if not element_id and name_hint and len(matches) > 1:
        body.setdefault("task_id", task_id)
        return _element_disambiguate_card(
            session, "remove_document_element", body, matches, task=task, message=message
        )

    if not matches:
        if name_hint:
            return f"本文档里没有名为「{name_hint}」的要素。", []
        return "请说明要删除哪个要素（名称或描述清楚一点）。", []

    el = matches[0]
    eid = str(getattr(el, "id", "") or "").strip()
    lib_id = str(getattr(el, "library_item_id", "") or "").strip()
    store = TaskStore(home / "tasks" / task_id)
    task = store.load()
    name = str(getattr(el, "name", "") or eid).strip()
    try:
        _apply_remove_document_element(task, eid)
    except ValueError as err:
        return str(err), []
    store.save(task)
    from report_writing.chat.element_configure_pending import list_configure_pending

    pending = list_configure_pending(session)
    to_drop = [
        str(p.get("pending_id") or "")
        for p in pending
        if str((p.get("draft") or {}).get("id") or "") == eid
    ]
    if to_drop:
        dismiss_configure_pending(session, to_drop)
    lib_note = (
        f"个人要素库中的「{name}」条目未动，只是本文档不再使用。"
        if lib_id
        else ""
    )
    from report_writing.chat.session_runtime import clear_leftover_study_triggers

    clear_leftover_study_triggers(session)
    return (
        f"已从本文档删除要素「{name}」（各章关联已一并去掉）。{lib_note}".strip(),
        [],
    )
