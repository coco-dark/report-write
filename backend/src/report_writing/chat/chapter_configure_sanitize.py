"""Agent 口语 configure_chapter：不得用模型臆造的要点/要素盖库内已落盘关联。"""

from __future__ import annotations

_AGENT_CONTENT_FIELDS = frozenset(
    {
        "points",
        "points_text",
        "point_items",
        "keywords",
        "keywords_text",
        "element_ids",
        "elements",
    }
)


def prepare_configure_chapter_body(body: dict | None) -> dict:
    """文档窗/UI 全量保存原样；Agent 或未落盘内联要点/要素一律剔除。"""
    out = dict(body or {})
    if out.get("already_persisted") or out.get("from_doc_window"):
        out.pop("_via_agent_tool", None)
        return out
    agent = bool(out.pop("_via_agent_tool", False))
    has_content = any(k in out for k in _AGENT_CONTENT_FIELDS)
    allow_inline = bool(out.get("allow_inline_chapter_config"))
    if agent or (has_content and not allow_inline):
        out = {k: v for k, v in out.items() if k not in _AGENT_CONTENT_FIELDS}
    return out
