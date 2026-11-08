"""章节说明 ← 写作要点；补充提示词不自动生成。"""

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
    assert describes.startswith("1.")
    assert "岗位职责" in describes or "写法" in describes or "写作风格" in describes
    assert aux == ""


def test_derive_meta_follows_source_only_strategy(monkeypatch):
    monkeypatch.setattr("report_writing.infra.llm.live_model", lambda **kw: None)
    describes, aux = derive_chapter_meta_from_points(
        title="事故专篇",
        point_items=[{"id": "p0", "text": "仅依据项目资料撰写事故经过"}],
        compose="source_only",
    )
    assert "写法" in describes or "排版" in describes or "事故专篇" in describes
    assert aux == ""


def test_derive_meta_follows_template_fill_strategy(monkeypatch):
    monkeypatch.setattr("report_writing.infra.llm.live_model", lambda **kw: None)
    describes, aux = derive_chapter_meta_from_points(
        title="项目概况",
        point_items=[{"id": "p0", "text": "填入{{工程名称}}"}],
        compose="template_fill",
    )
    assert "写法" in describes or "排版" in describes or "项目概况" in describes
    assert aux == ""


def test_derive_meta_follows_auto_generate_strategy(monkeypatch):
    monkeypatch.setattr("report_writing.infra.llm.live_model", lambda **kw: None)
    describes, aux = derive_chapter_meta_from_points(
        title="编制说明",
        point_items=[{"id": "p0", "text": "说明编制目的与适用范围"}],
        compose="auto_generate",
    )
    assert "写法" in describes or "排版" in describes or "编制说明" in describes
    assert aux == ""


def test_apply_meta_from_points_to_node_keeps_aux():
    node = OutlineNode(
        key="c1",
        title="任务清单",
        aux_prompt="用户自填",
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
    assert node.aux_prompt == "用户自填"
