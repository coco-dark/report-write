"""configure_chapter 更新要点时同步章节说明与补充提示词。"""

from report_writing.chat.writing import _apply_chapter_config
from report_writing.domain.models import OutlineNode, OutlineTree, WritingTask


def test_apply_chapter_config_regenerates_meta_when_points_change(monkeypatch):
    monkeypatch.setattr("report_writing.infra.llm.live_model", lambda **kw: None)

    task = WritingTask(
        id="t1",
        title="述职",
        category="述职报告",
        outline=OutlineTree(
            chapters=[
                OutlineNode(
                    key="c1",
                    title="职责",
                    describes="旧说明",
                    aux_prompt="旧提示",
                    points=["旧要点"],
                    point_items=[{"id": "p0", "text": "旧要点"}],
                )
            ]
        ),
        chapters=[],
    )
    _apply_chapter_config(
        task,
        "c1",
        {
            "point_items": [
                {"id": "p0", "text": "写清岗位名称（待补充：任职文件）", "slot": "任职文件"},
                {"id": "p1", "text": "分条写职责边界"},
            ],
        },
    )
    node = task.outline.chapters[0]
    assert node.points == [
        "写清岗位名称（待补充：任职文件）",
        "分条写职责边界",
    ]
    assert node.describes != "旧说明"
    assert node.aux_prompt != "旧提示"
    assert "任职文件" in node.aux_prompt


def test_apply_chapter_config_preserves_meta_when_flag_set(monkeypatch):
    monkeypatch.setattr("report_writing.infra.llm.live_model", lambda **kw: None)

    task = WritingTask(
        id="t2",
        outline=OutlineTree(
            chapters=[
                OutlineNode(
                    key="c1",
                    title="职责",
                    describes="手改说明",
                    aux_prompt="手改提示",
                    points=["旧要点"],
                    point_items=[{"id": "p0", "text": "旧要点"}],
                )
            ]
        ),
        chapters=[],
    )
    _apply_chapter_config(
        task,
        "c1",
        {
            "point_items": [{"id": "p0", "text": "新要点"}],
            "preserve_chapter_meta": True,
        },
    )
    node = task.outline.chapters[0]
    assert node.describes == "手改说明"
    assert node.aux_prompt == "手改提示"
