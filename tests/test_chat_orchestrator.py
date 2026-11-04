"""对话写报告编排：有效性、机器人过滤、phase 转移。"""

from __future__ import annotations

import json
from pathlib import Path

from report_writing.chat_orchestrator import (
    clone_blueprint_to_writing_task,
    handle_chat,
    is_blueprint_valid,
    is_chapter_analyzed,
    partition_blueprint_chapters,
)
from report_writing.services.chat_session import get_session
from report_writing.controller import ensure_task
from report_writing.domain.models import (
    ChatPhase,
    ChatRequest,
    OutlineNode,
    OutlineTree,
    WritingTask,
)
from report_writing.services.robots import create_robot, filter_blueprints_for_robot, search_blueprints
from report_writing.services.store import TaskStore


def _invoke(system: str, user: str) -> str:
    return f"echo:{user[:40]}"


def _bind_robot_blueprints(home: Path, robot_id: str, *blueprint_ids: str) -> None:
    from report_writing.services.robots import get_robot, update_robot

    robot = get_robot(home, robot_id)
    assert robot is not None
    data = robot.to_dict()
    data["blueprint_ids"] = list(blueprint_ids)
    update_robot(home, robot_id, data)


def _save_blueprint(home: Path, task_id: str, *, points: bool, confirmed: bool = False) -> WritingTask:
    path = home / "tasks" / task_id
    ensure_task(path)
    chapters = [
        OutlineNode(
            key="c1",
            sequence="1",
            title="工程概况",
            level=1,
            points=["写项目背景"] if points else [],
            keywords=["工程"] if points else [],
        ),
        OutlineNode(key="c2", sequence="2", title="结论", level=1, points=["总结"] if points else []),
    ]
    task = TaskStore(path).load()
    task.title = f"蓝本-{task_id}"
    task.category = "安全评价"
    task.tags = ["预评价"]
    task.outline = OutlineTree(chapters=chapters)
    task.outline_confirmed = confirmed
    TaskStore(path).save(task)
    return task


def test_blueprint_validity_rules(tmp_path: Path):
    empty = WritingTask(id="a")
    assert is_blueprint_valid(empty) is False

    no_points = WritingTask(
        id="b",
        outline=OutlineTree(chapters=[OutlineNode(key="k", title="t")]),
        outline_confirmed=False,
    )
    assert is_blueprint_valid(no_points) is True

    with_points = WritingTask(
        id="c",
        outline=OutlineTree(chapters=[OutlineNode(key="k", title="t", points=["p"])]),
        outline_confirmed=False,
    )
    assert is_blueprint_valid(with_points) is True

    confirmed = WritingTask(
        id="d",
        outline=OutlineTree(chapters=[OutlineNode(key="k", title="t")]),
        outline_confirmed=True,
    )
    assert is_blueprint_valid(confirmed) is True


def test_robot_filters_blueprint_search(tmp_path: Path):
    home = tmp_path
    _save_blueprint(home, "bp_a", points=True)
    _save_blueprint(home, "bp_b", points=True)
    robot = create_robot(
        home,
        {
            "id": "r1",
            "name": "限定",
            "blueprint_ids": ["bp_a"],
        },
    )
    all_items = search_blueprints(home, q="", robot_id="")
    assert {item.id for item in all_items} >= {"bp_a", "bp_b"}

    scoped = search_blueprints(home, q="", robot_id="r1")
    assert [item.id for item in scoped] == ["bp_a"]

    filtered = filter_blueprints_for_robot(all_items, robot)
    assert [item.id for item in filtered] == ["bp_a"]

    by_q = search_blueprints(home, q="安全", robot_id="r1")
    assert len(by_q) == 1 and by_q[0].id == "bp_a"


