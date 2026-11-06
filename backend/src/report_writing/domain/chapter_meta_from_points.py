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

_META_SYSTEM = (
    "你是专业报告写作教练。用户已编辑本章「写作要点」列表，请据此重写章节说明与补充提示词。\n"
    "只返回 JSON，不要 Markdown：\n"
    '{"describes":"80～280字本章说明","aux_prompt":"补充提示词"}\n'
    "规则：\n"
    "- describes：本章功能、信息结构、写法边界；禁止写具体项目实例（工程名、地址、数值等）。\n"
    "- aux_prompt：写作硬约束（体裁、篇幅、表格/条目偏好）；须与当前要点一致，可分行概括各条写法。\n"
    "- 若输入中所有 point_items 的 needs_supplement 均为 false 且无待补 slot，则 aux_prompt 与 describes 中禁止出现「待补充」「【建议补充资料】」。\n"
    "- 若仍有 needs_supplement 为 true 的要点，可在 aux 开头用「【建议补充资料】」列出对应资料类型。\n"
    "- aux_prompt 总长不超过 800 字；describes 不超过 500 字。"
)


def _fallback_describes(title: str, category: str, parent_title: str, points: list[str]) -> str:
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
    category: str = "报告",
    parent_title: str = "",
    point_items: list[Any] | None,
    model=None,
) -> tuple[str, str]:
    """返回 (describes, aux_prompt)。无要点时返回空字符串对。"""
    items = [x for x in list(point_items or []) if isinstance(x, dict)]
    points = points_from_point_items(items)
    if not points:
        return "", ""

    writing_pts = [
        writing_text_for_point_row(r)
        for r in items
        if isinstance(r, dict)
    ]
    writing_pts = [t for t in writing_pts if t]

    describes_fb = _fallback_describes(title, category, parent_title, writing_pts or points)
    aux_fb = _aux_from_points(items)

    try:
        from report_writing.infra.llm import or_live_model

        model = or_live_model(model)
    except Exception:
        model = None

    if model is None:
        return describes_fb, aux_fb

    user = json.dumps(
        {
            "doc_category": category,
            "chapter_sequence": sequence,
            "chapter_title": title,
            "parent_title": parent_title,
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
    try:
        if hasattr(model, "bind"):
            cold = model.bind(temperature=0.0)
            msg = cold.invoke(
                [
                    {"role": "system", "content": _META_SYSTEM},
                    {"role": "user", "content": user},
                ]
            )
        else:
            msg = model.invoke(
                [
                    {"role": "system", "content": _META_SYSTEM},
                    {"role": "user", "content": user},
                ]
            )
        content = getattr(msg, "content", None) or str(msg)
        parsed = _parse_meta_json(str(content))
        describes = parsed.get("describes") or describes_fb
        aux = parsed.get("aux_prompt") or aux_fb
        return describes[:500], aux[:800]
    except Exception:
        return describes_fb, aux_fb


def apply_meta_from_points_to_node(
    node: Any,
    *,
    category: str = "报告",
    parent_title: str = "",
    model=None,
) -> bool:
    """若 node 有可写要点，则重写 describes / aux_prompt。返回是否已更新。"""
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
    )
    node.describes = describes
    node.aux_prompt = aux
    return True
