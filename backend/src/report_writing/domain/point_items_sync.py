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


def _raw_points_from_node(node: Any) -> list[str]:
    return [str(p or "").strip() for p in list(getattr(node, "points", None) or []) if str(p or "").strip()]


def points_for_writing_from_node(node: Any) -> list[str]:
    """写作/同步 brief 用的要点：已关联要素或无待补标记的行去掉占位符。"""
    items = list(getattr(node, "point_items", None) or [])
    lines: list[str] = []
    for row in items:
        if isinstance(row, dict):
            text = writing_text_for_point_row(row)
        else:
            text = str(row or "").strip()
        if text:
            lines.append(text)
    # point_items 有壳没正文时不能把副本已保存的 points 丢掉
    if not lines:
        raw = _raw_points_from_node(node)
        if any(text_needs_supplement(p) for p in raw):
            return raw
        return [strip_supplement_markers(p) for p in raw if strip_supplement_markers(p)]
    return lines


def chapter_has_writing_config(node: Any) -> bool:
    """副本已配写作要点、章节说明或参考正文，即视为已配置（勿报「要点为空」）。"""
    if points_for_writing_from_node(node):
        return True
    if _raw_points_from_node(node):
        return True
    if str(getattr(node, "describes", "") or "").strip():
        return True
    if str(getattr(node, "generate", "") or "").strip():
        return True
    if str(getattr(node, "reference_text", "") or "").strip():
        return True
    if str(getattr(node, "aux_prompt", "") or "").strip():
        return True
    return False


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
    """仅回传用户手填的补充提示词。写作要点走 points / point_write_plan，勿并进 aux。"""
    items = list(getattr(node, "point_items", None) or [])
    pending = (
        chapter_has_pending_supplements(items)
        if items
        else any(text_needs_supplement(p) for p in (getattr(node, "points", None) or []))
    )
    aux = str(getattr(node, "aux_prompt", "") or "").strip()
    if pending:
        return aux
    return sanitize_writing_meta_text(aux, allow_supplement_markers=False)


def describes_for_writing_from_node(node: Any) -> str:
    items = list(getattr(node, "point_items", None) or [])
    pending = chapter_has_pending_supplements(items) if items else False
    desc = sanitize_writing_meta_text(
        str(getattr(node, "describes", "") or ""),
        allow_supplement_markers=pending,
    )
    if not pending:
        desc = re.sub(r"需填报处[^。；;\n]*[。；;]?", "", desc)
    return desc


