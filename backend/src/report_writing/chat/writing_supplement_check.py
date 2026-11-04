"""开写前待补充项检查：要点 + 配置要素，非强制。"""

from __future__ import annotations

import json
import re
import secrets
from pathlib import Path
from typing import Any, Callable

from report_writing.domain.elements import file_slot_missing_nos
from report_writing.domain.models import ChatCard, ChatSession, ElementNode, WritingTask
from report_writing.domain.point_items_sync import (
    _element_name,
    parse_slot_from_text,
    point_item_row_needs_supplement,
)

InvokeFn = Callable[[str, str], str]

REGISTRY_FF_KEY = "writing_supplement_registry"
SUPPLEMENT_TODO_RE = re.compile(r"（待补充[：:][^）)]*）|（待填写）|\{\{配置要素\}\}")
SUPPLEMENT_SLOT_RE = re.compile(r"（待补充[：:]([^）)]*)）|（待填写）|\{\{配置要素\}\}")
ELEMENT_ID_IN_LINE_RE = re.compile(r"\{\{?\s*el-[a-z0-9-]+", re.I)
GAP_DISPLAY_BRACE_RE = re.compile(r"\{\{[^}]*\}\}")


def user_facing_gap_line(text: str, *, fallback: str = "") -> str:
    """卡片展示：去掉 {{…}} 与「（待补充：…）」等内部占位，只留可读说明。"""
    t = GAP_DISPLAY_BRACE_RE.sub("", str(text or ""))
    t = SUPPLEMENT_TODO_RE.sub("", t)
    t = re.sub(r"\s{2,}", " ", t).strip()
    t = re.sub(r"[，,；;]\s*[，,；;]+", "，", t)
    t = re.sub(r"[：:]\s*$", "", t).strip("，,；; ：:")
    if t:
        return t
    fb = str(fallback or "").strip()
    return fb or "待补充项"


def text_needs_supplement(text: str) -> bool:
    return bool(SUPPLEMENT_TODO_RE.search(str(text or "")))


def _element_by_id(task: WritingTask) -> dict[str, ElementNode]:
    return {
        str(getattr(el, "id", "") or "").strip(): el
        for el in list(getattr(task, "element_nodes", None) or [])
        if str(getattr(el, "id", "") or "").strip()
    }


def normalize_gap_display(item: dict[str, Any], by_id: dict[str, ElementNode]) -> dict[str, Any]:
    """展示用：有 element_id 时用要素名称，避免 {{el-…}} 等内部标识泄露到卡片。"""
    if not isinstance(item, dict):
        return item
    out = dict(item)
    eid = str(out.get("element_id") or "").strip()
    name = ""
    el = None
    if eid:
        el = by_id.get(eid)
        name = str(getattr(el, "name", "") or "").strip() if el is not None else ""
        if not name:
            name = str(out.get("element_name") or out.get("slot_label") or "").strip()
        if name:
            out["element_name"] = name
        if el is not None:
            out["element_kind"] = str(getattr(el, "kind", "") or "").strip()
    fallback = name or str(out.get("element_name") or out.get("slot_label") or "").strip()
    line = str(out.get("display_line") or "").strip()
    if eid and name and (
        not line
        or eid in line
        or ELEMENT_ID_IN_LINE_RE.search(line)
        or line.startswith("已挂要素：")
    ):
        line = name
    out["display_line"] = user_facing_gap_line(line, fallback=fallback)
    return out


