"""P2 边角：开写节奏、资料包槽位/改名、收录多匹配、澄清过期再挂。"""

from __future__ import annotations

from collections.abc import Mapping

from pathlib import Path

from report_writing.chat.action_slots import ACTION_SLOT_SOURCE, try_resolve_action_slot_message
from report_writing.chat.collect_element_library import collect_task_elements_to_library
from report_writing.chat.clarify_flow import (
    begin_clarify,
    get_pending,
    rehang_last_clarify,
    try_handle_pending_message,
)
from report_writing.chat.tool_dispatch import execute_dispatch_tool
from report_writing.chat.materials_pack_gate import apply_materials_pack_choice
from report_writing.controller import ensure_task
from report_writing.domain.models import ChatPhase, ChatSession, ElementNode, OutlineNode, OutlineTree, WritingTask
from report_writing.services.store import TaskStore


def test_start_writing_without_pace_starts_batch(tmp_path: Path):
    """未指定 writing_pace 时默认 batch 直接开写，不再挂 writing_pace 澄清。"""
    tid = "bp_pace"
    path = tmp_path / "tasks" / tid
    ensure_task(path)
    task = TaskStore(path).load()
    task.outline = OutlineTree(
        chapters=[OutlineNode(key="c1", sequence="1", title="概述", points=["a"])]
    )
    TaskStore(path).save(task)
    session = ChatSession(
        id="s-pace",
        phase=ChatPhase.READY_TO_WRITE.value,
        blueprint_id=tid,
        task_id=tid,
        freeform={"write_chapter_keys": ["c1"]},
    )
    hit = execute_dispatch_tool(
        tmp_path,
        session,
        {"tool": "start_writing", "args": {}},
        "开始写吧",
    )
    assert hit is not None
    reply, cards, _ = hit
    assert cards and cards[0].type == "progress"
    assert get_pending(session) is None


def test_pace_oral_alias_resolves(tmp_path: Path):
    session = ChatSession(id="s-alias", phase=ChatPhase.READY_TO_WRITE.value)
    begin_clarify(
        session,
        source="control",
        question="想怎么开写？",
        options=[
            {
                "id": "pace_batch",
                "label": "一口气连着写",
                "slots": {"tool": "start_writing", "args": {"writing_pace": "batch"}},
            },
            {
                "id": "pace_step",
                "label": "写完一章再确认",
                "slots": {"tool": "start_writing", "args": {"writing_pace": "step"}},
            },
        ],
        candidate={"kind": "control_tool", "name": "start_writing", "payload": {}},
        phase_hint="writing_pace",
    )
    hit = try_handle_pending_message(session, "一章一章来", invoke=None)
    assert isinstance(hit, Mapping)
    assert (hit.get("slots") or {}).get("args", {}).get("writing_pace") == "step"


def test_materials_remove_slot_missing_no_hangs(tmp_path: Path):
    TaskStore(tmp_path / "tasks" / "w-slot").save(
        WritingTask(id="w-slot", title="槽位", element_nodes=[])
    )
    session = ChatSession(id="s-slot", task_id="w-slot", freeform={})
    apply_materials_pack_choice(
        tmp_path, session, "materials_pack_create", {"task_id": "w-slot", "name": "包甲"}
    )
    apply_materials_pack_choice(
        tmp_path, session, "materials_pack_add_slot", {"name": "批复", "task_id": "w-slot"}
    )
    apply_materials_pack_choice(
        tmp_path, session, "materials_pack_add_slot", {"name": "环评", "task_id": "w-slot"}
    )
    reply, cards = apply_materials_pack_choice(
        tmp_path, session, "materials_pack_remove_slot", {"task_id": "w-slot"}
    )
    assert cards and cards[0].type == "intent_clarify"
    pending = get_pending(session)
    assert pending["source"] == ACTION_SLOT_SOURCE
    assert "槽位" in reply
    labels = " ".join(str(o.get("label") or "") for o in pending.get("options") or [])
    assert "批复" in labels and "环评" in labels

    oral = try_resolve_action_slot_message(session, "槽位 1", task=None)
    assert oral is not None
    assert (oral.get("slots") or {}).get("slot_no") == 1


