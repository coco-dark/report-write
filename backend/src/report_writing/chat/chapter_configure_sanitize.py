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
    """文档窗/UI 全量保存原样；经 Agent 工具入口时剔除要点与要素字段。"""
    out = dict(body or {})
    if out.get("already_persisted") or out.get("from_doc_window"):
        return out
    if not out.get("_via_agent_tool"):
        return out
    out = {k: v for k, v in out.items() if k not in _AGENT_CONTENT_FIELDS}
    out.pop("_via_agent_tool", None)
    return out