def test_search_matches_blueprint_description(tmp_path: Path):
    home = tmp_path
    path = home / "tasks" / "bp_desc"
    ensure_task(path)
    task = TaskStore(path).load()
    task.title = "蓝本2"
    task.category = "安全"
    task.tags = ["安全"]
    task.description = "用于写安全预评价报告"
    task.outline = OutlineTree(
        chapters=[OutlineNode(key="c1", title="概况", points=["p"], sequence="1")]
    )
    task.outline_confirmed = True
    TaskStore(path).save(task)

    hits = search_blueprints(home, q="帮我写一份安全预评价报告")
    assert any(item.id == "bp_desc" for item in hits)
    assert hits[0].id == "bp_desc"


def test_vague_write_report_does_not_auto_select_blueprint(tmp_path: Path):
    """泛泛「帮我写一份报告」即使只匹配到一份蓝本，也必须先让用户点「选用」。"""
    home = tmp_path
    create_robot(home, {"id": "r_report", "name": "报告机器人"})
    _bind_robot_blueprints(home, "r_report", "bp_env")
    path = home / "tasks" / "bp_env"
    ensure_task(path)
    task = TaskStore(path).load()
    task.title = "工程环境影响报告"
    task.description = "输变电工程环境影响评价"
    task.outline = OutlineTree(
        chapters=[
            OutlineNode(key="c1", title="编制依据", points=["p"], sequence="1"),
            OutlineNode(key="c2", title="工程内容及规模", points=["p2"], sequence="2"),
        ]
    )
    task.outline_confirmed = True
    TaskStore(path).save(task)

    def invoke(system: str, user: str) -> str:
        if "蓝本选型" in system or "候选蓝本" in user:
            return json.dumps(
                {"reply": "匹配到工程环境影响报告。", "ids": ["bp_env"]},
                ensure_ascii=False,
            )
        return "ok"

    resp = handle_chat(
        home,
        ChatRequest(message="帮我写一份报告", robot_id="r_report"),
        invoke=invoke,
    )
    session = get_session(home, resp.session_id)
    assert session is not None
    assert session.blueprint_id == ""
    assert "订上" not in resp.reply
    assert "先给这份文档起个名字" not in (resp.reply or "")
    assert not any(c.type == "chapter_picker" for c in resp.cards)


def test_agent_recommends_by_description(tmp_path: Path):
    home = tmp_path
    create_robot(home, {"id": "r_report", "name": "报告机器人"})
    _bind_robot_blueprints(home, "r_report", "bp_desc", "bp_other")
    path = home / "tasks" / "bp_desc"
    ensure_task(path)
    task = TaskStore(path).load()
    task.title = "蓝本2"
    task.description = "用于写安全预评价报告"
    task.outline = OutlineTree(
        chapters=[OutlineNode(key="c1", title="概况", points=["p"], sequence="1")]
    )
    task.outline_confirmed = True
    TaskStore(path).save(task)
    _save_blueprint(home, "bp_other", points=True)

    def invoke(system: str, user: str) -> str:
        if "蓝本选型" in system or "候选蓝本" in user:
            return json.dumps(
                {"reply": "用途说明匹配安全预评价，推荐蓝本2。", "ids": ["bp_desc"]},
                ensure_ascii=False,
            )
        return "ok"

    resp = handle_chat(
        home,
        ChatRequest(message="写一份安全预评价报告", robot_id="r_report"),
        invoke=invoke,
    )
    session = get_session(home, resp.session_id)
    assert session is not None
    assert session.blueprint_id in {"", "bp_desc"}
    if session.blueprint_id == "bp_desc":
        assert not any(c.type == "blueprint_list" for c in resp.cards)
    assert "先给这份文档起个名字" not in (resp.reply or "")


def test_phase_select_invalid_then_handoff(tmp_path: Path):
    home = tmp_path
    _save_blueprint(home, "bp_bad", points=False, confirmed=False)
    resp = handle_chat(
        home,
        ChatRequest(message="写报告", action="select_blueprint", action_payload={"blueprint_id": "bp_bad"}),
        invoke=_invoke,
    )
    assert resp.phase == ChatPhase.CREATE_HANDOFF.value
    # 有目录但未分析：向导/小窗提示承接，会话不下发章节树
    assert any(
        card.type in {"doc_window_hint", "handoff", "blueprint_wizard"} for card in resp.cards
    )
    assert not any(card.type == "chapter_picker" for card in resp.cards)
    session = get_session(home, resp.session_id)
    assert session is not None
    assert session.blueprint_id == "bp_bad"