def build_point_write_plan(
    point_items: list[Any] | None,
    element_results: list[Any] | None,
    *,
    points: list[str] | None = None,
) -> list[dict[str, Any]]:
    """写作载荷：每条要点绑定其关联要素取值，供模型按「格式规则 + 要素内容」成表。"""
    by_id: dict[str, dict[str, Any]] = {}
    for row in list(element_results or []):
        if not isinstance(row, dict):
            continue
        eid = str(row.get("id") or "").strip()
        if eid:
            by_id[eid] = row
    items = [row for row in list(point_items or []) if isinstance(row, dict)]
    plan: list[dict[str, Any]] = []
    if items:
        for i, row in enumerate(items):
            text = writing_text_for_point_row(row)
            if not text:
                continue
            eid = str(row.get("element_id") or "").strip()
            el = by_id.get(eid) if eid else None
            value = str((el or {}).get("value") or "").strip()
            plan.append(
                {
                    "index": len(plan) + 1,
                    "point": text,
                    "slot": human_label(row.get("slot"), row.get("element_name")),
                    "element_id": eid,
                    "element_name": human_label(
                        (el or {}).get("name"), row.get("element_name"), row.get("slot")
                    ),
                    "element_value": value,
                    "has_element": bool(eid and value),
                }
            )
        return plan
    # 无 point_items 时按 points 顺序占位，要素仅作章级补充
    for i, line in enumerate(list(points or [])):
        text = str(line or "").strip()
        if not text:
            continue
        plan.append(
            {
                "index": len(plan) + 1,
                "point": text,
                "slot": "",
                "element_id": "",
                "element_name": "",
                "element_value": "",
                "has_element": False,
            }
        )
    return plan


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
    """保存要点文本时：按行更新 point_items，同索引保留 element_id / slot / id。

    若同索引旧行文本与新要点明显不同（重分析换内容），不继承旧 slot/要素，
    避免「评价单元」要点行仍挂着「水平接地体」待补标签。
    """
    prev = [x for x in list(prev_items or []) if isinstance(x, dict)]
    out: list[dict[str, Any]] = []
    lines = [str(p or "").strip() for p in list(points or []) if str(p or "").strip()]
    for i, line in enumerate(lines):
        prev_row = prev[i] if i < len(prev) else {}
        prev_text = str(prev_row.get("text") or prev_row.get("label") or "").strip()
        same_line = bool(prev_text) and (prev_text == line or prev_text in line or line in prev_text)
        pid = str(prev_row.get("id") or prev_row.get("key") or f"p{i}").strip() or f"p{i}"
        if same_line:
            slot = str(prev_row.get("slot") or prev_row.get("element_name") or "").strip()
        else:
            slot = ""
            pid = f"p{i}"
        if not slot:
            slot = parse_slot_from_text(line)
        # 仅按当前正文判定待补；用户改成普通要点后不得继承旧 needs_supplement
        needs = text_needs_supplement(line)
        row: dict[str, Any] = {"id": pid, "text": line}
        if slot:
            row["slot"] = slot
        eid = str(prev_row.get("element_id") or "").strip() if same_line else ""
        if eid:
            row["element_id"] = eid
        elif needs:
            row["needs_supplement"] = True
        ename = human_label(prev_row.get("element_name") if same_line else "", slot)
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
    # 显式 False + 正文无占位 → 普通要点（可不关联要素）
    if row.get("needs_supplement") is False and not text_needs_supplement(text):
        pass
    elif text_needs_supplement(text) or row.get("needs_supplement"):
        out["needs_supplement"] = True
    eid = str(row.get("element_id") or "").strip()
    if eid:
        out["element_id"] = eid
        out["needs_supplement"] = False
    ename = human_label(row.get("element_name"), out.get("slot"))
    if ename:
        out["element_name"] = ename
    return out


def _slot_covered_in_points(name: str, points: list[str]) -> bool:
    key = str(name or "").strip()
    if not key:
        return True
    for line in points:
        text = str(line or "")
        if key in text or parse_slot_from_text(text) == key:
            return True
    return False


def merge_project_slots_into_points(
    points: list[str] | None,
    project_slots: list[Any] | None,
    *,
    max_extra: int = 8,
    max_total: int = 14,
) -> list[str]:
    """样本章节分析：把 project_slots 落成带「（待补充：…）」的写法要点，便于文档窗与开写前检查。"""
    out = [str(p).strip() for p in list(points or []) if str(p).strip()]
    if not project_slots:
        return out[:max_total]
    added = 0
    for item in list(project_slots or []):
        if added >= max_extra:
            break
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name or _slot_covered_in_points(name, out):
            continue
        hint = str(item.get("hint") or "").strip()
        if hint:
            line = f"按本项目填写{name}（待补充：{name}）"
        else:
            line = f"写明{name}等关键信息（待补充：{name}）"
        out.append(line)
        added += 1
    return out[:max_total]


