"""configure_chapter 保存要点时不覆盖章节说明与补充提示词。"""

from report_writing.chat.writing import _apply_chapter_config
from report_writing.domain.models import ElementNode, FileSlotDef, OutlineNode, OutlineTree, WritingTask


def test_apply_chapter_config_keeps_meta_when_points_change(monkeypatch):
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
    assert node.describes == "旧说明"
    assert node.aux_prompt == "旧提示"


def test_apply_chapter_config_writes_payload_meta(monkeypatch):
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
            "describes": "手改说明",
            "aux_prompt": "手改提示",
        },
    )
    node = task.outline.chapters[0]
    assert node.describes == "手改说明"
    assert node.aux_prompt == "手改提示"


def test_apply_chapter_config_fixed_output_follows_compose():
    task = WritingTask(
        id="t3",
        outline=OutlineTree(
            chapters=[
                OutlineNode(
                    key="c1",
                    title="封面",
                    compose="hybrid",
                    fixed_output=False,
                    reference_text="原文封面",
                )
            ]
        ),
        chapters=[],
    )
    _apply_chapter_config(
        task,
        "c1",
        {"compose": "fixed", "reference_text": "原文封面"},
    )
    node = task.outline.chapters[0]
    assert node.compose == "fixed"
    assert node.fixed_output is True


def test_apply_chapter_config_parent_and_title():
    task = WritingTask(
        id="t4",
        outline=OutlineTree(
            chapters=[
                OutlineNode(key="p1", sequence="7", title="评价", level=1),
                OutlineNode(key="c1", sequence="7.1", title="旧题", level=1, parent=""),
            ]
        ),
        chapters=[],
    )
    _apply_chapter_config(
        task,
        "c1",
        {"title": "评价单元划分", "parent": "7"},
    )
    node = task.outline.chapters[1]
    assert node.title == "评价单元划分"
    assert node.parent == "p1"
    assert node.parent_sequence == "7"
    assert node.level == 2


def test_apply_chapter_config_keeps_file_slots_when_payload_omits_them():
    task = WritingTask(
        id="t5",
        outline=OutlineTree(chapters=[OutlineNode(key="c1", title="职责")]),
        element_nodes=[
            ElementNode(
                id="el1",
                name="批复",
                kind="file_slot",
                file_slots=[FileSlotDef(no=1, name="批复文件", query="建设规模")],
            )
        ],
        chapters=[],
    )
    _apply_chapter_config(
        task,
        "c1",
        {
            "element_ids": ["el1"],
            "elements": [
                {"id": "el1", "name": "批复", "kind": "file_slot", "file_slots": []},
            ],
        },
    )
    el = task.element_nodes[0]
    assert el.file_slots
    assert el.file_slots[0].query == "建设规模"


def test_save_outline_persists_compose(tmp_path):
    from pathlib import Path

    from report_writing.controller import ensure_task
    from report_writing.controller.outline_flow import save_outline
    from report_writing.services.store import TaskStore

    path = tmp_path / "tasks" / "wCompose01"
    ensure_task(path)
    task = TaskStore(path).load()
    task.id = "wCompose01"
    task.outline = OutlineTree(
        chapters=[
            OutlineNode(key="c1", sequence="2.2", title="建设规模", compose="hybrid"),
        ]
    )
    TaskStore(path).save(task)
    node = OutlineNode(
        key="c1",
        sequence="2.2",
        title="建设规模",
        compose="source_only",
        presence="if_materials",
    )
    save_outline(path, [node], replace_tree=True)
    again = TaskStore(path).load()
    assert again.outline.chapters[0].compose == "source_only"
    assert again.outline.chapters[0].presence == "if_materials"