def test_general_chat_without_robot_not_forced_to_report(tmp_path: Path):
    home = tmp_path
    resp = handle_chat(home, ChatRequest(message="帮我写一封感谢信"), invoke=_invoke)
    assert resp.phase == ChatPhase.INTENT.value
    assert "感谢信" in resp.reply or "蓝本" in resp.reply or "报告" in resp.reply
    assert not resp.cards


def test_stuck_writing_phase_recovers_on_new_request(tmp_path: Path):
    """选中蓝本后 phase 曾误标为 writing 且无 task 时，不应只回「尚未创建写作任务」。"""
    home = tmp_path
    create_robot(home, {"id": "r_report", "name": "报告机器人"})
    _bind_robot_blueprints(home, "r_report", "bp_ok")
    path = home / "tasks" / "bp_ok"
    ensure_task(path)
    task = TaskStore(path).load()
    task.title = "报告书写"
    task.description = "用于书写安全类报告"
    task.outline = OutlineTree(
        chapters=[OutlineNode(key="c1", title="概况", points=["p"], sequence="1")]
    )
    task.outline_confirmed = True
    TaskStore(path).save(task)

    # 模拟旧会话卡死：phase=writing 且无 task_id
    first = handle_chat(
        home,
        ChatRequest(message="看看蓝本", robot_id="r_report"),
        invoke=_invoke,
    )
    from report_writing.services.chat_session import get_session, save_session

    session = get_session(home, first.session_id)
    assert session is not None
    session.phase = ChatPhase.WRITING.value
    session.blueprint_id = ""
    session.task_id = ""
    save_session(home, session)

    recovered = handle_chat(
        home,
        ChatRequest(
            session_id=first.session_id,
            robot_id="r_report",
            message="帮我写一份安全评价报告",
        ),
        invoke=_invoke,
    )
    assert "尚未创建写作任务" not in recovered.reply
    assert recovered.phase in {
        ChatPhase.BLUEPRINT_SELECT.value,
        ChatPhase.READY_TO_WRITE.value,
        ChatPhase.FREEFORM_ASK.value,
        ChatPhase.INTENT.value,
    }


def test_list_blueprints_interactive_cards(tmp_path: Path):
    home = tmp_path
    create_robot(home, {"id": "r_report", "name": "报告机器人"})
    _save_blueprint(home, "bp_a", points=True)
    _save_blueprint(home, "bp_b", points=True)
    _bind_robot_blueprints(home, "r_report", "bp_a", "bp_b")
    disabled = _save_blueprint(home, "bp_off", points=True)
    disabled.enabled = False
    TaskStore(home / "tasks" / "bp_off").save(disabled)

    resp = handle_chat(
        home,
        ChatRequest(message="看看有哪些蓝本", robot_id="r_report"),
        invoke=_invoke,
    )
    assert resp.phase == ChatPhase.BLUEPRINT_SELECT.value
    cards = [c for c in resp.cards if c.type == "blueprint_list"]
    assert cards
    ids = {item["id"] for item in cards[0].payload["items"]}
    assert ids >= {"bp_a", "bp_b"}
    assert "bp_off" not in ids
    assert cards[0].payload.get("interactive") is True
    assert cards[0].payload.get("layout") == "table"

    listed = handle_chat(
        home,
        ChatRequest(session_id=resp.session_id, robot_id="r_report", action="list_blueprints"),
        invoke=_invoke,
    )
    assert any(c.type == "blueprint_list" for c in listed.cards)


