"""两条主路 + 无蓝本子路冒烟：无蓝本 / 依据模版 / 选用已有蓝本。"""

from __future__ import annotations

import json
from pathlib import Path

from report_writing.chat.actions import _action_select_blueprint, _action_skip_blueprint
from report_writing.chat.freeform_route import _apply_single_freeform_intent
from report_writing.chat.intent_router import (
    WRITING_PATH_BLUEPRINT,
    WRITING_PATH_FREEFORM,
    active_blueprint_wizard,
    get_writing_path,
    user_chose_skip_blueprint,
)
from report_writing.chat.resource_actions import _begin_blueprint_wizard, execute_wizard_command
from report_writing.chat.wizard_state import (
    WIZARD_MODE_CREATE,
    WIZARD_MODE_SELECTED,
    is_selected_wizard_mode,
    seed_selected_blueprint_wizard,
)
from report_writing.controller import ensure_task
from report_writing.domain.models import ChatPhase, ChatSession, OutlineNode, OutlineTree
from report_writing.services.store import TaskStore


# ---------------------------------------------------------------------------
# Path A: 无蓝本（freeform）
# ---------------------------------------------------------------------------


def test_path_freeform_skip_locks_and_blocks_wizard(tmp_path: Path):
    session = ChatSession(
        id="path_ff",
        phase=ChatPhase.INTENT.value,
        blueprint_id="bp_stale",
        freeform={
            "blueprint_wizard": {
                "blueprint_id": "bp_stale",
                "step": "upload",
                "mode": WIZARD_MODE_CREATE,
            }
        },
    )
    reply, cards = _action_skip_blueprint(session, home=tmp_path)
    assert get_writing_path(session) == WRITING_PATH_FREEFORM
    assert user_chose_skip_blueprint(session)
    assert not session.blueprint_id
    assert not (session.freeform or {}).get("blueprint_wizard")
    assert active_blueprint_wizard(session) is None
    # 不得再发创建蓝本向导卡
    assert not any(c.type == "blueprint_wizard" for c in cards)
    assert "蓝本" in reply or "目录" in reply or "类型" in reply or "标题" in reply
    # 无蓝本路径下不得复活向导
    assert active_blueprint_wizard(session) is None


def test_path_freeform_dialogue_maps_study_to_points(tmp_path: Path):
    """无蓝本对话「生成要点/学习」→ generate_points，不走样本学习向导。"""
    session = ChatSession(
        id="path_ff2",
        phase=ChatPhase.FREEFORM_POINTS.value,
        task_id="task_ff",
        freeform={
            "writing_path": WRITING_PATH_FREEFORM,
            "meta_confirmed": True,
            "category": "工作说明",
            "title": "测试报告",
            "outline_confirmed": True,
        },
    )
    path = tmp_path / "tasks" / "task_ff"
    ensure_task(path)
    task = TaskStore(path).load()
    task.title = "测试报告"
    task.outline_confirmed = True
    task.outline = OutlineTree(
        chapters=[OutlineNode(key="c1", title="概述", sequence="1", points=[])]
    )
    TaskStore(path).save(task)

    def invoke(system: str, user: str) -> str:
        return json.dumps(
            [{"key": "c1", "points": ["说明背景"], "describes": "概述", "keywords": ["背景"]}],
            ensure_ascii=False,
        )

    hit = _apply_single_freeform_intent(
        tmp_path,
        session,
        "帮我生成写作要点",
        "generate_points",
        phase=ChatPhase.FREEFORM_POINTS.value,
        tid="task_ff",
        invoke=invoke,
        llm=invoke,
        emit=None,
    )
    assert hit is not None
    reply, cards, _ = hit
    assert reply
    # 无蓝本不得出现「依据样本学习」向导卡
    assert not any(c.type == "blueprint_wizard" for c in cards)
    assert not any(c.type == "analysis_gate" for c in cards)


# ---------------------------------------------------------------------------
# Path B: 新建蓝本（create wizard）
# ---------------------------------------------------------------------------


def test_path_create_blueprint_opens_meta_wizard(tmp_path: Path):
    session = ChatSession(id="path_create", phase=ChatPhase.INTENT.value)
    reply, cards = _begin_blueprint_wizard(session, title_hint="", home=tmp_path, force_new=True)
    wiz = (session.freeform or {}).get("blueprint_wizard") or {}
    assert wiz.get("step") == "meta"
    assert wiz.get("mode") == WIZARD_MODE_CREATE
    assert any(c.type == "blueprint_wizard" for c in cards)
    card = next(c for c in cards if c.type == "blueprint_wizard")
    steps = card.payload.get("steps") or []
    assert any(s.get("id") == "meta" for s in steps)
    assert any(s.get("id") == "upload" for s in steps)
    assert "创建" in reply or "名称" in reply or "蓝本" in reply
    assert "创建蓝本" in (card.title or "") or card.payload.get("step") == "meta"


