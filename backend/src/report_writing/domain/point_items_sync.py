"""写作要点 points 与结构化 point_items 双向同步。"""

from __future__ import annotations

import re
from typing import Any

SUPPLEMENT_TODO_RE = re.compile(r"（待补充[：:][^）)]*）|（待填写）|\{\{配置要素\}\}")
SUPPLEMENT_SLOT_RE = re.compile(r"（待补充[：:]([^）)]*)）|（待填写）|\{\{配置要素\}\}")
ELEMENT_ID_TOKEN_RE = re.compile(r"^el-[a-z0-9-]+$", re.I)


def is_element_id_token(value: Any) -> bool:
    """内部要素 id（el-时间戳 / el-uuid）不能当展示名。"""
    return bool(ELEMENT_ID_TOKEN_RE.fullmatch(str(value or "").strip()))


def human_label(*candidates: Any) -> str:
    for raw in candidates:
        text = str(raw or "").strip()
        if text and not is_element_id_token(text):
            return text
    return ""


def parse_slot_from_text(text: str) -> str:
    raw = str(text or "").strip()
    if not raw:
        return ""
    m = SUPPLEMENT_SLOT_RE.search(raw)
    if m and m.lastindex:
        return str(m.group(1) or "").strip()
    return ""


def text_needs_supplement(text: str) -> bool:
    return bool(SUPPLEMENT_TODO_RE.search(str(text or "")))


def strip_supplement_markers(text: str) -> str:
    """去掉要点/说明里的「（待补充：…）」类占位，保留其余表述。"""
    t = SUPPLEMENT_TODO_RE.sub("", str(text or ""))
    t = re.sub(r"\s{2,}", " ", t).strip()
    t = re.sub(r"[，,；;]\s*[，,；;]+", "，", t)
    return t.strip("，,；; ")


def point_item_row_needs_supplement(row: Any) -> bool:
    if not isinstance(row, dict):
        return text_needs_supplement(str(row or ""))
    if str(row.get("element_id") or "").strip():
        return False
    text = str(row.get("text") or row.get("label") or "")
    if row.get("needs_supplement") is False and not text_needs_supplement(text):
        return False
    return bool(row.get("needs_supplement")) or text_needs_supplement(text)


def chapter_has_pending_supplements(items: list[Any] | None) -> bool:
    for row in list(items or []):
        if not isinstance(row, dict):
            continue
        if not str(row.get("text") or row.get("label") or "").strip():
            continue
        if point_item_row_needs_supplement(row):
            return True
    return False


def writing_text_for_point_row(row: dict[str, Any]) -> str:
    text = str(row.get("text") or row.get("label") or "").strip()
    if not text:
        return ""
    if point_item_row_needs_supplement(row):
        return text
    return strip_supplement_markers(text)


def points_for_writing_from_node(node: Any) -> list[str]:
    """写作/同步 brief 用的要点：已关联要素或无待补标记的行去掉占位符。"""
    items = list(getattr(node, "point_items", None) or [])
    if items:
        lines = [
            writing_text_for_point_row(r)
            for r in items
            if isinstance(r, dict)
        ]
        return [t for t in lines if t]
    raw = [str(p or "").strip() for p in list(getattr(node, "points", None) or []) if str(p or "").strip()]
    if any(text_needs_supplement(p) for p in raw):
        return raw
    return [strip_supplement_markers(p) for p in raw if strip_supplement_markers(p)]


_AUX_SUPPLEMENT_HEADER_RE = re.compile(r"【建议补充资料】[\s\S]*?(?=\n\n|\Z)")


