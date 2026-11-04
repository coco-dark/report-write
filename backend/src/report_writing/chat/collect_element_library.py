"""把本任务已有要素收入个人要素库（不是新建章节要素草稿）。"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from report_writing.domain.models import ChatSession, ElementNode, WritingTask

def extract_collect_element_name(text: str) -> str:
    """无模型时的窄兜底：常见「把××收入要素库」句式。有 invoke 请用 resolve_collect_element_name。"""
    t = (text or "").strip()
    if not t:
        return ""
    patterns = (
        r"(?:将|把)(.+?)(?:添加|加入|收入|收录|收集|存|放|导入).{0,8}(?:要素库|个人库|个人要素库)",
        r"(.+?)(?:添加|加入|收入|收录|收集|存|放|导入).{0,4}(?:到|进|入).{0,4}(?:要素库|个人库|个人要素库)",
    )
    for pat in patterns:
        m = re.search(pat, t)
        if not m:
            continue
        name = (m.group(1) or "").strip().strip("「」\"'《》")
        name = re.sub(r"^(把|将)", "", name).strip()
        if name in {"这个", "该", "该要素", "这个要素", "这些", "全部", "所有"}:
            return ""
        if name.endswith("要素") and len(name) > 2:
            # 保留「知识库要素」整名；去掉多余「的」
            name = name.lstrip("的").strip()
        if name:
            return name
    return ""


def resolve_collect_element_name(
    message: str,
    *,
    candidate_names: list[str] | None = None,
    invoke=None,
) -> str:
    """优先 LLM 抽要素名；失败再退回窄正则。"""
    text = (message or "").strip()
    if not text:
        return ""
    if invoke is not None:
        try:
            from report_writing.chat.intent_llm import extract_collect_element_name_llm

            hit = extract_collect_element_name_llm(
                text,
                candidate_names=candidate_names,
                invoke=invoke,
            )
            if hit:
                return hit
        except Exception:
            pass
    return extract_collect_element_name(text)

def _unlinked_task_elements(task: WritingTask | None) -> list[ElementNode]:
    out: list[ElementNode] = []
    if task is None:
        return out
    for el in list(getattr(task, "element_nodes", None) or []):
        if (getattr(el, "library_item_id", "") or "").strip():
            continue
        if not str(getattr(el, "id", "") or "").strip():
            continue
        out.append(el)
    return out

def _match_elements(
    task: WritingTask | None,
    *,
    name_hint: str = "",
    element_id: str = "",
) -> list[ElementNode]:
    nodes = _unlinked_task_elements(task)
    eid = (element_id or "").strip()
    if eid:
        return [e for e in nodes if str(getattr(e, "id", "") or "").strip() == eid]
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

def _chapter_for_element(task: WritingTask | None, element_id: str) -> tuple[str, str]:
    eid = (element_id or "").strip()
    if not task or not eid or not getattr(task, "outline", None):
        return "", ""
    for ch in list(task.outline.chapters or []):
        ids = [str(x).strip() for x in (getattr(ch, "element_ids", None) or [])]
        if eid in ids:
            title = f"{getattr(ch, 'sequence', '') or ''} {getattr(ch, 'title', '') or ''}".strip()
            return str(getattr(ch, "key", "") or ""), title
    return "", ""

def collect_task_elements_to_library(
    home: Path,
    session: ChatSession,
    message: str,
    payload: dict[str, Any] | None = None,
    *,
    invoke=None,
) -> tuple[str, list[Any]]:
    """将本任务未入库要素收入个人库；成功直接入库并回链。"""
    from report_writing.chat.element_library_pending import queue_element_pending
    from report_writing.chat.helpers import _load_task
    from report_writing.services.element_library import (
        element_content_fingerprint,
        find_library_items_by_identity,
        find_library_items_by_name,
        library_item_from_element,
        link_task_element_to_library_item,
        normalize_element_name,
        upsert_library_item,
    )

    body = dict(payload or {})
    task_id = str(body.get("task_id") or "").strip()
    if not task_id:
        task_id = str(getattr(session, "task_id", "") or "").strip()
    if not task_id:
        task_id = str(getattr(session, "blueprint_id", "") or "").strip()
    if not task_id:
        wiz = dict((session.freeform or {}).get("blueprint_wizard") or {})
        task_id = str(wiz.get("blueprint_id") or "").strip()
    task = _load_task(home, task_id) if task_id else None
    if task is None:
        return "还没有写作任务，先选定蓝本或确认大纲后再把要素收入要素库。", []

    name_hint = str(body.get("element_name") or body.get("name") or "").strip()
    if not name_hint:
        candidates = [
            str(getattr(el, "name", "") or "").strip()
            for el in _unlinked_task_elements(task)
            if str(getattr(el, "name", "") or "").strip()
        ]
        name_hint = resolve_collect_element_name(
            message,
            candidate_names=candidates,
            invoke=invoke,
        )
    element_id = str(body.get("element_id") or "").strip()
    matches = _match_elements(task, name_hint=name_hint, element_id=element_id)

    # 点名后命中多个：挂选要素，勿静默全收
    if not element_id and name_hint and len(matches) > 1:
        from report_writing.chat.action_slots import build_action_slot_clarify

        rows = [
            (str(getattr(el, "id", "") or "").strip(), str(getattr(el, "name", "") or "").strip())
            for el in matches
            if str(getattr(el, "id", "") or "").strip()
        ]
        reply, cards = build_action_slot_clarify(
            session,
            "collect_to_element_library",
            {
                "task_id": task_id,
                "element_name": name_hint,
                "message": (message or "")[:500],
            },
            ["element_id"],
            task=task,
            message=message,
            blueprints=rows,
        )
        return reply, list(cards or [])

    leftovers = _unlinked_task_elements(task)
    if not matches:
        if not leftovers:
            return "本任务没有尚未入库的要素。可先给章节添加要素，或打开要素库查看已有条目。", []
        if name_hint:
            for el in leftovers:
                ck, ct = _chapter_for_element(task, str(el.id))
                queue_element_pending(
                    session,
                    el,
                    task_id=task_id,
                    chapter_key=ck,
                    chapter_title=ct,
                    category=str(getattr(el, "category", "") or "").strip(),
                    tags=list(getattr(el, "tags", None) or []),
                    purpose=str(getattr(el, "purpose", "") or "").strip(),
                )
            return (
                f"没有找到名为「{name_hint}」的未入库要素。"
                f"本任务还有 {len(leftovers)} 个可收录，可直接说「收入要素库」全部收入，"
                "或在右侧文档窗口点「待收录要素」。",
                [],
            )
        # 对话里说「收入要素库」且未点名：直接全部入库（分类已填则带上，未填也可入库）
        matches = leftovers

    imported: list[str] = []
    conflicts: list[ElementNode] = []
    for el in matches:
        name = normalize_element_name(str(getattr(el, "name", "") or el.id))
        category = str(getattr(el, "category", "") or "").strip()
        tags = [str(t).strip() for t in (getattr(el, "tags", None) or []) if str(t).strip()]
        # 内容完全一致的同名库条目：直接回链
        name_hits = find_library_items_by_name(home, name)
        linked = False
        for hit in name_hits:
            if element_content_fingerprint(el) == element_content_fingerprint(hit):
                link_task_element_to_library_item(
                    home, task_id=task_id, element_id=str(el.id), item=hit
                )
                imported.append(hit.name or name)
                linked = True
                break
        if linked:
            continue
        identity_hits = find_library_items_by_identity(
            home, name, category=category, tags=tags
        )
        if identity_hits and str(getattr(el, "library_item_id", "") or "").strip() != str(
            identity_hits[0].id
        ):
            conflicts.append(el)
            ck, ct = _chapter_for_element(task, str(el.id))
            queue_element_pending(
                session,
                el,
                task_id=task_id,
                chapter_key=ck,
                chapter_title=ct,
                category=category,
                tags=tags,
                purpose=str(getattr(el, "purpose", "") or "").strip(),
            )
            continue
        purpose = str(getattr(el, "purpose", "") or "").strip() or str(
            getattr(el, "name", "") or ""
        ).strip()
        item = library_item_from_element(
            el,
            category=category,
            tags=tags,
            purpose=purpose,
        )
        item.name = name
        if identity_hits:
            item.id = identity_hits[0].id
            item.created_at = identity_hits[0].created_at
            item.link_ids = list(identity_hits[0].link_ids or [])
        saved = upsert_library_item(home, item)
        link_task_element_to_library_item(
            home, task_id=task_id, element_id=str(el.id), item=saved
        )
        imported.append(saved.name or name)

    if imported and not conflicts:
        names = "、".join(imported[:6])
        suffix = f"等共 {len(imported)} 个" if len(imported) > 6 else ""
        return f"已将「{names}」{suffix}收入个人要素库，并与本任务要素同步。", []

    if imported and conflicts:
        return (
            f"已收入 {len(imported)} 个；另有 {len(conflicts)} 个与库中名称+分类+标签相同，"
            "请在右侧文档窗口「待收录要素」修改这三项后再收入。",
            [],
        )
    return (
        "要素库已有相同名称+分类+标签的条目，请在右侧文档窗口「待收录要素」修改后再收入。",
        [],
    )