def test_list_blueprints_skips_unnamed_doc_hang(tmp_path: Path):
    """未定名车道时「看看有哪些蓝本」应列出蓝本，不应先要文档名。"""
    home = tmp_path
    create_robot(home, {"id": "r_report", "name": "报告机器人"})
    _save_blueprint(home, "bp_a", points=True)
    _bind_robot_blueprints(home, "r_report", "bp_a")

    from report_writing.chat.session_documents import begin_document_onboarding, focus_needs_doc_name
    from report_writing.domain.models import ChatSession

    session = ChatSession(id="browse_unnamed", robot_id="r_report")
    begin_document_onboarding(session, home, message="", force_new=True)
    assert focus_needs_doc_name(session)
    from report_writing.services.chat_session import save_session

    save_session(home, session)

    resp = handle_chat(
        home,
        ChatRequest(session_id=session.id, message="看看有哪些蓝本", robot_id="r_report"),
        invoke=_invoke,
    )
    assert "先给这份文档起个名字" not in (resp.reply or "")
    cards = [c for c in resp.cards if c.type == "blueprint_list"]
    assert cards
    assert any(
        item.get("id") == "bp_a"
        for item in (cards[0].payload.get("items") or [])
    )


def test_phase_select_valid_then_clone_ready(tmp_path: Path):
    home = tmp_path
    create_robot(home, {"id": "r_report", "name": "报告机器人"})
    _save_blueprint(home, "bp_ok", points=True, confirmed=True)
    first = handle_chat(
        home,
        ChatRequest(message="写安评报告", robot_id="r_report"),
        invoke=_invoke,
    )
    assert first.session_id
    assert first.phase in {
        ChatPhase.BLUEPRINT_SELECT.value,
        ChatPhase.FREEFORM_ASK.value,
        ChatPhase.INTENT.value,
        ChatPhase.READY_TO_WRITE.value,
    }

    selected = handle_chat(
        home,
        ChatRequest(
            session_id=first.session_id,
            robot_id="r_report",
            action="select_blueprint",
            action_payload={"blueprint_id": "bp_ok"},
        ),
        invoke=_invoke,
    )
    assert selected.phase == ChatPhase.READY_TO_WRITE.value
    assert any(card.type == "doc_window_hint" for card in selected.cards)
    assert not any(card.type == "chapter_picker" for card in selected.cards)

    cloned = clone_blueprint_to_writing_task(home, "bp_ok")
    assert cloned.id.startswith("w")
    assert cloned.id != "bp_ok"
    assert cloned.outline is not None
    assert len(cloned.outline.chapters) == 2
    from report_writing.services.blueprint_catalog import load_blueprint_task

    assert load_blueprint_task("bp_ok") is not None
    assert not (home / "tasks" / "bp_ok" / "task.json").is_file()


def test_freeform_context_after_skip_and_write_intent(tmp_path: Path):
    home = tmp_path
    create_robot(home, {"id": "r_report", "name": "报告机器人", "chat_prompt": "你负责写报告"})
    r1 = handle_chat(
        home,
        ChatRequest(message="帮我起草一份工作说明", robot_id="r_report"),
        invoke=_invoke,
    )
    r2 = handle_chat(
        home,
        ChatRequest(session_id=r1.session_id, robot_id="r_report", message="直接开始写吧"),
        invoke=_invoke,
    )
    assert r2.phase == ChatPhase.FREEFORM_ASK.value
    r3 = handle_chat(
        home,
        ChatRequest(session_id=r1.session_id, robot_id="r_report", message="写一份工作说明啊"),
        invoke=_invoke,
    )
    assert r3.phase in {
        ChatPhase.FREEFORM_ASK.value,
        ChatPhase.FREEFORM_OUTLINE.value,
        ChatPhase.INTENT.value,
    }
    assert "先给这份文档起个名字" not in (r3.reply or "")


def test_freeform_rejects_direct_write_as_title(tmp_path: Path):
    home = tmp_path
    create_robot(home, {"id": "r_report", "name": "报告机器人", "chat_prompt": "你负责写报告"})
    r1 = handle_chat(
        home,
        ChatRequest(action="skip_blueprint"),
        invoke=_invoke,
    )
    assert r1.phase == ChatPhase.FREEFORM_ASK.value
    r2 = handle_chat(
        home,
        ChatRequest(session_id=r1.session_id, message="直接写"),
        invoke=_invoke,
    )
    assert r2.phase == ChatPhase.FREEFORM_ASK.value
    assert (
        "类型" in r2.reply
        or "想写什么" in r2.reply
        or "叫什么" in r2.reply
        or "题目" in r2.reply
    )
    assert not any(card.type == "outline_preview" for card in r2.cards)