def test_begin_wizard_unlocks_freeform_so_meta_card_survives(tmp_path: Path):
    """先无蓝本再「我有样本」：清 writing_path=freeform，并下发创建名称卡（前端才不会吞卡）。"""
    session = ChatSession(
        id="path_ff_to_create",
        phase=ChatPhase.FREEFORM_OUTLINE.value,
        freeform={
            "writing_path": WRITING_PATH_FREEFORM,
            "writing_doc_named": True,
            "title": "安全报告",
            "plan": {"goal": "skip_blueprint"},
        },
    )
    reply, cards = _begin_blueprint_wizard(
        session,
        title_hint="安全报告",
        home=tmp_path,
        force_new=True,
        user_message="我有样本 可以通过样本分析",
    )
    assert get_writing_path(session) != WRITING_PATH_FREEFORM
    assert not user_chose_skip_blueprint(session)
    assert session.phase == ChatPhase.CREATE_HANDOFF.value
    assert any(c.type == "blueprint_wizard" for c in cards)
    card = next(c for c in cards if c.type == "blueprint_wizard")
    assert card.payload.get("step") == "meta"
    assert (session.freeform or {}).get("blueprint_wizard", {}).get("mode") == WIZARD_MODE_CREATE
    assert "名称" in reply or "样板" in reply


def test_path_create_wizard_allows_go_study_after_seed(tmp_path: Path):
    bid = "bp_create_study"
    path = tmp_path / "tasks" / bid
    ensure_task(path)
    task = TaskStore(path).load()
    task.title = "新建测试蓝本"
    task.sample_ready = True
    task.outline_confirmed = True
    task.outline = OutlineTree(
        chapters=[
            OutlineNode(key="c1", title="概述", sequence="1"),
            OutlineNode(key="c2", title="工程", sequence="2"),
        ]
    )
    TaskStore(path).save(task)

    session = ChatSession(
        id="path_create2",
        blueprint_id=bid,
        phase=ChatPhase.CREATE_HANDOFF.value,
        freeform={
            "blueprint_wizard": {
                "blueprint_id": bid,
                "step": "study",
                "mode": WIZARD_MODE_CREATE,
                "title": "新建测试蓝本",
            }
        },
    )
    reply, cards = execute_wizard_command(tmp_path, session, "go_study")
    assert "学" in reply
    assert any(c.type == "blueprint_wizard" for c in cards)
    wiz = (session.freeform or {}).get("blueprint_wizard") or {}
    assert wiz.get("mode") == WIZARD_MODE_CREATE
    assert wiz.get("trigger_begin_study") or wiz.get("trigger_chapter_study")


# ---------------------------------------------------------------------------
# Path C: 选用已有蓝本（selected，无创建向导）
# ---------------------------------------------------------------------------


def _ready_blueprint(home: Path, bid: str, *, analyzed: bool = False) -> None:
    path = home / "tasks" / bid
    ensure_task(path)
    task = TaskStore(path).load()
    task.title = "选用测试蓝本"
    task.sample_ready = True
    task.outline_confirmed = True
    task.outline = OutlineTree(
        chapters=[
            OutlineNode(
                key="c1",
                title="概述",
                sequence="1",
                points=["p1"] if analyzed else [],
            ),
            OutlineNode(key="c2", title="工程概况", sequence="2"),
        ]
    )
    TaskStore(path).save(task)


def test_path_select_existing_seeds_ops_without_create_steps(tmp_path: Path):
    bid = "bp_sel_path"
    _ready_blueprint(tmp_path, bid, analyzed=False)
    session = ChatSession(id="path_sel", phase=ChatPhase.INTENT.value)
    reply, cards = _action_select_blueprint(tmp_path, session, {"blueprint_id": bid})

    assert get_writing_path(session) == WRITING_PATH_BLUEPRINT
    assert not user_chose_skip_blueprint(session)
    wiz = (session.freeform or {}).get("blueprint_wizard") or {}
    assert is_selected_wizard_mode(wiz)
    assert wiz.get("step") not in {"meta", "upload", "outline"}
    assert "开始分析" in reply or "分析" in reply or "没学" in reply or "已分析" in reply

    wiz_cards = [c for c in cards if c.type == "blueprint_wizard"]
    assert wiz_cards
    card = wiz_cards[0]
    assert card.payload.get("mode") == WIZARD_MODE_SELECTED
    assert "创建蓝本" not in (card.title or "")
    steps = card.payload.get("steps") or []
    assert not any(s.get("id") == "meta" for s in steps)
    assert not any(s.get("id") == "upload" for s in steps)
    assert any(s.get("id") == "study" for s in steps)