def test_materials_rename_free_text_resume(tmp_path: Path):
    TaskStore(tmp_path / "tasks" / "w-ren").save(
        WritingTask(id="w-ren", title="改名", element_nodes=[])
    )
    session = ChatSession(id="s-ren", task_id="w-ren", freeform={})
    apply_materials_pack_choice(
        tmp_path, session, "materials_pack_create", {"task_id": "w-ren", "name": "旧名"}
    )
    reply, cards = apply_materials_pack_choice(
        tmp_path, session, "materials_pack_rename", {"task_id": "w-ren"}
    )
    assert cards and cards[0].type == "intent_clarify"
    assert get_pending(session)["source"] == "materials_name"
    assert "旧名" in reply

    hit = try_handle_pending_message(session, "项目资料包", invoke=None)
    assert isinstance(hit, Mapping)
    assert (hit.get("slots") or {}).get("args", {}).get("name") == "项目资料包"


def test_collect_multi_match_hangs_element_id(tmp_path: Path):
    tid = "w-collect"
    path = tmp_path / "tasks" / tid
    ensure_task(path)
    task = TaskStore(path).load()
    task.element_nodes = [
        ElementNode(id="el-a", name="站址概况", kind="rag_search", query="a"),
        ElementNode(id="el-b", name="站址地貌", kind="rag_search", query="b"),
    ]
    task.outline = OutlineTree(
        chapters=[
            OutlineNode(key="c1", sequence="1", title="站址", element_ids=["el-a", "el-b"]),
        ]
    )
    TaskStore(path).save(task)
    session = ChatSession(id="s-col", phase="writing", task_id=tid, blueprint_id=tid)
    reply, cards = collect_task_elements_to_library(
        tmp_path,
        session,
        "把站址收入要素库",
        {"element_name": "站址"},
    )
    assert cards and cards[0].type == "intent_clarify"
    pending = get_pending(session)
    assert pending["source"] == ACTION_SLOT_SOURCE
    assert pending["candidate"]["name"] == "collect_to_element_library"
    assert "哪一个" in reply
    ids = [str((o.get("slots") or {}).get("element_id") or "") for o in pending.get("options") or []]
    assert "el-a" in ids and "el-b" in ids


def test_rehang_last_clarify_after_expiry():
    session = ChatSession(id="s-rehang", phase="intent", freeform={})
    begin_clarify(
        session,
        source="control",
        question="想怎么开写？",
        options=[
            {
                "id": "pace_batch",
                "label": "一口气连着写",
                "slots": {"tool": "start_writing", "args": {"writing_pace": "batch"}},
            }
        ],
        candidate={"kind": "control_tool", "name": "start_writing", "payload": {}},
        phase_hint="writing_pace",
    )
    # 模拟过期：清 pending，保留 last 快照
    from report_writing.chat.clarify_flow import clear_pending

    clear_pending(session)
    assert get_pending(session) is None
    assert (session.freeform or {}).get("last_intent_clarify")

    rehang = rehang_last_clarify(session)
    assert rehang is not None
    reply, cards = rehang
    assert cards and cards[0].type == "intent_clarify"
    assert "过期" in reply
    pending = get_pending(session)
    assert pending is not None
    assert any(str(o.get("id") or "") == "pace_batch" for o in pending.get("options") or [])


def test_resolve_intent_clarify_recovers_from_cleared_pending(tmp_path: Path):
    """pending 误清后点选：应从 last 快照恢复执行，而不是报「过期了」再逼点一次。"""
    from report_writing.agents.chat_control import run_ui_action_shortcut
    from report_writing.chat.clarify_flow import (
        build_entry_branch_clarify,
        clear_pending,
    )
    from report_writing.chat.session_documents import confirm_writing_document_name

    (tmp_path / "tasks").mkdir(parents=True)
    session = ChatSession(id="s-recover", phase=ChatPhase.INTENT.value)
    confirm_writing_document_name(session, "关于建设智能文档的必要性", home=tmp_path)
    build_entry_branch_clarify(
        session,
        message="帮我写一份关于建设智能文档的必要性",
        title_hint="关于建设智能文档的必要性",
    )
    assert get_pending(session) and get_pending(session).get("source") == "entry_branch"
    clear_pending(session)
    assert get_pending(session) is None

    hit = run_ui_action_shortcut(
        home=tmp_path,
        session=session,
        robot=None,
        invoke=None,
        emit=None,
        action="resolve_intent_clarify",
        payload={
            "option_id": "without_blueprint",
            "option_label": "无蓝本 · 自己写",
        },
        message="无蓝本 · 自己写",
    )
    assert hit is not None
    reply, cards, _ = hit
    assert "过期" not in (reply or "")
    assert "无蓝本" in (reply or "") or any(
        str((getattr(c, "payload", {}) or {}).get("source") or "")
        in {"entry_freeform_mode", "entry_branch"}
        for c in (cards or [])
    )
