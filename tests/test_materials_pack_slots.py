"""资料包引用、文件插槽检索、入库列表。"""

from __future__ import annotations

from pathlib import Path

from report_writing.chat.materials_pack_gate import (
    MATERIALS_PACK_ACTIONS,
    apply_materials_pack_choice,
    collect_write_slot_gaps,
    draft_slot_upload_reminder,
    materials_pack_gate_satisfied,
    offer_materials_pack_cards,
    remind_slot_upload_before_write,
    task_needs_materials_pack,
)
from report_writing.domain.elements import resolve_elements_for_chapter
from report_writing.domain.models import (
    ChapterRequirement,
    ChatSession,
    ElementNode,
    FileSlotDef,
    OutlineNode,
    OutlineTree,
    WritingBrief,
    WritingTask,
)
from report_writing.services.materials_pack import (
    collect_file_slot_schema,
    create_pack,
    effective_slot_rows,
    list_packs,
    reorder_pack_slots,
    search_in_slot_file,
    set_pack_in_library,
    upload_slot_file,
)
from report_writing.services.store import TaskStore


def test_new_pack_not_in_library_until_confirm(tmp_path: Path):
    pack = create_pack(tmp_path, name="项目甲", in_library=False)
    assert pack.in_library is False
    assert [p.id for p in list_packs(tmp_path, in_library=True)] == []
    assert [p.id for p in list_packs(tmp_path, in_library=False)] == [pack.id]

    updated = set_pack_in_library(tmp_path, pack.id, True)
    assert updated.in_library is True
    assert [p.id for p in list_packs(tmp_path, in_library=True)] == [pack.id]
    assert list_packs(tmp_path, in_library=False) == []


def test_two_tasks_share_same_pack_file_change(tmp_path: Path):
    pack = create_pack(tmp_path, name="共享包", in_library=False)
    upload_slot_file(
        tmp_path,
        pack.id,
        1,
        filename="概况.txt",
        data="工程名称：示范项目甲\n建设地点：上海".encode("utf-8"),
    )

    for tid in ("w-share-a", "w-share-b"):
        task = WritingTask(
            id=tid,
            title=f"文档{tid}",
            materials_pack_id=pack.id,
            element_nodes=[
                ElementNode(
                    id="el-slot",
                    name="概况槽",
                    kind="file_slot",
                    file_slots=[FileSlotDef(no=1, name="概况", query="工程名称")],
                )
            ],
            outline=OutlineTree(chapters=[OutlineNode(key="ch1", title="一", level=1)]),
        )
        TaskStore(tmp_path / "tasks" / tid).save(task)

    a = TaskStore(tmp_path / "tasks" / "w-share-a").load()
    b = TaskStore(tmp_path / "tasks" / "w-share-b").load()
    assert a.materials_pack_id == b.materials_pack_id == pack.id

    upload_slot_file(
        tmp_path,
        pack.id,
        1,
        filename="概况.txt",
        data="工程名称：示范项目乙\n建设地点：北京".encode("utf-8"),
    )

    for tid in ("w-share-a", "w-share-b"):
        task = TaskStore(tmp_path / "tasks" / tid).load()
        req = ChapterRequirement(key="ch1", title="一", element_ids=["el-slot"])
        resolve_elements_for_chapter(
            req,
            list(task.element_nodes or []),
            None,
            materials_pack_id=task.materials_pack_id,
            home=tmp_path,
        )
        assert "示范项目乙" in (req.element_results[0]["value"] or "")
        assert "示范项目甲" not in (req.element_results[0]["value"] or "")


def test_file_slot_resolve_only_searches_slot_file(tmp_path: Path):
    pack = create_pack(tmp_path, name="槽位包", in_library=False)
    upload_slot_file(
        tmp_path,
        pack.id,
        1,
        filename="slot1.txt",
        data="本文件含关键词：消防验收结论合格。".encode("utf-8"),
    )
    upload_slot_file(
        tmp_path,
        pack.id,
        2,
        filename="slot2.txt",
        data="本文件含关键词：结构安全等级一级。".encode("utf-8"),
    )

    elements = [
        ElementNode(
            id="e1",
            name="消防",
            kind="file_slot",
            file_slots=[FileSlotDef(no=1, name="消防", query="消防验收")],
        )
    ]
    req = ChapterRequirement(key="ch1", title="一", element_ids=["e1"])
    resolve_elements_for_chapter(
        req,
        elements,
        None,
        materials_pack_id=pack.id,
        home=tmp_path,
    )
    value = req.element_results[0]["value"] or ""
    assert "消防验收" in value
    assert "结构安全" not in value


