"""蓝本槽位变更同步到资料包。"""

from __future__ import annotations

from pathlib import Path

from report_writing.domain.models import ElementNode, FileSlotDef, WritingTask
from report_writing.services.materials_pack import (
    apply_slot_schema,
    create_pack,
    get_pack,
    sync_packs_for_task,
    upload_slot_file,
)
from report_writing.services.store import TaskStore


def test_apply_slot_schema_adds_and_prunes(tmp_path: Path):
    pack = create_pack(tmp_path, name="包A", slot_schema=[{"no": 1, "name": "概况", "query": "工程"}])
    assert [s.no for s in pack.slot_schema] == [1]

    pack = apply_slot_schema(
        tmp_path,
        pack,
        [{"no": 1, "name": "概况", "query": "工程名称"}, {"no": 2, "name": "图纸", "query": "平面图"}],
        blueprint_id="bp-1",
    )
    assert [s.no for s in pack.slot_schema] == [1, 2]
    # 资料包不落检索词
    assert pack.slot_schema[0].query == ""
    assert pack.slot_schema[0].name == "概况"
    assert "bp-1" in pack.blueprint_ids

    pack = apply_slot_schema(
        tmp_path,
        pack,
        [{"no": 2, "name": "图纸", "query": "平面图"}],
        blueprint_id="bp-1",
        prune=True,
    )
    assert [s.no for s in pack.slot_schema] == [2]


def test_sync_packs_preserves_pack_slot_names_when_elements_empty(tmp_path: Path):
    """要素 file_slot 常只有 no、空 name；同步不得抹掉资料包槽位名称。"""
    pack = create_pack(
        tmp_path,
        name="变电站资料包",
        in_library=True,
        slot_schema=[
            {"no": 1, "name": "线路报告", "query": ""},
            {"no": 2, "name": "设想报告", "query": ""},
            {"no": 3, "name": "批复文件", "query": ""},
        ],
        blueprint_id="bp-demo",
    )
    upload_slot_file(tmp_path, pack.id, 1, filename="a.txt", data=b"a")
    upload_slot_file(tmp_path, pack.id, 2, filename="b.txt", data=b"b")
    upload_slot_file(tmp_path, pack.id, 3, filename="c.txt", data=b"c")

    task = WritingTask(
        id="w-1",
        title="写作",
        source_blueprint_id="bp-demo",
        materials_pack_id=pack.id,
        element_nodes=[
            ElementNode(
                id="e1",
                kind="file_slot",
                name="槽",
                file_slots=[
                    FileSlotDef(no=1, name="", query="线路"),
                    FileSlotDef(no=2, name="", query="设想"),
                    FileSlotDef(no=3, name="", query="批复"),
                ],
            )
        ],
    )
    TaskStore(tmp_path / "tasks" / task.id).save(task)

    sync_packs_for_task(tmp_path, task)
    got = get_pack(tmp_path, pack.id)
    assert [s.name for s in got.slot_schema] == ["线路报告", "设想报告", "批复文件"]


def test_sync_packs_for_task_updates_library_pack(tmp_path: Path):
    pack = create_pack(
        tmp_path,
        name="共享",
        in_library=True,
        slot_schema=[{"no": 1, "name": "旧", "query": "a"}],
        blueprint_id="bp-demo",
    )
    task = WritingTask(
        id="w-1",
        title="写作",
        source_blueprint_id="bp-demo",
        materials_pack_id=pack.id,
        element_nodes=[
            ElementNode(
                id="e1",
                kind="file_slot",
                name="槽",
                file_slots=[
                    FileSlotDef(no=1, name="概况", query="工程"),
                    FileSlotDef(no=2, name="消防", query="验收"),
                ],
            )
        ],
    )
    TaskStore(tmp_path / "tasks" / task.id).save(task)

    updated = sync_packs_for_task(tmp_path, task)
    assert updated
    got = get_pack(tmp_path, pack.id)
    assert [s.no for s in got.slot_schema] == [1, 2]
    assert got.slot_schema[1].name == "消防"


def test_prune_keeps_filled_orphan_slot_file(tmp_path: Path):
    pack = create_pack(
        tmp_path,
        name="保文件",
        slot_schema=[{"no": 1, "name": "A", "query": "a"}, {"no": 2, "name": "B", "query": "b"}],
    )
    upload_slot_file(tmp_path, pack.id, 1, filename="a.txt", data=b"hello")
    pack = get_pack(tmp_path, pack.id)
    pack = apply_slot_schema(
        tmp_path,
        pack,
        [{"no": 2, "name": "B", "query": "b"}],
        prune=True,
    )
    # 有文件的孤儿槽仍保留 schema（含名称），避免刷新后只见「已解析」无名称
    assert [s.no for s in pack.slot_schema] == [1, 2]
    assert pack.slot_schema[0].name == "A"
    assert any(int(f.slot_no) == 1 and f.file_name for f in pack.slots_fill)
