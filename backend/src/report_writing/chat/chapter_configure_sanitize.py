"""Agent 口语 configure_chapter：不得用模型臆造的要点/要素盖库内已落盘关联。

口语改要点：仅允许 points / points_text（按行 sync 并保留同索引要素关联），
不允许 agent 直接提交 point_items / element_ids。
"""

from __future__ import annotations

_AGENT_STRUCTURED_FIELDS = frozenset(
    {
        "point_items",
        "keywords",
        "keywords_text",
        "element_ids",
        "elements",
    }
)
_AGENT_POINTS_FIELDS = frozenset({"points", "points_text"})
_LEGACY_AGENT_CONTENT_FIELDS = _AGENT_STRUCTURED_FIELDS | _AGENT_POINTS_FIELDS


def prepare_configure_chapter_body(body: dict | None) -> dict:
    """文档窗/UI 全量保存原样；Agent 结构化要点/要素剔除，口语要点行保留。"""
    out = dict(body or {})
    if out.get("already_persisted") or out.get("from_doc_window"):
        out.pop("_via_agent_tool", None)
        return out
    agent = bool(out.pop("_via_agent_tool", False))
    allow_inline = bool(out.pop("allow_inline_chapter_config", False))
    has_points = any(k in out for k in _AGENT_POINTS_FIELDS)
    has_structured = any(k in out for k in _AGENT_STRUCTURED_FIELDS)
    if agent:
        for key in _AGENT_STRUCTURED_FIELDS:
            out.pop(key, None)
        if allow_inline and has_structured:
            # 仅非 agent 路径应开 allow_inline；agent 忽略以免模型自证内联
            pass
        return out
    has_content = has_points or has_structured
    if has_content and not allow_inline:
        out = {k: v for k, v in out.items() if k not in _LEGACY_AGENT_CONTENT_FIELDS}
    return out