def sanitize_writing_meta_text(text: str, *, allow_supplement_markers: bool) -> str:
    raw = str(text or "").strip()
    if not raw:
        return ""
    if allow_supplement_markers:
        return raw
    cleaned = _AUX_SUPPLEMENT_HEADER_RE.sub("", raw).strip()
    cleaned = strip_supplement_markers(cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned


def aux_prompt_for_writing_from_node(node: Any) -> str:
    items = list(getattr(node, "point_items", None) or [])
    pending = (
        chapter_has_pending_supplements(items)
        if items
        else any(text_needs_supplement(p) for p in (getattr(node, "points", None) or []))
    )
    aux = str(getattr(node, "aux_prompt", "") or "").strip()
    if pending:
        return aux[:800]
    cleaned = sanitize_writing_meta_text(aux, allow_supplement_markers=False)
    body = "\n".join(points_for_writing_from_node(node)[:12]).strip()
    if body:
        return (cleaned + "\n\n" + body).strip()[:800] if cleaned else body[:800]
    return cleaned[:800]


def describes_for_writing_from_node(node: Any) -> str:
    items = list(getattr(node, "point_items", None) or [])
    pending = chapter_has_pending_supplements(items) if items else False
    desc = sanitize_writing_meta_text(
        str(getattr(node, "describes", "") or ""),
        allow_supplement_markers=pending,
    )
    if not pending:
        desc = re.sub(r"需填报处[^。；;\n]*[。；;]?", "", desc)
    return desc[:500]


def pending_supplement_slots(items: list[Any] | None) -> list[str]:
    """仍待补充的 slot 名称（未关联要素且 needs_supplement）。"""
    seen: set[str] = set()
    out: list[str] = []
    for row in list(items or []):
        if not isinstance(row, dict) or not point_item_row_needs_supplement(row):
            continue
        slot = str(row.get("slot") or row.get("element_name") or "").strip()
        if not slot:
            slot = parse_slot_from_text(str(row.get("text") or ""))
        if slot and slot not in seen:
            seen.add(slot)
            out.append(slot)
    return out[:8]


def points_from_point_items(items: list[Any] | None) -> list[str]:
    out: list[str] = []
    for row in list(items or []):
        if isinstance(row, str):
            line = row.strip()
            if line:
                out.append(line)
            continue
        if not isinstance(row, dict):
            continue
        line = str(row.get("text") or row.get("label") or "").strip()
        if line:
            out.append(line)
    return out


def sync_point_items_from_points(
    points: list[str],
    prev_items: list[Any] | None,
) -> list[dict[str, Any]]:
    """保存要点文本时：按行更新 point_items，同索引保留 element_id / slot / id。"""
    prev = [x for x in list(prev_items or []) if isinstance(x, dict)]
    out: list[dict[str, Any]] = []
    lines = [str(p or "").strip() for p in list(points or []) if str(p or "").strip()]
    for i, line in enumerate(lines):
        prev_row = prev[i] if i < len(prev) else {}
        pid = str(prev_row.get("id") or prev_row.get("key") or f"p{i}").strip() or f"p{i}"
        slot = str(prev_row.get("slot") or prev_row.get("element_name") or "").strip()
        if not slot:
            slot = parse_slot_from_text(line)
        needs = text_needs_supplement(line) or bool(prev_row.get("needs_supplement"))
        row: dict[str, Any] = {"id": pid, "text": line}
        if slot:
            row["slot"] = slot
        eid = str(prev_row.get("element_id") or "").strip()
        if eid:
            row["element_id"] = eid
        elif needs:
            row["needs_supplement"] = True
        ename = human_label(prev_row.get("element_name"), slot)
        if ename:
            row["element_name"] = ename
        out.append(row)
    return out


def sync_points_from_point_items(items: list[Any] | None) -> list[str]:
    """从 point_items 生成 points（与 points_from_point_items 相同，语义别名）。"""
    return points_from_point_items(items)


def normalize_llm_point_item(row: Any, index: int = 0) -> dict[str, Any] | None:
    """模型返回的单条 point_items 行 → 标准 dict。"""
    if isinstance(row, str):
        text = row.strip()
        if not text:
            return None
        out: dict[str, Any] = {"id": f"p{index}", "text": text}
        slot = parse_slot_from_text(text)
        if slot:
            out["slot"] = slot
        if text_needs_supplement(text):
            out["needs_supplement"] = True
        return out
    if not isinstance(row, dict):
        return None
    text = str(row.get("text") or row.get("label") or "").strip()
    if not text:
        return None
    pid = str(row.get("id") or row.get("key") or f"p{index}").strip() or f"p{index}"
    out = {"id": pid, "text": text}
    slot = human_label(row.get("slot"), row.get("element_name")) or parse_slot_from_text(text)
    if slot:
        out["slot"] = slot
    if text_needs_supplement(text) or row.get("needs_supplement"):
        out["needs_supplement"] = True
    eid = str(row.get("element_id") or "").strip()
    if eid:
        out["element_id"] = eid
        out["needs_supplement"] = False
    ename = human_label(row.get("element_name"), out.get("slot"))
    if ename:
        out["element_name"] = ename
    return out


def merge_generated_point_items(
    points: list[str] | None,
    raw_items: list[Any] | None,
    prev_items: list[Any] | None = None,
) -> tuple[list[str], list[dict[str, Any]]]:
    """分析/生成落盘：优先 structured point_items，否则由 points 同步生成。"""
    prev = [x for x in list(prev_items or []) if isinstance(x, dict)]
    if isinstance(raw_items, list) and raw_items:
        items: list[dict[str, Any]] = []
        for i, row in enumerate(raw_items):
            norm = normalize_llm_point_item(row, i)
            if norm:
                items.append(norm)
        if items:
            return points_from_point_items(items), items
    lines = [str(p or "").strip() for p in list(points or []) if str(p or "").strip()]
    return lines, sync_point_items_from_points(lines, prev)


def apply_points_to_outline_node(
    node: Any,
    points: list[str] | None,
    *,
    raw_point_items: list[Any] | None = None,
) -> None:
    """同时写入 node.points 与 node.point_items（OutlineNode 或兼容属性）。"""
    prev = list(getattr(node, "point_items", None) or [])
    pts, items = merge_generated_point_items(points, raw_point_items, prev_items=prev)
    node.points = pts
    node.point_items = items


def _element_name(el: Any) -> str:
    if el is None:
        return ""
    if isinstance(el, dict):
        return human_label(el.get("name"))
    return human_label(getattr(el, "name", ""))


def link_point_items_to_element_ids(
    items: list[dict[str, Any]] | None,
    element_ids: list[str] | None,
    elements_by_id: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """按待补项名与章 element_ids，补全 point_items 上的 element_id / element_name。"""
    by_id = dict(elements_by_id or {})
    eids = [str(x or "").strip() for x in list(element_ids or []) if str(x or "").strip()]
    out: list[dict[str, Any]] = []
    for row in list(items or []):
        if not isinstance(row, dict):
            continue
        next_row = dict(row)
        eid = str(next_row.get("element_id") or "").strip()
        if not eid and eids:
            slot = str(next_row.get("slot") or next_row.get("element_name") or "").strip()
            if slot:
                for cid in eids:
                    el = by_id.get(cid)
                    name = _element_name(el)
                    if not name:
                        continue
                    if name == slot or name in slot or slot in name:
                        eid = cid
                        break
        if eid and eid in eids:
            el = by_id.get(eid)
            next_row["element_id"] = eid
            name = human_label(
                _element_name(el),
                next_row.get("element_name"),
                next_row.get("slot"),
            )
            if name:
                next_row["element_name"] = name
            elif "element_name" in next_row and is_element_id_token(next_row.get("element_name")):
                del next_row["element_name"]
            next_row["needs_supplement"] = False
        out.append(next_row)
    used = {str(r.get("element_id") or "").strip() for r in out if str(r.get("element_id") or "").strip()}
    orphans = [x for x in eids if x not in used]
    for eid in orphans:
        idx = next(
            (
                i
                for i, row in enumerate(out)
                if not str(row.get("element_id") or "").strip()
                and (
                    row.get("needs_supplement")
                    or str(row.get("slot") or "").strip()
                    or text_needs_supplement(str(row.get("text") or ""))
                )
            ),
            -1,
        )
        if idx < 0:
            break
        row = dict(out[idx])
        el = by_id.get(eid)
        row["element_id"] = eid
        name = human_label(_element_name(el), row.get("element_name"), row.get("slot"))
        if name:
            row["element_name"] = name
        elif "element_name" in row and is_element_id_token(row.get("element_name")):
            del row["element_name"]
        row["needs_supplement"] = False
        out[idx] = row
    return out


def chapter_point_items_from_payload(
    item: dict[str, Any],
    *,
    elements_by_id: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """PUT/GET 大纲：结构化 point_items 与 points 对齐，并按 element_ids 补关联。"""
    points = item.get("points") or []
    if isinstance(points, str):
        points = [line.strip() for line in points.splitlines() if line.strip()]
    else:
        points = [str(line).strip() for line in points if str(line).strip()]
    raw_items = item.get("point_items")
    if isinstance(raw_items, list) and raw_items:
        items: list[dict[str, Any]] = []
        for i, row in enumerate(raw_items):
            norm = normalize_llm_point_item(row, i)
            if norm:
                items.append(norm)
        if not items:
            items = sync_point_items_from_points(points, raw_items)
    else:
        items = sync_point_items_from_points(points, [])
    eids = [str(x).strip() for x in (item.get("element_ids") or []) if str(x).strip()]
    if eids:
        items = link_point_items_to_element_ids(items, eids, elements_by_id)
    return items


def elements_by_id_from_task(task: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for el in list(getattr(task, "element_nodes", None) or []):
        eid = str(getattr(el, "id", "") or "").strip()
        if eid:
            out[eid] = el
    return out


def sanitize_point_item_row(row: dict[str, Any], elements_by_id: dict[str, Any] | None) -> dict[str, Any]:
    """无有效 element_id 时去掉幽灵 element_name；已关联则清 needs_supplement。"""
    by_id = dict(elements_by_id or {})
    out = dict(row)
    eid = str(out.get("element_id") or "").strip()
    if eid and eid in by_id:
        out["element_id"] = eid
        out["needs_supplement"] = False
        name = human_label(
            _element_name(by_id.get(eid)),
            out.get("element_name"),
            out.get("slot"),
        )
        if name:
            out["element_name"] = name
        elif "element_name" in out:
            del out["element_name"]
    else:
        out.pop("element_id", None)
        out.pop("element_name", None)
    return out


def normalize_chapter_point_items_on_node(task: Any, node: Any) -> None:
    """保存/配置后：link + 去幽灵字段 + 同步 points。"""
    if bool(getattr(node, "structure_only", False)):
        return
    by_id = elements_by_id_from_task(task)
    eids = [str(x).strip() for x in (getattr(node, "element_ids", None) or []) if str(x).strip()]
    payload = {
        "points": list(getattr(node, "points", None) or []),
        "point_items": list(getattr(node, "point_items", None) or []),
        "element_ids": eids,
    }
    items = chapter_point_items_from_payload(payload, elements_by_id=by_id)
    cleaned = [sanitize_point_item_row(r, by_id) for r in items if isinstance(r, dict)]
    node.point_items = cleaned
    node.points = points_from_point_items(cleaned)
    _repair_id_like_element_names(task, cleaned)


def _repair_id_like_element_names(task: Any, items: list[dict[str, Any]]) -> None:
    """要点关联后，把要素节点上误存的 el-… 标识符还原成可读名称。"""
    by_id = elements_by_id_from_task(task)
    for row in items:
        eid = str(row.get("element_id") or "").strip()
        el = by_id.get(eid)
        if el is None:
            continue
        current = str(el.get("name") if isinstance(el, dict) else getattr(el, "name", "") or "").strip()
        if current and not is_element_id_token(current):
            continue
        name = human_label(row.get("element_name"), row.get("slot"), parse_slot_from_text(row.get("text")))
        if not name:
            continue
        if isinstance(el, dict):
            el["name"] = name
        else:
            el.name = name