def test_file_slot_resolve_when_home_is_user_workspace(tmp_path: Path):
    """流水线 home 常是用户 workspace；不得再套一层 users/…/workspace 导致槽位误判缺失。"""
    from report_writing.services.materials_pack import _as_user_workspace, get_pack, resolve_slot_file_path
    from report_writing.services.users import set_current_user_id

    set_current_user_id("u_slot_ws")
    pack = create_pack(tmp_path, name="批复包", in_library=False)
    upload_slot_file(
        tmp_path,
        pack.id,
        3,
        filename="批复.txt",
        data="电压等级：220kV\n建设规模：示范工程".encode("utf-8"),
    )
    workspace = _as_user_workspace(tmp_path)
    assert workspace.name == "workspace"
    # 模拟 run_chapters：task_dir=workspace/tasks/w… → home=workspace
    pack2 = get_pack(workspace, pack.id)
    assert pack2 is not None
    assert resolve_slot_file_path(workspace, pack2, 3) is not None

    elements = [
        ElementNode(
            id="e1",
            name="建设规模",
            kind="file_slot",
            file_slots=[FileSlotDef(no=3, name="批复文件", query="电压等级")],
        )
    ]
    req = ChapterRequirement(key="ch1", title="建设规模", element_ids=["e1"])
    resolve_elements_for_chapter(
        req,
        elements,
        None,
        materials_pack_id=pack.id,
        home=workspace,
    )
    row = req.element_results[0]
    assert "220kV" in (row.get("value") or "")
    assert row.get("query") == "电压等级"
    assert not row.get("slot_missing")


def test_file_slot_empty_when_pack_skipped(tmp_path: Path):
    elements = [
        ElementNode(
            id="e1",
            name="槽",
            kind="file_slot",
            file_slots=[FileSlotDef(no=1, name="概况", query="工程")],
        )
    ]
    req = ChapterRequirement(key="ch1", title="一", element_ids=["e1"])
    resolve_elements_for_chapter(
        req,
        elements,
        None,
        materials_pack_id="",
        home=tmp_path,
    )
    assert req.element_results[0]["value"] == ""
    assert req.element_results[0].get("slot_missing") == [1]
    assert req.element_results[0].get("slot_skip_search") is True


def test_file_slot_missing_marks_skip_when_unfilled(tmp_path: Path):
    """资料包有 schema 但槽位未上传文件：不检索，并标 slot_missing。"""
    pack = create_pack(tmp_path, name="空槽包", in_library=False)
    elements = [
        ElementNode(
            id="e1",
            name="概况",
            kind="file_slot",
            file_slots=[FileSlotDef(no=1, name="概况", query="工程名称")],
        )
    ]
    req = ChapterRequirement(key="ch1", title="一", element_ids=["e1"])
    resolve_elements_for_chapter(
        req,
        elements,
        None,
        materials_pack_id=pack.id,
        home=tmp_path,
    )
    row = req.element_results[0]
    assert row["value"] == ""
    assert row.get("slot_missing") == [1]
    assert row.get("slot_skip_search") is True


def test_collect_file_slot_schema_merges_by_no():
    elements = [
        ElementNode(
            id="a",
            kind="file_slot",
            file_slots=[FileSlotDef(no=1, name="概况", query="工程名称")],
        ),
        ElementNode(
            id="b",
            kind="file_slot",
            file_slots=[
                FileSlotDef(no=1, name="", query=""),
                FileSlotDef(no=2, name="图纸", query="平面图"),
            ],
        ),
    ]
    schema = collect_file_slot_schema(elements)
    assert [s["no"] for s in schema] == [1, 2]
    assert schema[0]["name"] == "概况"
    assert schema[0]["query"] == "工程名称"
    assert schema[1]["name"] == "图纸"


def test_materials_pack_gate_and_library_cards(tmp_path: Path):
    pending = create_pack(tmp_path, name="待入", in_library=False)
    library = create_pack(tmp_path, name="已入", in_library=True)
    task = WritingTask(
        id="w-gate",
        title="门控",
        element_nodes=[
            ElementNode(
                id="e1",
                kind="file_slot",
                file_slots=[FileSlotDef(no=1, name="概况", query="工程")],
            )
        ],
    )
    assert task_needs_materials_pack(task) is True
    session = ChatSession(id="s1", task_id=task.id, freeform={})
    assert materials_pack_gate_satisfied(session, task) is False

    session.freeform = {"materials_pack_skipped": True}
    assert materials_pack_gate_satisfied(session, task) is True

    session.freeform = {}
    task.materials_pack_id = library.id
    assert materials_pack_gate_satisfied(session, task) is True

    task.materials_pack_id = ""
    reply, cards = offer_materials_pack_cards(session, tmp_path, task)
    assert "资料包" in reply
    assert cards and cards[0].type == "materials_pack_gate"
    payload = cards[0].payload or {}
    assert any(p["id"] == library.id for p in payload.get("library_packs") or [])
    assert any(p["id"] == pending.id for p in payload.get("pending_packs") or [])
    assert any(s["no"] == 1 for s in payload.get("slot_schema") or [])