def aux_prompt_from_project_slots(
    points: list[str] | None,
    project_slots: list[Any] | None,
    *,
    existing: str = "",
) -> str:
    """与无蓝本起草要点一致：在 aux 前缀列出建议补充的资料类型。"""
    base = str(existing or "").strip()
    if base and "【建议补充资料】" in base:
        return base[:800]
    slot_lines: list[str] = []
    seen: set[str] = set()
    for item in list(project_slots or [])[:12]:
        if isinstance(item, dict):
            name = str(item.get("name") or "").strip()
            hint = str(item.get("hint") or "").strip()
            if not name or name in seen:
                continue
            seen.add(name)
            slot_lines.append(f"资料·{name}" + (f"：{hint}" if hint else ""))
        elif str(item).strip():
            t = str(item).strip()
            if t not in seen:
                seen.add(t)
                slot_lines.append(f"资料·{t}")
    if not slot_lines:
        return base[:800]
    prefix = "【建议补充资料】\n" + "\n".join(slot_lines)
    body = "\n".join(str(p or "").strip() for p in list(points or [])[:8] if str(p or "").strip())
    if base:
        return f"{prefix}\n\n{base}"[:800]
    if body:
        return f"{prefix}\n\n{body}"[:800]
    return prefix[:800]


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
    replace: bool = False,
) -> None:
    """同时写入 node.points 与 node.point_items（OutlineNode 或兼容属性）。

    replace=True：重分析整表替换，不按旧行索引继承 slot/要素（否则接地行会留在评价单元章）；
    同时清空章级 element_ids，避免上一轮关联残留到新要点。
    """
    prev = [] if replace else list(getattr(node, "point_items", None) or [])
    pts, items = merge_generated_point_items(points, raw_point_items, prev_items=prev)
    node.points = pts
    node.point_items = items
    if replace and hasattr(node, "element_ids"):
        node.element_ids = []


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
    link_chapter_elements_to_points: bool = False,
) -> list[dict[str, Any]]:
    """PUT/GET 大纲：结构化 point_items 与 points 对齐；默认不把章 element_ids 自动挂到要点。"""
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
        elif points:
            items_text = "\n".join(
                str((row or {}).get("text") or "").strip() for row in items if str((row or {}).get("text") or "").strip()
            )
            points_text = "\n".join(points)
            if points_text and items_text and points_text != items_text:
                # 以 points 正文为准，但按旧行继承 element_id/slot（去掉待补标记后文本仍算同一行）
                items = sync_point_items_from_points(points, items)
    else:
        items = sync_point_items_from_points(points, [])
    eids = [str(x).strip() for x in (item.get("element_ids") or []) if str(x).strip()]
    if eids and link_chapter_elements_to_points:
        items = link_point_items_to_element_ids(items, eids, elements_by_id)
    return items


