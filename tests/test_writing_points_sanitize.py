"""写作前要点/说明净化：无待补项时不带占位符。"""

from report_writing.domain.models import OutlineNode
from report_writing.domain.point_items_sync import (
    aux_prompt_for_writing_from_node,
    chapter_has_pending_supplements,
    chapter_has_writing_config,
    points_for_writing_from_node,
    strip_supplement_markers,
)


def test_linked_element_row_not_pending():
    items = [
        {"id": "p0", "text": "说明管辖幅度（待补充：管辖幅度数据）", "slot": "管辖幅度数据", "element_id": "e1"},
        {"id": "p1", "text": "分条列出核心职责", "needs_supplement": False},
    ]
    assert not chapter_has_pending_supplements(items)
    pts = points_for_writing_from_node(OutlineNode(title="职责", point_items=items, points=[]))
    assert all("待补充" not in p for p in pts)


def test_aux_drops_supplement_block_when_ready():
    node = OutlineNode(
        title="职责",
        aux_prompt=(
            "【建议补充资料】\n资料·任职文件\n\n"
            "以一句话明确岗位（待补充：任职文件）。\n分条列出职责。"
        ),
        point_items=[
            {"id": "p0", "text": "以一句话明确岗位名称与汇报关系"},
            {"id": "p1", "text": "分条列出核心职责"},
        ],
        points=["以一句话明确岗位名称与汇报关系", "分条列出核心职责"],
    )
    aux = aux_prompt_for_writing_from_node(node)
    assert "【建议补充资料】" not in aux
    assert "待补充" not in aux
    assert "分条列出核心职责" in aux


def test_points_for_writing_falls_back_when_items_have_no_text():
    node = OutlineNode(
        title="评价结果",
        point_items=[{"id": "p0"}, {"id": "p1", "text": "  "}],
        points=["对评价中发现的问题给出结论"],
        describes="1. 注意与表述方式。",
    )
    assert points_for_writing_from_node(node) == ["对评价中发现的问题给出结论"]
    assert chapter_has_writing_config(node)
    empty = OutlineNode(title="空章", points=[], point_items=[], describes="")
    assert not chapter_has_writing_config(empty)


def test_strip_supplement_markers():
    t = strip_supplement_markers("界定区间（待补充：任职文件或岗位说明书）。")
    assert "待补充" not in t
    assert "界定区间" in t