def normalize_gap_list(task: WritingTask, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = _element_by_id(task)
    return [normalize_gap_display(x, by_id) for x in list(items or []) if isinstance(x, dict)]


def supplement_batch_scope(task_id: str, chapter_keys: list[str]) -> str:
    tid = str(task_id or "").strip()
    keys = sorted({str(k).strip() for k in (chapter_keys or []) if str(k).strip()})
    return f"{tid}|{','.join(keys)}"


def _new_gap_id() -> str:
    return f"wsg-{secrets.token_hex(4)}"


def _chapter_label(task: WritingTask, chapter_key: str) -> str:
    key = str(chapter_key or "").strip()
    if not key or task.outline is None:
        return key
    for node in task.outline.chapters or []:
        if str(getattr(node, "key", "") or "") == key:
            seq = str(getattr(node, "sequence", "") or "").strip()
            title = str(getattr(node, "title", "") or "").strip()
            return f"{seq} {title}".strip() or key
    return key


def _gap_from_point_slot(
    task: WritingTask,
    chapter_key: str,
    *,
    point_index: int = -1,
    point_id: str = "",
    slot_label: str = "",
    display_text: str = "",
    element_id: str = "",
    element_name: str = "",
    element_kind: str = "",
) -> dict[str, Any]:
    label = _chapter_label(task, chapter_key)
    slot = str(slot_label or "").strip()
    line = str(display_text or "").strip()
    eid = str(element_id or "").strip()
    ename = str(element_name or "").strip()
    if eid and ename and (not line or eid in line or ELEMENT_ID_IN_LINE_RE.search(line)):
        line = ename
    if not line:
        line = slot or ename or "待补充项"
    line = user_facing_gap_line(line, fallback=slot or ename or "待补充项")
    return {
        "id": _new_gap_id(),
        "chapter_key": chapter_key,
        "chapter_label": label,
        "element_id": str(element_id or "").strip(),
        "element_name": str(element_name or slot or "").strip(),
        "element_kind": str(element_kind or "").strip(),
        "point_index": point_index,
        "point_id": str(point_id or "").strip(),
        "slot_label": slot,
        "display_line": line,
        "reason": f"章节「{label}」写作要点/要素尚需配置",
        "status": "open",
    }


def _gaps_from_point_items(
    task: WritingTask,
    chapter_key: str,
    node,
    by_id: dict[str, ElementNode],
    *,
    home: Path | None,
    seen: set[tuple[str, str]],
) -> list[dict[str, Any]]:
    raw_items = _chapter_point_items(task, chapter_key)
    if not raw_items:
        return []
    out: list[dict[str, Any]] = []
    for idx, row in enumerate(raw_items):
        if isinstance(row, str):
            text = row.strip()
            if not SUPPLEMENT_TODO_RE.search(text):
                continue
            m = SUPPLEMENT_SLOT_RE.search(text)
            slot = (m.group(1) or "").strip() if m and m.lastindex else ""
            out.append(
                _gap_from_point_slot(
                    task,
                    chapter_key,
                    point_index=idx,
                    slot_label=slot,
                    display_text=text[:200],
                )
            )
            continue
        if not isinstance(row, dict):
            continue
        pid = str(row.get("id") or row.get("key") or f"p{idx}").strip()
        text = str(row.get("text") or row.get("label") or "").strip()
        slot = str(row.get("slot") or row.get("element_name") or "").strip()
        eid = str(row.get("element_id") or "").strip()
        if eid and eid in by_id:
            continue
        if _point_row_has_linked_element(task, chapter_key, row, by_id):
            continue
        from report_writing.domain.point_items_sync import point_item_row_needs_supplement

        if not point_item_row_needs_supplement(row):
            continue
        if slot or "{{配置要素}}" in text or SUPPLEMENT_TODO_RE.search(text):
            m = SUPPLEMENT_SLOT_RE.search(text)
            slot_from_text = (m.group(1) or "").strip() if m and m.lastindex else ""
            out.append(
                _gap_from_point_slot(
                    task,
                    chapter_key,
                    point_index=idx,
                    point_id=pid,
                    slot_label=slot or slot_from_text,
                    display_text=text[:240] if text else "",
                )
            )
    return out


def _gaps_from_points_strings(task: WritingTask, chapter_key: str, node) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    by_id = _element_by_id(task)
    structured = _chapter_point_items(task, chapter_key)
    pts = list(getattr(node, "points", None) or [])
    for idx, raw in enumerate(pts):
        if idx < len(structured) and isinstance(structured[idx], dict):
            if _point_row_resolved_element_id(task, chapter_key, structured[idx], by_id):
                continue
            if not point_item_row_needs_supplement(structured[idx]):
                continue
        text = str(raw or "").strip()
        if not text or not SUPPLEMENT_TODO_RE.search(text):
            continue
        m = SUPPLEMENT_SLOT_RE.search(text)
        slot = (m.group(1) or "").strip() if m and m.lastindex else ""
        snippet = text if len(text) <= 120 else f"{text[:117]}…"
        out.append(
            _gap_from_point_slot(
                task,
                chapter_key,
                point_index=idx,
                slot_label=slot,
                display_text=snippet,
            )
        )
    desc = str(getattr(node, "describes", "") or "").strip()
    if desc and SUPPLEMENT_TODO_RE.search(desc):
        m = SUPPLEMENT_SLOT_RE.search(desc)
        slot = (m.group(1) or "").strip() if m and m.lastindex else ""
        out.append(
            _gap_from_point_slot(
                task,
                chapter_key,
                point_index=-1,
                slot_label=slot or "章节说明",
                display_text=desc[:160],
            )
        )
    return out


def _outline_node(task: WritingTask, chapter_key: str):
    if task.outline is None:
        return None
    for node in task.outline.chapters or []:
        if str(getattr(node, "key", "") or "") == str(chapter_key or "").strip():
            return node
    return None


def _chapter_point_items(task: WritingTask, chapter_key: str) -> list[dict[str, Any]]:
    """与保存/展示一致：point_items + element_ids 补全后的要点行。"""
    node = _outline_node(task, chapter_key)
    if node is None:
        return []
    from report_writing.domain.point_items_sync import chapter_point_items_from_payload

    by_id = _element_by_id(task)
    payload = {
        "points": list(getattr(node, "points", None) or []),
        "point_items": list(getattr(node, "point_items", None) or []),
        "element_ids": list(getattr(node, "element_ids", None) or []),
    }
    return chapter_point_items_from_payload(payload, elements_by_id=by_id)


def _names_overlap(a: str, b: str) -> bool:
    a = str(a or "").strip()
    b = str(b or "").strip()
    if not a or not b:
        return False
    return a == b or a in b or b in a


def _point_row_resolved_element_id(
    task: WritingTask,
    chapter_key: str,
    row: dict[str, Any] | None,
    by_id: dict[str, ElementNode],
) -> str:
    """要点行已关联的有效要素（含 element_ids 按 slot/名称补全后的关联）。"""
    if not isinstance(row, dict):
        return ""
    eid = str(row.get("element_id") or "").strip()
    if eid and by_id.get(eid) is not None:
        return eid
    node = _outline_node(task, chapter_key)
    if node is None:
        return ""
    slot = str(row.get("slot") or row.get("element_name") or "").strip()
    if not slot:
        slot = parse_slot_from_text(str(row.get("text") or row.get("label") or ""))
    if not slot:
        return ""
    for cid in list(getattr(node, "element_ids", None) or []):
        cid = str(cid or "").strip()
        el = by_id.get(cid)
        if el is None:
            continue
        ename = str(getattr(el, "name", "") or "").strip()
        if _names_overlap(slot, ename):
            return cid
    return ""


def _point_row_has_linked_element(
    task: WritingTask,
    chapter_key: str,
    row: dict[str, Any],
    by_id: dict[str, ElementNode],
) -> bool:
    return bool(_point_row_resolved_element_id(task, chapter_key, row, by_id))


def _element_ids_linked_on_point_items(
    task: WritingTask,
    chapter_key: str,
) -> set[str]:
    by_id = _element_by_id(task)
    out: set[str] = set()
    for row in _chapter_point_items(task, chapter_key):
        if isinstance(row, dict):
            eid = _point_row_resolved_element_id(task, chapter_key, row, by_id)
            if eid:
                out.add(eid)
    return out


def collapse_supplement_gaps(
    task: WritingTask,
    items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """去掉已关联要点上的项，并合并同一章「要点 + 要素」重复待补。"""
    by_id = _element_by_id(task)
    stage1: list[dict[str, Any]] = []
    for g in list(items or []):
        if not isinstance(g, dict):
            continue
        ck = str(g.get("chapter_key") or "").strip()
        eid = str(g.get("element_id") or "").strip()
        if eid and eid in _element_ids_linked_on_point_items(task, ck):
            continue
        try:
            pi = int(g.get("point_index")) if g.get("point_index") is not None else -1
        except (TypeError, ValueError):
            pi = -1
        if pi >= 0 and not eid:
            _, row = _point_item_row(task, ck, pi, str(g.get("point_id") or ""))
            if _point_row_resolved_element_id(task, ck, row, by_id):
                continue
        stage1.append(g)

    by_ch: dict[str, list[dict[str, Any]]] = {}
    for g in stage1:
        ck = str(g.get("chapter_key") or "").strip()
        by_ch.setdefault(ck, []).append(g)

    out: list[dict[str, Any]] = []
    for ck, group in by_ch.items():
        elem_gaps = [g for g in group if str(g.get("element_id") or "").strip()]
        other = [g for g in group if not str(g.get("element_id") or "").strip()]
        elem_names: list[str] = []
        for eg in elem_gaps:
            el = by_id.get(str(eg.get("element_id") or ""))
            if el is not None:
                elem_names.append(str(getattr(el, "name", "") or ""))
        out.extend(elem_gaps)
        for pg in other:
            slot = str(pg.get("slot_label") or "").strip()
            if elem_names and slot and any(_names_overlap(slot, n) for n in elem_names):
                continue
            try:
                pi = int(pg.get("point_index")) if pg.get("point_index") is not None else -1
            except (TypeError, ValueError):
                pi = -1
            if pi >= 0 and elem_names:
                _, row = _point_item_row(task, ck, pi, str(pg.get("point_id") or ""))
                if isinstance(row, dict):
                    en = str(row.get("element_name") or row.get("slot") or "").strip()
                    if en and any(_names_overlap(en, n) for n in elem_names):
                        continue
            out.append(pg)
    return out


def element_needs_supplement(
    node: ElementNode,
    task: WritingTask,
    home: Path | None,
) -> bool:
    kind = str(getattr(node, "kind", "") or "").strip()
    if kind == "rag_search":
        kind = "file"
    if kind == "kv":
        pairs = list(getattr(node, "kv_pairs", None) or [])
        if pairs:
            for item in pairs:
                if hasattr(item, "key"):
                    v = str(getattr(item, "value", "") or "").strip()
                elif isinstance(item, dict):
                    v = str(item.get("value") or "").strip()
                else:
                    v = ""
                if v:
                    return False
            return True
        return not str(getattr(node, "value", "") or "").strip()
    if kind == "fixed":
        return not str(getattr(node, "value", "") or "").strip()
    if kind in {"file", "rag_search"}:
        files = list(getattr(node, "files", None) or [])
        if not files:
            return True
        return not any(
            str(getattr(f, "parse_status", "") or (f.get("parse_status") if isinstance(f, dict) else ""))
            == "parsed"
            for f in files
        )
    if kind == "file_slot":
        pack_id = str(getattr(task, "materials_pack_id", "") or "").strip()
        missing = file_slot_missing_nos(node, materials_pack_id=pack_id, home=home)
        return bool(missing) or not list(getattr(node, "file_slots", None) or [])
    if kind == "llm_generate":
        return not str(getattr(node, "prompt", "") or "").strip()
    if kind == "image":
        from report_writing.services.element_images import absolute_image_path

        source = str(getattr(node, "image_source", "") or "upload").strip() or "upload"
        if source == "llm":
            return not str(getattr(node, "prompt", "") or "").strip()
        if str(getattr(node, "image_path", "") or "").strip():
            return False
        return not absolute_image_path(home, str(getattr(task, "id", "") or ""), node) if home else True
    if kind == "node_ref":
        return not str(getattr(node, "ref_key", "") or "").strip()
    return not str(getattr(node, "value", "") or "").strip()


def _point_item_row(task: WritingTask, chapter_key: str, point_index: int, point_id: str = ""):
    pid = str(point_id or "").strip()
    items = _chapter_point_items(task, chapter_key)
    if pid:
        for idx, row in enumerate(items):
            if isinstance(row, dict):
                rid = str(row.get("id") or row.get("key") or "").strip()
                if rid == pid:
                    return idx, row
    if point_index >= 0 and point_index < len(items):
        return point_index, items[point_index]
    return -1, None


def _element_configured(task: WritingTask, element_id: str, home: Path | None) -> bool:
    eid = str(element_id or "").strip()
    if not eid:
        return False
    el = _element_by_id(task).get(eid)
    if el is None:
        return False
    return not element_needs_supplement(el, task, home)


def _point_row_satisfied(task: WritingTask, chapter_key: str, row: dict[str, Any], home: Path | None) -> bool:
    by_id = _element_by_id(task)
    if _point_row_resolved_element_id(task, chapter_key, row, by_id):
        return True
    eid = str(row.get("element_id") or "").strip()
    if eid and _element_configured(task, eid, home):
        return True
    needs = bool(row.get("needs_supplement"))
    text = str(row.get("text") or row.get("label") or "").strip()
    if not needs and not SUPPLEMENT_TODO_RE.search(text):
        return True
    return False


def gap_resolved(task: WritingTask, gap: dict[str, Any], home: Path | None) -> bool:
    """该待补项是否已在章节/要素中配置完成（不应再出现在开写前列表）。"""
    ck = str(gap.get("chapter_key") or "").strip()
    if not ck:
        return False
    node = _outline_node(task, ck)
    if node is None:
        return False
    eid = str(gap.get("element_id") or "").strip()
    if eid:
        if eid in _element_ids_linked_on_point_items(task, ck):
            return True
        return _element_configured(task, eid, home)
    pi = int(gap.get("point_index")) if gap.get("point_index") is not None else -1
    pid = str(gap.get("point_id") or "").strip()
    _idx, row = _point_item_row(task, ck, pi, pid)
    if isinstance(row, dict):
        return _point_row_satisfied(task, ck, row, home)
    slot = str(gap.get("slot_label") or "").strip()
    if slot:
        for _i, raw in enumerate(_chapter_point_items(task, ck)):
            if not isinstance(raw, dict):
                continue
            rs = str(raw.get("slot") or raw.get("element_name") or "").strip()
            if not rs or (rs != slot and rs not in slot and slot not in rs):
                continue
            if _point_row_satisfied(task, ck, raw, home):
                return True
    if pi >= 0 and not list(getattr(node, "point_items", None) or []):
        pts = list(getattr(node, "points", None) or [])
        if pi < len(pts) and not SUPPLEMENT_TODO_RE.search(str(pts[pi] or "")):
            return True
    return False


def mark_resolved_gaps_in_registry(
    session: ChatSession,
    task: WritingTask,
    home: Path | None,
) -> None:
    reg = load_registry(session)
    items = list(reg.get("items") or [])
    changed = False
    for item in items:
        if not isinstance(item, dict):
            continue
        if str(item.get("status") or "") != "open":
            continue
        if gap_resolved(task, item, home):
            item["status"] = "filled"
            changed = True
    if changed:
        reg["items"] = items
        save_registry(session, reg)
        sync_registry_to_task(task, reg)


def gaps_still_open_for_task(
    task: WritingTask,
    items: list[dict[str, Any]],
    home: Path | None,
) -> list[dict[str, Any]]:
    by_id = _element_by_id(task)
    dedupe: set[tuple[str, ...]] = set()
    out: list[dict[str, Any]] = []
    for item in list(items or []):
        if not isinstance(item, dict):
            continue
        if str(item.get("status") or "open") != "open":
            continue
        if gap_resolved(task, item, home):
            continue
        ck = str(item.get("chapter_key") or "").strip()
        eid = str(item.get("element_id") or "").strip()
        pi = int(item.get("point_index")) if item.get("point_index") is not None else -999
        if eid:
            sig: tuple[str, ...] = ("e", ck, eid)
        elif pi >= 0:
            sig = ("p", ck, str(pi))
        else:
            sig = ("s", ck, str(item.get("slot_label") or "")[:80])
        if sig in dedupe:
            continue
        dedupe.add(sig)
        out.append(normalize_gap_display(item, by_id))
    return collapse_supplement_gaps(task, out)


def _gap_from_element(
    task: WritingTask,
    chapter_key: str,
    node: ElementNode,
    *,
    home: Path | None,
) -> dict[str, Any]:
    name = str(getattr(node, "name", "") or "").strip() or str(getattr(node, "id", "") or "")
    label = _chapter_label(task, chapter_key)
    return {
        "id": _new_gap_id(),
        "chapter_key": chapter_key,
        "chapter_label": label,
        "element_id": str(getattr(node, "id", "") or "").strip(),
        "element_name": name,
        "element_kind": str(getattr(node, "kind", "") or "").strip(),
        "display_line": user_facing_gap_line(name, fallback=name),
        "reason": f"章节「{label}」关联的要素「{name}」尚未配置完整",
        "status": "open",
    }


def collect_deterministic_gaps(
    task: WritingTask,
    chapter_keys: list[str],
    home: Path | None,
) -> list[dict[str, Any]]:
    keys = [str(k).strip() for k in (chapter_keys or []) if str(k).strip()]
    by_id = {
        str(getattr(el, "id", "") or "").strip(): el
        for el in list(getattr(task, "element_nodes", None) or [])
        if str(getattr(el, "id", "") or "").strip()
    }
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for ck in keys:
        node = _outline_node(task, ck)
        if node is None or bool(getattr(node, "structure_only", False)):
            continue
        linked_on_points = _element_ids_linked_on_point_items(task, ck)
        for eid in list(getattr(node, "element_ids", None) or []):
            eid = str(eid or "").strip()
            if not eid:
                continue
            if eid in linked_on_points:
                continue
            el = by_id.get(eid)
            if el is None or not element_needs_supplement(el, task, home):
                continue
            sig = (ck, eid)
            if sig in seen:
                continue
            seen.add(sig)
            out.append(_gap_from_element(task, ck, el, home=home))
        item_gaps = _gaps_from_point_items(task, ck, node, by_id, home=home, seen=seen)
        out.extend(item_gaps)
        # 有结构化 point_items 时不再扫 legacy points 字符串，避免已关联要素仍因旧文案报待补
        if not _chapter_point_items(task, ck):
            out.extend(_gaps_from_points_strings(task, ck, node))
    return collapse_supplement_gaps(task, out)


def _merge_gap_lists(primary: list[dict[str, Any]], extra: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in list(primary or []) + list(extra or []):
        if not isinstance(item, dict):
            continue
        ck = str(item.get("chapter_key") or "").strip()
        eid = str(item.get("element_id") or "").strip()
        name = str(item.get("element_name") or item.get("display_line") or "").strip()
        sig = f"{ck}|{eid}|{name}"
        if sig in seen:
            continue
        seen.add(sig)
        if not str(item.get("id") or "").strip():
            item = {**item, "id": _new_gap_id()}
        out.append(item)
    return out


def _point_item_rows_for_chapter(
    task: WritingTask,
    chapter_key: str,
    home: Path | None,
) -> list[dict[str, Any]]:
    by_id = _element_by_id(task)
    items = _chapter_point_items(task, chapter_key)
    rows: list[dict[str, Any]] = []
    for i, row in enumerate(items):
        if not isinstance(row, dict):
            continue
        eid = str(row.get("element_id") or "").strip()
        el = by_id.get(eid) if eid else None
        ename = _element_name(el) if el else str(row.get("element_name") or "").strip()
        need_cfg = not _point_row_has_linked_element(task, chapter_key, row, by_id) and (
            bool(row.get("needs_supplement"))
            or text_needs_supplement(str(row.get("text") or ""))
        )
        rows.append(
            {
                "point_index": i,
                "point_id": str(row.get("id") or row.get("key") or f"p{i}").strip(),
                "text": str(row.get("text") or "").strip(),
                "slot": str(row.get("slot") or "").strip(),
                "element_id": eid,
                "element_name": ename,
                "needs_config": need_cfg,
            }
        )
    return rows


def merge_llm_gaps(
    task: WritingTask,
    chapter_keys: list[str],
    deterministic: list[dict[str, Any]],
    *,
    invoke: InvokeFn | None = None,
) -> list[dict[str, Any]]:
    if invoke is None:
        return deterministic
    keys = [str(k).strip() for k in (chapter_keys or []) if str(k).strip()]
    table_lines: list[str] = []
    for ck in keys[:12]:
        label = _chapter_label(task, ck)
        rows = _point_item_rows_for_chapter(task, ck, None)
        if not rows:
            continue
        table_lines.append(f"## {label} (key={ck})")
        for r in rows:
            if not r.get("needs_config"):
                continue
            table_lines.append(
                " | ".join(
                    [
                        f"point_index={r['point_index']}",
                        f"待补项={r.get('slot') or '—'}",
                        f"要点={str(r.get('text') or '')[:100]}",
                        f"关联要素={r.get('element_name') or '未关联'}",
                        f"element_id={r.get('element_id') or '—'}",
                    ]
                )
            )
    if not table_lines:
        return deterministic
    det_summary = json.dumps(
        [
            {
                "chapter_key": g.get("chapter_key"),
                "point_index": g.get("point_index"),
                "element_id": g.get("element_id"),
                "slot_label": g.get("slot_label"),
            }
            for g in deterministic[:30]
        ],
        ensure_ascii=False,
    )
    system = (
        "你是报告写作助手。用户按「写作要点 ↔ 关联要素」配置章节，开写前只检查下表仍缺配置的要点行。"
        "只输出 JSON 数组；每项必须对应表中一条 point_index，禁止新增表外建议。"
        "字段：chapter_key, point_index, element_id(可空), element_name, display_line, reason。"
        "display_line 用「待补项或要素名：{{配置要素}}」；element_id 须来自该行已关联要素或留空。"
        "禁止编造具体项目事实。"
    )
    user = (
        "写作要点与关联要素（仅列出仍缺配置的行）：\n"
        + "\n".join(table_lines)
        + f"\n\n规则扫描已识别项：{det_summary}"
    )
    try:
        raw = (invoke(system, user) or "").strip()
        start = raw.find("[")
        end = raw.rfind("]")
        if start < 0 or end <= start:
            return deterministic
        data = json.loads(raw[start : end + 1])
        if not isinstance(data, list):
            return deterministic
        llm_items: list[dict[str, Any]] = []
        for row in data:
            if not isinstance(row, dict):
                continue
            ck = str(row.get("chapter_key") or "").strip()
            if not ck:
                continue
            pi_raw = row.get("point_index")
            try:
                pi = int(pi_raw) if pi_raw is not None else -1
            except (TypeError, ValueError):
                pi = -1
            if pi < 0:
                continue
            rows = _point_item_rows_for_chapter(task, ck, None)
            if pi >= len(rows) or not rows[pi].get("needs_config"):
                continue
            src = rows[pi]
            llm_items.append(
                {
                    "id": _new_gap_id(),
                    "chapter_key": ck,
                    "chapter_label": _chapter_label(task, ck),
                    "point_index": pi,
                    "point_id": str(src.get("point_id") or "").strip(),
                    "slot_label": str(src.get("slot") or row.get("element_name") or "").strip(),
                    "element_id": str(row.get("element_id") or src.get("element_id") or "").strip(),
                    "element_name": str(row.get("element_name") or src.get("element_name") or "").strip(),
                    "display_line": str(row.get("display_line") or "").strip()
                    or str(src.get("slot") or src.get("element_name") or "待补充项"),
                    "reason": str(row.get("reason") or "").strip(),
                    "status": "open",
                }
            )
        return _merge_gap_lists(deterministic, llm_items)
    except Exception:
        return deterministic


def enrich_gaps_for_display(task: WritingTask, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return normalize_gap_list(task, items)


def load_registry(session: ChatSession) -> dict[str, Any]:
    raw = (session.freeform or {}).get(REGISTRY_FF_KEY)
    return dict(raw) if isinstance(raw, dict) else {}


def save_registry(session: ChatSession, registry: dict[str, Any]) -> None:
    session.freeform = {**(session.freeform or {}), REGISTRY_FF_KEY: dict(registry or {})}


def sync_registry_to_task(task: WritingTask, registry: dict[str, Any]) -> None:
    items = list(registry.get("items") or [])
    task.writing_supplement_gaps = [dict(x) for x in items if isinstance(x, dict)]


def open_gaps(registry: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        dict(x)
        for x in list(registry.get("items") or [])
        if isinstance(x, dict) and str(x.get("status") or "open") == "open"
    ]


def effective_open_gaps(
    task: WritingTask,
    registry: dict[str, Any],
    home: Path | None,
) -> list[dict[str, Any]]:
    """按当前章节/要素状态过滤，已配置完成的不算待补。"""
    return gaps_still_open_for_task(task, open_gaps(registry), home)


def run_supplement_check(
    home: Path,
    session: ChatSession,
    task: WritingTask,
    chapter_keys: list[str],
    *,
    invoke: InvokeFn | None = None,
) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    from report_writing.domain.point_items_sync import normalize_chapter_point_items_on_node

    for ck in [str(k).strip() for k in (chapter_keys or []) if str(k).strip()]:
        node = _outline_node(task, ck)
        if node is not None:
            normalize_chapter_point_items_on_node(task, node)
    det = collect_deterministic_gaps(task, chapter_keys, home)
    merged = det if not det else merge_llm_gaps(task, chapter_keys, det, invoke=invoke)
    items = normalize_gap_list(task, merged)
    for item in items:
        if isinstance(item, dict) and gap_resolved(task, item, home):
            item["status"] = "filled"
    scope = supplement_batch_scope(task.id, chapter_keys)
    registry = {
        "scope": scope,
        "task_id": task.id,
        "chapter_keys": list(chapter_keys),
        "items": items,
        "by_chapter": {},
        "skipped": False,
        "checked": True,
    }
    save_registry(session, registry)
    sync_registry_to_task(task, registry)
    from report_writing.services.store import TaskStore

    TaskStore(home / "tasks" / task.id).save(task)
    open_items = effective_open_gaps(task, registry, home)
    display_items = gaps_still_open_for_task(task, open_items, home)
    reply = (
        "开写前我对齐了各章**写作要点**与每条要点**关联的配置要素**，"
        f"{'下面有 ' + str(len(display_items)) + ' 处还空着' if display_items else '目前看不出必须补的配置'}；"
        "补上后检索会更准，**不补也可以先写**（正文里会用待补充占位）。"
        "（此检查仅在开始写作前进行。）"
    )
    return reply, display_items, registry


def supplement_check_card(
    task: WritingTask,
    chapter_keys: list[str],
    *,
    writing_pace: str = "",
    items: list[dict[str, Any]] | None = None,
    readonly: bool = False,
    home: Path | None = None,
) -> ChatCard | None:
    display_items = gaps_still_open_for_task(task, list(items or []), home=home)
    if not display_items:
        return None
    return ChatCard(
        type="writing_supplement_check",
        title="开写前待补充提示",
        payload={
            "task_id": task.id,
            "chapter_keys": list(chapter_keys or []),
            "writing_pace": str(writing_pace or "").strip(),
            "items": display_items,
            "readonly": bool(readonly),
        },
    )


def mark_supplement_skipped(session: ChatSession, scope: str) -> None:
    reg = load_registry(session)
    reg["skipped"] = True
    reg["scope"] = str(scope or reg.get("scope") or "").strip()
    save_registry(session, reg)


def reset_supplement_gate_for_scope(
    session: ChatSession, task_id: str, chapter_keys: list[str]
) -> None:
    """保存章节配置后允许同 scope 再次开写前检查（覆盖「先这样开写」）。"""
    scope = supplement_batch_scope(task_id, chapter_keys)
    reg = load_registry(session)
    if str(reg.get("scope") or "") != scope:
        return
    reg["checked"] = False
    reg["skipped"] = False
    save_registry(session, reg)


def supplement_gate_needed(
    session: ChatSession,
    task: WritingTask,
    chapter_keys: list[str],
    *,
    force_skip: bool = False,
    home: Path | None = None,
) -> bool:
    if force_skip:
        return False
    keys = [str(k).strip() for k in (chapter_keys or []) if str(k).strip()]
    scope = supplement_batch_scope(task.id, keys)
    reg = load_registry(session)
    if str(reg.get("scope") or "") != scope:
        return True
    if reg.get("skipped"):
        return False
    if not reg.get("checked"):
        return True
    if effective_open_gaps(task, reg, home):
        return True
    # 会话里已「检查过」但章集/配置变了：对当前 scope 再扫一遍，避免漏检（如文档窗「全部开写」）
    if home is not None:
        fresh = collect_deterministic_gaps(task, keys, home)
        if not fresh:
            return False
        for gap in fresh:
            if not gap_resolved(task, gap, home):
                return True
    return False


def dismiss_gap(
    home: Path,
    session: ChatSession,
    task: WritingTask,
    gap_id: str,
    *,
    dissociate: bool = True,
) -> WritingTask:
    from report_writing.chat.writing import _apply_dissociate_chapter_element
    from report_writing.services.store import TaskStore

    gid = str(gap_id or "").strip()
    reg = load_registry(session)
    items = list(reg.get("items") or [])
    target = next((x for x in items if isinstance(x, dict) and str(x.get("id") or "") == gid), None)
    if target is None:
        return task
    ck = str(target.get("chapter_key") or "").strip()
    eid = str(target.get("element_id") or "").strip()
    for item in items:
        if isinstance(item, dict) and str(item.get("id") or "") == gid:
            item["status"] = "dismissed"
    reg["items"] = items
    save_registry(session, reg)
    if dissociate and ck and eid:
        try:
            _apply_dissociate_chapter_element(task, ck, eid)
        except ValueError:
            pass
    sync_registry_to_task(task, reg)
    TaskStore(home / "tasks" / task.id).save(task)
    return task


def mark_gaps_filled_for_element(session: ChatSession, task: WritingTask, element_id: str) -> None:
    eid = str(element_id or "").strip()
    if not eid:
        return
    reg = load_registry(session)
    changed = False
    for item in list(reg.get("items") or []):
        if not isinstance(item, dict):
            continue
        if str(item.get("element_id") or "").strip() == eid and str(item.get("status") or "") == "open":
            item["status"] = "filled"
            changed = True
    if changed:
        save_registry(session, reg)
        sync_registry_to_task(task, reg)


def snapshot_chapter_gaps_at_write(registry: dict[str, Any], chapter_key: str) -> None:
    ck = str(chapter_key or "").strip()
    if not ck:
        return
    by_ch = dict(registry.get("by_chapter") or {})
    ids = [
        str(x.get("id") or "")
        for x in open_gaps(registry)
        if str(x.get("chapter_key") or "") == ck
    ]
    from datetime import datetime, timezone

    by_ch[ck] = {
        "at_write": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "gap_ids": ids,
    }
    registry["by_chapter"] = by_ch


def finish_summary_text(registry: dict[str, Any]) -> str:
    open_items = open_gaps(registry)
    if not open_items:
        return ""
    bits: list[str] = []
    for item in open_items[:8]:
        label = str(item.get("chapter_label") or item.get("chapter_key") or "").strip()
        name = str(item.get("element_name") or item.get("display_line") or "待补充").strip()
        bits.append(f"{label} {name}".strip())
    tail = "；".join(bits)
    if len(open_items) > 8:
        tail += f" 等共 {len(open_items)} 处"
    return (
        f"这轮写完了。还有 **{len(open_items)}** 处配置要素没补：{tail}。"
        "有空在对话里点 **去配置**，或到右侧文档窗「章节配置」里改；"
        "不补也不影响已生成的正文，只是后续重跑检索可能仍缺资料。"
    )