def test_path_select_blocks_create_commands_and_allows_study(tmp_path: Path):
    bid = "bp_sel_ops"
    _ready_blueprint(tmp_path, bid)
    session = ChatSession(id="path_sel2", phase=ChatPhase.INTENT.value)
    _action_select_blueprint(tmp_path, session, {"blueprint_id": bid})

    try:
        execute_wizard_command(tmp_path, session, "re_extract_outline")
        assert False, "selected path must block re_extract"
    except ValueError as err:
        assert "选用" in str(err) or "创建向导" in str(err)

    reply, cards = execute_wizard_command(tmp_path, session, "go_study")
    assert "学" in reply
    assert any(c.type == "blueprint_wizard" for c in cards)
    title = next(c.title for c in cards if c.type == "blueprint_wizard")
    assert "创建蓝本" not in (title or "")
    assert is_selected_wizard_mode((session.freeform or {}).get("blueprint_wizard"))


def test_path_select_start_study_maps_to_go_study(tmp_path: Path):
    """选用蓝本后说「开始学习」应走 go_study，而不是指定章解析失败。"""
    from report_writing.chat.intent_dispatch import execute_dispatch_tool

    bid = "bp_sel_study_all"
    _ready_blueprint(tmp_path, bid, analyzed=False)
    session = ChatSession(id="path_sel_study", phase=ChatPhase.INTENT.value)
    _action_select_blueprint(tmp_path, session, {"blueprint_id": bid})

    hit = execute_dispatch_tool(
        tmp_path,
        session,
        {"tool": "re_study_chapters", "args": {}},
        "开始学习",
        emit=None,
        invoke=None,
    )
    assert hit is not None
    reply, cards, _ = hit
    assert "没认出" not in reply
    assert "学" in reply
    assert any(c.type == "blueprint_wizard" for c in cards)


def test_path_select_continue_study_maps_to_go_study(tmp_path: Path):
    from report_writing.chat.intent_dispatch import execute_dispatch_tool

    bid = "bp_sel_continue"
    _ready_blueprint(tmp_path, bid, analyzed=False)
    session = ChatSession(id="path_sel_cont", phase=ChatPhase.INTENT.value)
    _action_select_blueprint(tmp_path, session, {"blueprint_id": bid})

    hit = execute_dispatch_tool(
        tmp_path,
        session,
        {"tool": "re_study_chapters", "args": {}},
        "继续学习",
        emit=None,
        invoke=None,
    )
    assert hit is not None
    reply, cards, _ = hit
    assert "没认出" not in reply
    assert "学" in reply
    assert any(c.type == "blueprint_wizard" for c in cards)


def test_path_select_re_study_specific_chapter(tmp_path: Path):
    from report_writing.chat.intent_dispatch import execute_dispatch_tool

    bid = "bp_sel_restudy"
    _ready_blueprint(tmp_path, bid, analyzed=True)
    session = ChatSession(id="path_sel_rs", phase=ChatPhase.INTENT.value)
    _action_select_blueprint(tmp_path, session, {"blueprint_id": bid})

    hit = execute_dispatch_tool(
        tmp_path,
        session,
        {"tool": "re_study_chapters", "args": {"chapter_sequences": ["1"], "study_method": "sample"}},
        "重学第1章",
        emit=None,
        invoke=None,
    )
    assert hit is not None
    reply, cards, _ = hit
    assert "没认出" not in reply
    assert any(c.type == "blueprint_wizard" for c in cards)
    wiz = (session.freeform or {}).get("blueprint_wizard") or {}
    keys = list(wiz.get("trigger_chapter_study") or [])
    assert keys  # 指定章应触发学习


def test_path_select_wizard_route_does_not_open_create(tmp_path: Path):
    bid = "bp_sel_route"
    _ready_blueprint(tmp_path, bid, analyzed=True)
    # 两章都学完
    task = TaskStore(tmp_path / "tasks" / bid).load()
    task.outline.chapters[1].points = ["p2"]
    TaskStore(tmp_path / "tasks" / bid).save(task)

    session = ChatSession(id="path_sel3", phase=ChatPhase.READY_TO_WRITE.value)
    _action_select_blueprint(tmp_path, session, {"blueprint_id": bid})
    seed_selected_blueprint_wizard(tmp_path, session, bid)
    wizard = (session.freeform or {}).get("blueprint_wizard") or {}
    # 选用锁定路径：向导 mode=selected，禁止回到创建 meta 步
    assert is_selected_wizard_mode(wizard)
    assert wizard.get("mode") == WIZARD_MODE_SELECTED
    assert str(wizard.get("step") or "") != "meta"