def test_search_in_slot_file_prefers_matching_paragraph(tmp_path: Path):
    path = tmp_path / "doc.txt"
    path.write_text("无关前言。\n\n本段写明建设规模为三万平方米。\n\n结尾。", encoding="utf-8")
    hits = search_in_slot_file(path, "建设规模", limit=2)
    assert hits
    assert "三万平方米" in hits[0]


def test_materials_pack_chat_actions_list_add_remove(tmp_path: Path):
    assert "materials_pack_list" in MATERIALS_PACK_ACTIONS
    TaskStore(tmp_path / "tasks" / "w-act").save(
        WritingTask(id="w-act", title="动作", element_nodes=[])
    )
    session = ChatSession(id="s-act", task_id="w-act", freeform={})
    reply, cards = apply_materials_pack_choice(
        tmp_path,
        session,
        "materials_pack_create",
        {"task_id": "w-act", "name": "对话包"},
    )
    assert "对话包" in reply
    assert cards and cards[0].type == "materials_pack_bound"
    pack_id = cards[0].payload["pack_id"]

    reply, cards = apply_materials_pack_choice(
        tmp_path, session, "materials_pack_add_slot", {"name": "批复", "task_id": "w-act"}
    )
    assert "批复" in reply
    slots = cards[0].payload.get("slots") or []
    assert any(s.get("name") == "批复" for s in slots)

    no = next(s["no"] for s in slots if s.get("name") == "批复")
    reply, cards = apply_materials_pack_choice(
        tmp_path,
        session,
        "materials_pack_remove_slot",
        {"slot_no": no, "task_id": "w-act"},
    )
    assert f"删除槽位 {no}" in reply or "删除" in reply

    reply, cards = apply_materials_pack_choice(tmp_path, session, "materials_pack_list", {})
    assert cards and cards[0].type == "materials_pack_list"
    assert any(p["id"] == pack_id for p in cards[0].payload.get("pending_packs") or [])


def test_collect_write_slot_gaps_and_soft_reminder(tmp_path: Path, monkeypatch):
    pack = create_pack(tmp_path, name="提醒包", in_library=False)
    task = WritingTask(
        id="w-remind",
        title="提醒",
        materials_pack_id=pack.id,
        element_nodes=[
            ElementNode(
                id="el-slot",
                name="概况槽",
                kind="file_slot",
                file_slots=[FileSlotDef(no=1, name="概况", query="工程")],
            )
        ],
        outline=OutlineTree(
            chapters=[OutlineNode(key="ch1", title="概述", level=1, element_ids=["el-slot"])]
        ),
        brief=WritingBrief(
            template_id="t",
            template_version="1",
            chapters=[ChapterRequirement(key="ch1", title="概述", element_ids=["el-slot"])],
        ),
    )
    gaps = collect_write_slot_gaps(tmp_path, task, ["ch1"])
    assert gaps
    assert gaps[0]["missing_nos"] == [1]
    assert "概况槽" in gaps[0]["element_name"]

    draft = draft_slot_upload_reminder(gaps, pack_bound=True)
    assert "资料包" in draft
    assert "不强制" in draft

    monkeypatch.setenv("REPORT_WRITING_REPLY_POLISH", "0")
    session = ChatSession(id="s-remind", freeform={})
    tokens: list[str] = []

    def _emit(ev):
        if ev.get("type") == "token":
            tokens.append(str(ev.get("text") or ""))

    text = remind_slot_upload_before_write(
        tmp_path,
        session,
        task,
        ["ch1"],
        invoke=None,
        emit=_emit,
    )
    assert "资料包" in text
    assert tokens and "资料包" in "".join(tokens)

    upload_slot_file(
        tmp_path,
        pack.id,
        1,
        filename="概况.txt",
        data="工程名称：已上传".encode("utf-8"),
    )
    assert collect_write_slot_gaps(tmp_path, task, ["ch1"]) == []
    assert (
        remind_slot_upload_before_write(tmp_path, session, task, ["ch1"], invoke=None, emit=None)
        == ""
    )

