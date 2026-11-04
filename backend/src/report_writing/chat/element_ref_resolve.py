"""对话里用要素名称解析 ref_key（引用结果）。"""

from __future__ import annotations

from typing import Any

from report_writing.domain.models import ElementNode


def _element_row(el: ElementNode | dict[str, Any]) -> tuple[str, str]:
    if isinstance(el, ElementNode):
        return str(el.id or "").strip(), str(el.name or "").strip()
    return str(el.get("id") or "").strip(), str(el.get("name") or "").strip()


def _is_element_id(value: str) -> bool:
    s = (value or "").strip()
    return s.startswith("el-") and len(s) > 3


def resolve_ref_element_name(name: str, task_elements: list[ElementNode] | list[dict] | None) -> dict[str, Any]:
    q = (name or "").strip()
    if not q:
        return {"ref_key": "", "ref_picks": [], "unmatched": False}
    rows = []
    for el in task_elements or []:
        eid, ename = _element_row(el)
        if eid:
            rows.append({"id": eid, "name": ename, "label": f"{ename or eid}"})
    if _is_element_id(q):
        if any(r["id"] == q for r in rows):
            return {"ref_key": q, "ref_picks": [], "unmatched": False}
    exact = [r for r in rows if r["name"] == q]
    if len(exact) == 1:
        return {"ref_key": exact[0]["id"], "ref_picks": [], "unmatched": False}
    if len(exact) > 1:
        return {
            "ref_key": "",
            "ref_picks": [{"query": q, "candidates": exact, "selected_id": ""}],
            "unmatched": False,
        }
    q_lower = q.lower()
    partial = [r for r in rows if q_lower in (r["name"] or "").lower()]
    if len(partial) == 1:
        return {"ref_key": partial[0]["id"], "ref_picks": [], "unmatched": False}
    if len(partial) > 1:
        return {
            "ref_key": "",
            "ref_picks": [{"query": q, "candidates": partial, "selected_id": ""}],
            "unmatched": False,
        }
    return {"ref_key": "", "ref_picks": [], "unmatched": True}
