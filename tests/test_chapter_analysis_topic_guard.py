"""章名与粘贴主题冲突时：不得把接地分析残留再送回模型 / 写回简述。"""

from report_writing.controller.points import (
    _looks_like_prior_analysis_artifact,
    _text_has_topic_conflict,
    filter_analysis_against_title,
    prepare_chapter_excerpt_for_analysis,
)

GROUNDING_ARTIFACT = """本章为评价单元划分的接地部分，先引述评价依据条款，再逐项交代接地装置各组成部分的材质与规格（水平接地体、垂直接地极、引下线、室内接地引线、室内接地干线），最后给出接地电阻计算值与最大接触电势值等核算结论。固定层为栏目顺序与单位写法，具体规格、数值按项目资料填报。
【写作特点】说明性条文式，先依据后配置；逐项罗列、句式平行；以数据与规格为主，简洁不带评述
【须按本项目资料填写的元数据·勿照搬样板实例】
- 评价依据条款（文号）：引用的评价导则/规范条款编号及要求
- 水平接地体规格：材质与截面，如扁钢截面尺寸
- 垂直接地极规格：型式、截面尺寸与长度
- 引下线规格：材质与截面尺寸
- 室内接地导体规格：室内接地引线与接地干线的材质及截面
- 接地电阻计算值（数值）：接地网接地电阻计算值，单位Ω
- 接触电势计算值（数值）：最大接触电势值及单位
"""


def test_artifact_detector():
    assert _looks_like_prior_analysis_artifact(GROUNDING_ARTIFACT)
    assert _text_has_topic_conflict(GROUNDING_ARTIFACT, "评价单元划分")
    assert not _text_has_topic_conflict(GROUNDING_ARTIFACT, "接地装置")


def test_prepare_does_not_restore_conflicting_raw():
    cleaned = prepare_chapter_excerpt_for_analysis(
        GROUNDING_ARTIFACT, title="评价单元划分"
    )
    assert cleaned == ""
    assert "水平接地体" not in cleaned
    assert "接地电阻" not in cleaned


def test_filter_drops_grounding_describes_for_unit_chapter():
    pts, kws, desc, slots, styles = filter_analysis_against_title(
        title="评价单元划分",
        points=["交代水平接地体规格", "说明评价单元如何划分"],
        keywords=["接地电阻", "评价单元"],
        describes=GROUNDING_ARTIFACT,
        project_slots=[
            {"name": "水平接地体规格", "hint": "扁钢"},
            {"name": "评价单元名称", "hint": "按项目"},
        ],
        writing_style=["说明性条文式，先依据后配置；交代接地电阻"],
    )
    assert desc == ""
    assert all("接地" not in p for p in pts)
    assert all("接地" not in k for k in kws)
    assert all("接地" not in str(s.get("name") or "") for s in slots)
    assert pts  # 保留与章名相符的要点
    assert "评价单元" in pts[0]


def test_apply_points_replace_drops_old_grounding_rows():
    from report_writing.domain.models import OutlineNode
    from report_writing.domain.point_items_sync import (
        apply_points_to_outline_node,
        chapter_point_items_from_payload,
    )

    node = OutlineNode(
        title="评价单元划分",
        points=["交代水平接地体规格"],
        point_items=[
            {"id": "p0", "text": "交代水平接地体规格", "slot": "水平接地体规格"},
        ],
    )
    apply_points_to_outline_node(
        node,
        ["说明评价单元如何划分", "交代划分依据"],
        replace=True,
    )
    assert node.points == ["说明评价单元如何划分", "交代划分依据"]
    assert all("接地" not in str(row.get("text") or "") for row in node.point_items)
    payload_items = chapter_point_items_from_payload(
        {
            "points": node.points,
            "point_items": [
                {"id": "p0", "text": "交代水平接地体规格", "slot": "水平接地体规格"},
            ],
        }
    )
    assert [row["text"] for row in payload_items] == node.points


def test_apply_points_replace_clears_element_bindings():
    from report_writing.domain.models import OutlineNode
    from report_writing.domain.point_items_sync import apply_points_to_outline_node

    node = OutlineNode(
        title="法律条目",
        points=["按序号名称文号日期排列"],
        point_items=[
            {
                "id": "p0",
                "text": "按序号名称文号日期排列",
                "element_id": "el-law-order",
                "element_name": "法律条目排列逻辑",
            }
        ],
        element_ids=["el-law-order", "el-chapter"],
    )
    apply_points_to_outline_node(
        node,
        ["按序号名称文号日期排列", "补充层级顺序说明"],
        replace=True,
    )
    assert node.element_ids == []
    assert all(not str(row.get("element_id") or "").strip() for row in node.point_items)