def elements_by_id_from_task(task: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for el in list(getattr(task, "element_nodes", None) or []):
        eid = str(getattr(el, "id", "") or "").strip()
        if eid:
            out[eid] = el
    return out


def _match_element_id_by_label(
    label: str,
    elements_by_id: dict[str, Any] | None,
    *,
    used: set[str] | None = None,
) -> str:
    """按要素显示名精确匹配 id；用于要点只剩 element_name、id 被洗掉时回补。

    同名多个时不猜（避免挂错「法律名称清单」副本），留给显式 element_id。
    """
    key = human_label(label)
    if not key:
        return ""
    by_id = dict(elements_by_id or {})
    blocked = set(used or set())
    hits = [
        str(eid or "").strip()
        for eid, el in by_id.items()
        if str(eid or "").strip()
        and str(eid or "").strip() not in blocked
        and _element_name(el) == key
    ]
    if len(hits) == 1:
        return hits[0]
    return ""


def _element_retrieval_labels(el: Any) -> list[str]:
    """要素可用于与要点/章题对齐的名称：要素名、槽位名、标签。"""
    labels: list[str] = []
    seen: set[str] = set()

    def _push(raw: Any) -> None:
        text = human_label(raw)
        if not text or text in seen or is_element_id_token(text):
            return
        seen.add(text)
        labels.append(text)

    _push(_element_name(el))
    slots = []
    if isinstance(el, dict):
        slots = list(el.get("file_slots") or [])
    else:
        slots = list(getattr(el, "file_slots", None) or [])
    for slot in slots:
        # 只用槽位名对齐要点；检索词 query 不参与匹配，避免短词误挂
        if isinstance(slot, dict):
            _push(slot.get("name"))
        else:
            _push(getattr(slot, "name", None))
    tags = el.get("tags") if isinstance(el, dict) else getattr(el, "tags", None)
    for tag in list(tags or []):
        # 过短标签（如「法律」）易误伤整章，收进 labels 后由 _label_hits_text 过滤
        _push(tag)
    return labels


def _label_hits_text(label: str, text: str) -> bool:
    a = human_label(label)
    b = human_label(text)
    if not a or not b:
        return False
    if a == b:
        return True
    # 禁止用「法律」这类过短标签误挂整章；至少 4 字才做包含匹配
    if len(a) < 4 and len(b) < 4:
        return False
    if len(a) < 4:
        return False
    return a in b or (len(b) >= 4 and b in a)


def _element_kind(el: Any) -> str:
    if isinstance(el, dict):
        return str(el.get("kind") or "").strip()
    return str(getattr(el, "kind", "") or "").strip()


def _best_element_hit(hits: list[str], by_id: dict[str, Any], row_text: str) -> str:
    """多候选时优先 file_slot，再比最长标签命中。"""
    if not hits:
        return ""
    if len(hits) == 1:
        return hits[0]

    def score(eid: str) -> tuple[int, int]:
        el = by_id.get(eid)
        kind_bonus = 2 if _element_kind(el) == "file_slot" else (1 if _element_kind(el) in {"file", "rag_search"} else 0)
        best_len = 0
        for label in _element_retrieval_labels(el):
            if _label_hits_text(label, row_text):
                best_len = max(best_len, len(human_label(label)))
        return (kind_bonus, best_len)

    return sorted(hits, key=score, reverse=True)[0]


def infer_element_ids_for_chapter_node(
    node: Any,
    *,
    elements_by_id: dict[str, Any] | None = None,
) -> list[str]:
    """无显式关联时，按要点/章题与要素名、资料槽位名推断检索要素。

    重分析会清空关联；写作时仍须尽量拿到 file_slot 内容，避免「有要素却检索为空」。
    同名多个时不猜。
    """
    by_id = dict(elements_by_id or {})
    if not by_id:
        return []
    haystacks: list[str] = []
    title = human_label(getattr(node, "title", None))
    if title:
        haystacks.append(title)
    for row in list(getattr(node, "point_items", None) or []):
        if isinstance(row, dict):
            for key in ("text", "slot", "element_name"):
                t = human_label(row.get(key))
                if t:
                    haystacks.append(t)
        else:
            t = human_label(row)
            if t:
                haystacks.append(t)
    for line in list(getattr(node, "points", None) or []):
        t = human_label(line)
        if t:
            haystacks.append(t)
    if not haystacks:
        return []

    # label -> [eid, ...]
    by_label: dict[str, list[str]] = {}
    for eid, el in by_id.items():
        cid = str(eid or "").strip()
        if not cid:
            continue
        for label in _element_retrieval_labels(el):
            by_label.setdefault(label, []).append(cid)

    ordered: list[str] = []
    seen: set[str] = set()
    for text in haystacks:
        for label, eids in by_label.items():
            if not _label_hits_text(label, text):
                continue
            uniq = []
            for eid in eids:
                if eid not in uniq:
                    uniq.append(eid)
            eid = _best_element_hit(uniq, by_id, text)
            if not eid or eid in seen:
                continue
            seen.add(eid)
            ordered.append(eid)
    return ordered


def element_ids_for_chapter_node(
    node: Any,
    *,
    elements_by_id: dict[str, Any] | None = None,
    infer_unlinked: bool = False,
) -> list[str]:
    """章 element_ids 与 point_items 上已关联的 element_id 并集（检索/待补与 UI 一致）。

    要点若只剩 element_name（id 在某次 PUT/同步中丢失），仍按名称回补进检索并集，
    避免「界面上看着关联了、写作 element_results 却为空」。
    infer_unlinked=True：显式关联为空时，按要点文案/槽位名推断 file_slot 等要素。
    """
    by_id = dict(elements_by_id or {})
    ordered: list[str] = []
    seen: set[str] = set()

    def _add(eid: str) -> None:
        if not eid or eid in seen:
            return
        if by_id and eid not in by_id:
            return
        seen.add(eid)
        ordered.append(eid)

    for raw in list(getattr(node, "element_ids", None) or []):
        _add(str(raw or "").strip())
    for row in list(getattr(node, "point_items", None) or []):
        if not isinstance(row, dict):
            continue
        eid = str(row.get("element_id") or "").strip()
        if eid:
            _add(eid)
            continue
        healed = _match_element_id_by_label(
            human_label(row.get("element_name"), row.get("slot")),
            by_id,
            used=seen,
        )
        if healed:
            _add(healed)
            continue
        # 要点正文对上要素名/槽位名（如「法律法规资料」↔ 槽位「法律法规」）
        if by_id:
            row_text = human_label(row.get("text"), row.get("slot"), row.get("element_name"))
            hits = [
                cid
                for cid, el in by_id.items()
                if cid not in seen
                and any(_label_hits_text(label, row_text) for label in _element_retrieval_labels(el))
            ]
            best = _best_element_hit(hits, by_id, row_text)
            if best:
                _add(best)
    if infer_unlinked and not ordered:
        for eid in infer_element_ids_for_chapter_node(node, elements_by_id=by_id):
            _add(eid)
    return ordered


def relink_chapter_elements_after_study(task: Any, node: Any) -> None:
    """重分析清空关联后：按新要点文案/槽位名把任务内已有要素挂回要点，并补章级检索 id。"""
    if node is None or bool(getattr(node, "structure_only", False)):
        return
    by_id = elements_by_id_from_task(task)
    if not by_id:
        return
    items = [dict(r) for r in list(getattr(node, "point_items", None) or []) if isinstance(r, dict)]
    if not items:
        return
    used: set[str] = set()
    changed = False
    for i, row in enumerate(items):
        if str(row.get("element_id") or "").strip():
            used.add(str(row.get("element_id") or "").strip())
            continue
        row_text = human_label(row.get("text"), row.get("slot"), row.get("element_name"))
        hits = [
            cid
            for cid, el in by_id.items()
            if cid not in used
            and any(_label_hits_text(label, row_text) for label in _element_retrieval_labels(el))
        ]
        eid = _best_element_hit(hits, by_id, row_text)
        if not eid:
            continue
        el = by_id.get(eid)
        nxt = dict(row)
        nxt["element_id"] = eid
        nxt["element_name"] = human_label(_element_name(el), row.get("element_name"), row.get("slot"))
        nxt["needs_supplement"] = False
        items[i] = nxt
        used.add(eid)
        changed = True
    if changed:
        node.point_items = items
        node.points = points_from_point_items(items)
    # 章级：确保至少一个 file_slot 在 element_ids 里，写作检索必达
    inferred = infer_element_ids_for_chapter_node(node, elements_by_id=by_id)
    point_ids = element_ids_for_chapter_node(node, elements_by_id=by_id)
    cur = [str(x).strip() for x in (getattr(node, "element_ids", None) or []) if str(x).strip()]
    for eid in [*point_ids, *inferred]:
        if eid not in cur:
            cur.append(eid)
    # 去重但保序；同名 file_slot 只留评分最高的一个，避免双份摘录
    deduped: list[str] = []
    seen_name: set[str] = set()
    for eid in cur:
        el = by_id.get(eid)
        name = _element_name(el)
        kind = _element_kind(el)
        if kind == "file_slot" and name:
            if name in seen_name:
                continue
            seen_name.add(name)
        if eid not in deduped:
            deduped.append(eid)
    node.element_ids = deduped


def sanitize_point_item_row(row: dict[str, Any], elements_by_id: dict[str, Any] | None) -> dict[str, Any]:
    """无 element_id 时尽量按 element_name 回补；仍无则去掉幽灵 element_name。

    要素体尚未写入 task.element_nodes（新建后立刻 PUT outline）时，仍保留要点上的
    element_id / element_name，避免关联刚落盘就被洗掉导致前端「未回显 / 要点像消失」。
    """
    by_id = dict(elements_by_id or {})
    out = dict(row)
    eid = str(out.get("element_id") or "").strip()
    if not eid and by_id:
        eid = _match_element_id_by_label(
            human_label(out.get("element_name"), out.get("slot")),
            by_id,
        )
    if eid:
        out["element_id"] = eid
        out["needs_supplement"] = False
        name = human_label(
            _element_name(by_id.get(eid)) if eid in by_id else "",
            out.get("element_name"),
            out.get("slot"),
        )
        if name:
            out["element_name"] = name
        elif "element_name" in out and is_element_id_token(out.get("element_name")):
            del out["element_name"]
    else:
        out.pop("element_id", None)
        # 要素库尚未就绪时保留名称，便于下次 normalize 回补 id；有库仍对不上才清掉幽灵名
        if by_id:
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
    # 章 element_ids 与要点 element_id 分开存；检索/待补并集见 element_ids_for_chapter_node
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
