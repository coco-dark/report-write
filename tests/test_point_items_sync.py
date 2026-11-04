from report_writing.domain.point_items_sync import (
    points_from_point_items,
    sync_point_items_from_points,
    sync_points_from_point_items,
)


def test_sync_preserves_element_id_by_index():
    prev = [
        {"id": "p0", "text": "旧", "element_id": "el-1", "slot": "项目名"},
    ]
    items = sync_point_items_from_points(["新文本（待补充：项目名）"], prev)
    assert len(items) == 1
    assert items[0]["element_id"] == "el-1"
    assert items[0]["text"].startswith("新文本")
    assert items[0].get("needs_supplement") is not True


def test_merge_from_llm_point_items():
    from report_writing.domain.point_items_sync import merge_generated_point_items

    pts, items = merge_generated_point_items(
        None,
        [
            {"id": "p0", "text": "说明投资（待补充：投资额）", "needs_supplement": True, "slot": "投资额"},
            {"text": "站址条件"},
        ],
    )
    assert pts == ["说明投资（待补充：投资额）", "站址条件"]
    assert len(items) == 2
    assert items[0]["needs_supplement"] is True
    assert items[0]["slot"] == "投资额"


def test_merge_from_plain_points():
    from report_writing.domain.point_items_sync import merge_generated_point_items

    pts, items = merge_generated_point_items(["A（待补充：项目名）"], None)
    assert pts == ["A（待补充：项目名）"]
    assert items[0]["needs_supplement"] is True
    assert items[0]["slot"] == "项目名"


def test_points_from_items():
    items = [{"id": "a", "text": "line1"}, {"text": "line2"}]
    assert sync_points_from_point_items(items) == ["line1", "line2"]
    assert points_from_point_items(items) == ["line1", "line2"]


def test_outline_from_payload_keeps_point_items_element_id():
    from report_writing.domain.outline import outline_from_payload

    tree = outline_from_payload(
        [
            {
                "key": "c1",
                "sequence": "1",
                "title": "章",
                "points": ["背景（待补充：政策名）"],
                "point_items": [
                    {
                        "id": "p0",
                        "text": "背景（待补充：政策名）",
                        "slot": "政策名",
                        "needs_supplement": True,
                        "element_id": "el-bg",
                        "element_name": "行业背景说明",
                    }
                ],
                "element_ids": ["el-bg"],
            }
        ]
    )
    node = tree.chapters[0]
    assert node.point_items and node.point_items[0].get("element_id") == "el-bg"
    assert node.point_items[0].get("needs_supplement") is False


def test_link_orphan_element_to_supplement_row():
    from report_writing.domain.point_items_sync import link_point_items_to_element_ids

    items = [
        {
            "id": "p0",
            "text": "岗位（待补充：任职文件或岗位说明书）",
            "slot": "任职文件或岗位说明书",
            "needs_supplement": True,
        }
    ]
    elements_by_id = {"el-job": {"id": "el-job", "name": "岗位信息"}}
    linked = link_point_items_to_element_ids(items, ["el-job"], elements_by_id)
    assert linked[0]["element_id"] == "el-job"
    assert linked[0]["needs_supplement"] is False
