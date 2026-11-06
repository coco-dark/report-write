import json

from report_writing.chat.chapter_resolve import resolve_chapter_config_patch
from report_writing.chat.chapter_resolve_config_llm import llm_extract_chapter_config_patch
from report_writing.domain.models import OutlineNode, OutlineTree, WritingTask


def test_llm_extract_maps_sequence():
    task = WritingTask(
        id="w",
        outline=OutlineTree(chapters=[OutlineNode(key="k2", sequence="2.1", title="措施")]),
    )

    def invoke(_system: str, user: str) -> str:
        assert "2.1" in user or "措施" in user
        return json.dumps(
            {"chapter_sequence": "2.1", "points_text": "列三条整改措施"},
            ensure_ascii=False,
        )

    patch = llm_extract_chapter_config_patch("把措施写具体点", task, invoke)
    assert patch.get("chapter_key") == "k2"
    assert "整改" in patch.get("points_text", "")


def test_resolve_uses_llm_when_rules_miss():
    task = WritingTask(
        id="w",
        outline=OutlineTree(chapters=[OutlineNode(key="k1", sequence="1", title="概述")]),
    )

    def invoke(_s: str, _u: str) -> str:
        return json.dumps({"points_text": "补充项目背景"}, ensure_ascii=False)

    patch = resolve_chapter_config_patch("章节配置：多写项目背景", task, invoke=invoke)
    assert "背景" in patch.get("points_text", "")