def test_skip_blueprint_freeform_path(tmp_path: Path):
    home = tmp_path
    create_robot(home, {"id": "r_report", "name": "报告机器人", "chat_prompt": "你负责写报告"})
    r1 = handle_chat(
        home,
        ChatRequest(message="跳过蓝本，直接写", robot_id="r_report"),
        invoke=_invoke,
    )
    assert r1.phase == ChatPhase.FREEFORM_ASK.value

    r2 = handle_chat(
        home,
        ChatRequest(session_id=r1.session_id, robot_id="r_report", message="安全评价｜某某项目预评价"),
        invoke=_invoke,
    )
    assert r2.phase in {
        ChatPhase.FREEFORM_ASK.value,
        ChatPhase.FREEFORM_OUTLINE.value,
    }
    assert "先给这份文档起个名字" not in (r2.reply or "")

    outline = "1 工程概况\n1.1 项目由来\n2 评价结论"
    r3 = handle_chat(
        home,
        ChatRequest(session_id=r1.session_id, robot_id="r_report", message=outline),
        invoke=_invoke,
    )
    assert r3.phase == ChatPhase.FREEFORM_OUTLINE.value
    assert r3.task_id.startswith("w")
    assert any(card.type == "outline_preview" for card in r3.cards)

    r4 = handle_chat(
        home,
        ChatRequest(
            session_id=r1.session_id,
            robot_id="r_report",
            action="submit_outline",
            action_payload={"outline_text": outline, "confirm": True},
        ),
        invoke=_invoke,
    )
    assert r4.phase in {ChatPhase.FREEFORM_POINTS.value, ChatPhase.READY_TO_WRITE.value}

    r5 = handle_chat(
        home,
        ChatRequest(
            session_id=r1.session_id,
            robot_id="r_report",
            action="skip_freeform_points",
            action_payload={},
        ),
        invoke=_invoke,
    )
    assert r5.phase == ChatPhase.READY_TO_WRITE.value
    assert any(card.type == "start_writing" for card in r5.cards)


def test_resume_after_handoff_when_valid(tmp_path: Path):
    home = tmp_path
    _save_blueprint(home, "bp_fix", points=False, confirmed=False)
    bad = handle_chat(
        home,
        ChatRequest(action="select_blueprint", action_payload={"blueprint_id": "bp_fix"}),
        invoke=_invoke,
    )
    assert bad.phase == ChatPhase.CREATE_HANDOFF.value

    # 用户在蓝本页补齐全部章节分析
    path = home / "tasks" / "bp_fix"
    task = TaskStore(path).load()
    for chapter in task.outline.chapters:
        chapter.points = ["补齐"]
        chapter.keywords = ["k"]
    task.outline_confirmed = True
    TaskStore(path).save(task)

    resumed = handle_chat(
        home,
        ChatRequest(
            session_id=bad.session_id,
            action="resume",
            action_payload={"blueprint_id": "bp_fix"},
        ),
        invoke=_invoke,
    )
    assert resumed.phase == ChatPhase.READY_TO_WRITE.value
    assert any(card.type == "doc_window_hint" for card in resumed.cards)
    assert not any(card.type == "chapter_picker" for card in resumed.cards)


