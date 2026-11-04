"""章节说明 / 补充提示词 ← 写作要点。"""

from report_writing.domain.chapter_meta_from_points import (
    apply_meta_from_points_to_node,
    derive_chapter_meta_from_points,
)
from report_writing.domain.models import OutlineNode


def test_derive_meta_fallback_without_model(monkeypatch):
    monkeypatch.setattr("report_writing.infra.llm.live_model", lambda **kw: None)

    describes, aux = derive_chapter_meta_from_points(
        title="岗位职责",
        category="述职报告",
        point_items=[
            {
                "id": "p0",
                "text": "说明岗位名称与汇报关系（待补充：任职文件）",
                "slot": "任职文件",
                "needs_supplement": True,
            },
            {"id": "p1", "text": "分条列出核心职责，动词开头"},
        ],
    )
    assert describes
    assert "岗位职责" in describes or "当前要点" in describes
    assert "【建议补充资料】" in aux
    assert "任职文件" in aux
    assert "核心职责" in aux


def test_apply_meta_from_points_to_node():
    node = OutlineNode(
        key="c1",
        title="任务清单",
        points=["用表格列任务", "标注完成时限"],
        point_items=[
            {"id": "p0", "text": "用表格列任务"},
            {"id": "p1", "text": "标注完成时限"},
        ],
    )
    from unittest.mock import patch

    with patch("report_writing.infra.llm.live_model", lambda **kw: None):
        ok = apply_meta_from_points_to_node(node, category="报告")
    assert ok
    assert node.describes
    assert node.aux_prompt
    assert "表格" in node.aux_prompt