def test_first_save_keeps_slot_order_not_numeric(tmp_path: Path):
    """第一次写入按传入顺序，不按编号升序打乱；路径失效仍能找到槽位文件。"""
    from report_writing.services.materials_pack import (
        apply_slot_schema,
        get_pack,
        resolve_slot_file_path,
        save_pack,
    )

    pack = create_pack(
        tmp_path,
        name="首存顺序",
        slot_schema=[
            {"no": 4, "name": "槽位 4"},
            {"no": 2, "name": "设想报告"},
            {"no": 3, "name": "批复文件"},
        ],
    )
    assert pack.slot_order == [4, 2, 3]
    rows = effective_slot_rows(pack)
    assert [r["no"] for r in rows] == [4, 2, 3]

    upload_slot_file(
        tmp_path,
        pack.id,
        4,
        filename="批复.txt",
        data="批复文号：已上传".encode("utf-8"),
    )
    stored = get_pack(tmp_path, pack.id)
    assert stored is not None
    stored.slots_fill[0].source_path = "missing/not-there.txt"
    save_pack(tmp_path, stored)
    found = resolve_slot_file_path(tmp_path, stored, 4)
    assert found is not None and found.is_file()
    assert "批复" in found.name

    fresh = create_pack(tmp_path, name="空序", slot_schema=[])
    fresh.slot_order = []
    updated = apply_slot_schema(
        tmp_path,
        fresh,
        [{"no": 3, "name": "后"}, {"no": 1, "name": "先"}],
    )
    assert updated.slot_order[:2] == [3, 1]


def test_freeform_keeps_own_pending_pack(tmp_path: Path):
    """无蓝本自己的预选资料包要出现在运行时里，不能被当成上一份蓝本清掉。"""
    from report_writing.chat.session_runtime import runtime_public_view
    from report_writing.domain.models import ChatSession

    own = ChatSession(
        id="s-own",
        freeform={
            "writing_path": "freeform",
            "title": "自动大纲",
            "pending_materials_pack_id": "mp-own",
        },
    )
    view = runtime_public_view(own, tmp_path)
    assert view["pending_materials_pack_id"] == "mp-own"

    foreign = ChatSession(
        id="s-foreign",
        freeform={
            "writing_path": "freeform",
            "title": "自动大纲",
            "pending_materials_pack_id": "mp-old",
            "pending_materials_pack_by_blueprint": {"bpOld": "mp-old"},
        },
    )
    view2 = runtime_public_view(foreign, tmp_path)
    assert view2["pending_materials_pack_id"] == ""


def test_reorder_pack_slots_display_order(tmp_path: Path):
    pack = create_pack(
        tmp_path,
        name="排序包",
        slot_schema=[
            {"no": 2, "name": "设想报告"},
            {"no": 3, "name": "批复文件"},
            {"no": 4, "name": "槽位 4"},
        ],
    )
    rows = effective_slot_rows(pack)
    assert [r["no"] for r in rows] == [2, 3, 4]
    updated = reorder_pack_slots(tmp_path, pack.id, [4, 2, 3])
    assert updated.slot_order == [4, 2, 3]
    rows2 = effective_slot_rows(updated)
    assert [r["no"] for r in rows2] == [4, 2, 3]
    assert [r["name"] for r in rows2] == ["槽位 4", "设想报告", "批复文件"]


def test_saving_one_element_keeps_other_chapter_slots(tmp_path: Path):
    """保存建设规模时，请求里若丢了变电站站址的槽位，沿用已保存的，不要整单拒绝。"""
    from report_writing.controller import ensure_task
    from report_writing.controller.outline_flow import save_element_nodes
    from report_writing.services.store import TaskStore

    tid = "w-keep-other-slots"
    path = tmp_path / "tasks" / tid
    ensure_task(path)
    task = TaskStore(path).load()
    task.element_nodes = [
        ElementNode(
            id="el-scale",
            name="建设规模",
            kind="file_slot",
            file_slots=[FileSlotDef(no=1, name="批复文件", query="建设规模")],
        ),
        ElementNode(
            id="el-site",
            name="变电站站址",
            kind="file_slot",
            file_slots=[FileSlotDef(no=1, name="批复文件", query="站址")],
        ),
    ]
    TaskStore(path).save(task)

    saved = save_element_nodes(
        path,
        [
            {
                "id": "el-scale",
                "name": "建设规模",
                "kind": "file_slot",
                "file_slots": [{"no": 1, "name": "批复文件", "query": "建设规模"}],
            },
            {
                "id": "el-site",
                "name": "变电站站址",
                "kind": "file_slot",
                "file_slots": [],
            },
        ],
    )
    by_id = {item.id: item for item in saved.element_nodes}
    assert by_id["el-site"].file_slots[0].query == "站址"
    assert by_id["el-scale"].file_slots[0].query == "建设规模"