def test_select_blueprint_unanalyzed_gate_and_write_analyzed_only(tmp_path: Path):
    home = tmp_path
    path = home / "tasks" / "bp_partial"
    ensure_task(path)
    task = TaskStore(path).load()
    task.title = "部分已分析"
    task.outline = OutlineTree(
        chapters=[
            OutlineNode(key="c1", sequence="1", title="已分析章", points=["p1"], keywords=["k"]),
            OutlineNode(key="c2", sequence="2", title="未分析章"),
            OutlineNode(key="c3", sequence="3", title="目录", structure_only=True),
        ]
    )
    task.outline_confirmed = True
    TaskStore(path).save(task)

    parts = partition_blueprint_chapters(task)
    assert [n.key for n in parts["analyzed"]] == ["c1"]
    assert [n.key for n in parts["unanalyzed"]] == ["c2"]
    assert is_chapter_analyzed(task.outline.chapters[2]) is True

    selected = handle_chat(
        home,
        ChatRequest(action="select_blueprint", action_payload={"blueprint_id": "bp_partial"}),
        invoke=_invoke,
    )
    assert selected.phase == ChatPhase.CREATE_HANDOFF.value
    assert any(c.type == "doc_window_hint" for c in selected.cards)
    assert not any(c.type == "chapter_picker" for c in selected.cards)

    confirmed = handle_chat(
        home,
        ChatRequest(
            session_id=selected.session_id,
            action="select_write_chapters",
            action_payload={"blueprint_id": "bp_partial", "chapter_keys": ["c1"]},
        ),
        invoke=_invoke,
    )
    assert not any(c.type == "writing_pace" for c in confirmed.cards)
    session = get_session(home, confirmed.session_id)
    assert session is not None
    assert session.freeform.get("write_chapter_keys") == ["c1"]

    cloned = clone_blueprint_to_writing_task(home, "bp_partial", chapter_keys=["c1"])
    keys = [c.key for c in cloned.outline.chapters]
    assert "c1" in keys
    assert "c2" not in keys
    # 无关纯目录节点不再整棵带上，只保留勾选章及其祖先
    assert "c3" not in keys


def test_blueprint_wizard_meta_creates_new_bp(tmp_path: Path):
    """会话里挂着旧 blueprint_id 时，填名称仍应新建蓝本。"""
    home = tmp_path
    old = "bp_old_bind"
    path = home / "tasks" / old
    ensure_task(path)
    task = TaskStore(path).load()
    task.title = "旧蓝本"
    task.sample_ready = True
    task.outline = OutlineTree(chapters=[OutlineNode(key="c1", title="章", sequence="1", points=["p"])])
    task.outline_confirmed = True
    TaskStore(path).save(task)

    started = handle_chat(
        home,
        ChatRequest(
            action="blueprint_wizard",
            action_payload={"step": "meta", "title": "新的蓝本", "blueprint_id": ""},
        ),
        invoke=_invoke,
    )
    session = get_session(home, started.session_id)
    assert session is not None
    new_id = str(session.blueprint_id or session.task_id or "").strip()
    assert new_id
    assert new_id != old
    assert new_id.startswith("bp") or new_id.startswith("w")
    assert "新的蓝本" in (started.reply or "")
    wiz = (session.freeform or {}).get("blueprint_wizard") or {}
    assert wiz.get("step") == "upload"
    assert str(wiz.get("blueprint_id") or wiz.get("working_task_id") or "") in {
        new_id,
        str(session.task_id or ""),
    } or str(wiz.get("title") or "") == "新的蓝本"


def test_writing_cancel_flag():
    from report_writing.services.writing_cancel import clear_writing_cancel, is_writing_cancelled, request_writing_cancel

    clear_writing_cancel("cs_test")
    assert not is_writing_cancelled("cs_test")
    request_writing_cancel("cs_test")
    assert is_writing_cancelled("cs_test")
    clear_writing_cancel("cs_test")
    assert not is_writing_cancelled("cs_test")


def test_sync_confirmed_chapters_skips_without_db(tmp_path, monkeypatch):
    from report_writing.domain.models import ChapterState, ChapterStatus
    from report_writing.services.task_chapters import sync_confirmed_chapters

    monkeypatch.setenv("REPORT_WRITING_DATABASE", "file")
    task = WritingTask(id="w1", chapters=[ChapterState(key="a", status=ChapterStatus.CONFIRMED, official_text="正文")])
    assert sync_confirmed_chapters(task) == 0
