"""根据写作要点 point_items 生成章节说明与补充提示词。"""

from __future__ import annotations

import json
import re
from typing import Any

from report_writing.domain.point_items_sync import (
    chapter_has_pending_supplements,
    parse_slot_from_text,
    pending_supplement_slots,
    point_item_row_needs_supplement,
    points_for_writing_from_node,
    points_from_point_items,
    strip_supplement_markers,
    writing_text_for_point_row,
)

def _fallback_describes(
    title: str,
    category: str,
    parent_title: str,
    points: list[str],
    *,
    compose: str = "",
    presence: str = "",
) -> str:
    from report_writing.domain.compose_analysis import fallback_describes
    from report_writing.domain.chapter_compose import COMPOSE_HYBRID, normalize_compose

    mode = normalize_compose(compose, presence=presence)
    if mode != COMPOSE_HYBRID:
        return fallback_describes(title, points, compose=mode, presence=presence)
    from report_writing.chat.freeform_points import _fallback_describes as _ff

    base = _ff(title, category, parent_title=parent_title)
    if not points:
        return base
    hints = "；".join(strip_supplement_markers(p)[:48] for p in points[:4])
    return f"{base}当前要点侧重：{hints}。"[:500]


def _aux_from_points(items: list[Any]) -> str:
    pending = chapter_has_pending_supplements(items)
    lines = [
        writing_text_for_point_row(r)
        for r in items
        if isinstance(r, dict)
    ]
    body = "\n".join(t for t in lines if t).strip()
    if not pending:
        return body[:800]
    slot_lines = [f"资料·{s}" for s in pending_supplement_slots(items)]
    if slot_lines:
        prefix = "【建议补充资料】\n" + "\n".join(slot_lines)
        if body:
            return f"{prefix}\n\n{body}"[:800]
        return prefix[:800]
    return body[:800]


def _parse_meta_json(raw: str) -> dict[str, str]:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-z]*\n?", "", text, flags=re.I)
        text = re.sub(r"\n?```\s*$", "", text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{[\s\S]*\}", text)
        if not m:
            return {}
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            return {}
    if not isinstance(data, dict):
        return {}
    return {
        "describes": str(data.get("describes") or "").strip(),
        "aux_prompt": str(data.get("aux_prompt") or "").strip(),
    }


def derive_chapter_meta_from_points(
    *,
    title: str = "",
    sequence: str = "",
    category: str = "文档",
    parent_title: str = "",
    point_items: list[Any] | None,
    model=None,
    compose: str = "",
    presence: str = "",
    reference_text: str = "",
) -> tuple[str, str]:
    """返回 (describes, aux_prompt)。aux 恒为空：补充提示词留给用户。"""
    from report_writing.domain.compose_analysis import meta_system_prompt

    items = [x for x in list(point_items or []) if isinstance(x, dict)]
    points = points_from_point_items(items)
    reference = str(reference_text or "").strip()
    if not points and not reference and not str(title or "").strip():
        return "", ""

    writing_pts = [
        writing_text_for_point_row(r)
        for r in items
        if isinstance(r, dict)
    ]
    writing_pts = [t for t in writing_pts if t]

    describes_fb = _fallback_describes(
        title, category, parent_title, writing_pts or points, compose=compose, presence=presence
    )

    try:
        from report_writing.infra.llm import or_live_model

        model = or_live_model(model)
    except Exception:
        model = None

    if model is None:
        from report_writing.domain.compose_analysis import format_describes_as_points

        return format_describes_as_points(describes_fb), ""

    user = json.dumps(
        {
            "doc_category": category,
            "chapter_sequence": sequence,
            "chapter_title": title,
            "parent_title": parent_title,
            "compose": compose or "hybrid",
            "presence": presence or "always",
            "reference_excerpt": reference[:2500],
            "point_items": [
                {
                    "text": writing_text_for_point_row(r)
                    or str(r.get("text") or "").strip(),
                    "slot": str(r.get("slot") or "").strip(),
                    "needs_supplement": point_item_row_needs_supplement(r),
                }
                for r in items
                if str(r.get("text") or "").strip()
            ],
        },
        ensure_ascii=False,
    )
    system = meta_system_prompt(compose=compose, presence=presence, category=category)
    try:
        if hasattr(model, "bind"):
            cold = model.bind(temperature=0.0)
            msg = cold.invoke(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ]
            )
        else:
            msg = model.invoke(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ]
            )
        content = getattr(msg, "content", None) or str(msg)
        parsed = _parse_meta_json(str(content))
        from report_writing.domain.compose_analysis import format_describes_as_points

        describes = format_describes_as_points(parsed.get("describes") or describes_fb)
        return describes, ""
    except Exception:
        from report_writing.domain.compose_analysis import format_describes_as_points

        return format_describes_as_points(describes_fb), ""


def apply_meta_from_points_to_node(
    node: Any,
    *,
    category: str = "文档",
    parent_title: str = "",
    model=None,
) -> bool:
    """若 node 有可写要点，则重写章节说明。补充提示词留给用户，不覆盖。"""
    if bool(getattr(node, "structure_only", False)):
        return False
    items = list(getattr(node, "point_items", None) or [])
    points = list(getattr(node, "points", None) or [])
    if not points and items:
        points = points_from_point_items(items)
    if not points:
        return False
    describes, aux = derive_chapter_meta_from_points(
        title=str(getattr(node, "title", "") or ""),
        sequence=str(getattr(node, "sequence", "") or ""),
        category=category,
        parent_title=parent_title,
        point_items=items if items else [{"text": p} for p in points],
        model=model,
        compose=str(getattr(node, "compose", "") or ""),
        presence=str(getattr(node, "presence", "") or ""),
    )
    node.describes = describes
    return True
