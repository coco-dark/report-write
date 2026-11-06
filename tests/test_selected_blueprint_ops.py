"""选用已有蓝本：对话确认/学习/开写，不走创建向导。"""

from __future__ import annotations

from pathlib import Path

from report_writing.chat.actions import _action_select_blueprint
from report_writing.chat.session_state import (
    WRITING_PATH_BLUEPRINT,
    active_blueprint_wizard,
    get_writing_path,
)
from report_writing.chat.resource_actions import execute_wizard_command
from report_writing.chat.wizard_state import (
    WIZARD_MODE_SELECTED,
    is_selected_wizard_mode,
    seed_selected_blueprint_wizard,
    sync_blueprint_wizard_session,
)
from report_writing.controller import ensure_task
from report_writing.domain.models import ChatSession, OutlineNode, OutlineTree
from report_writing.services.store import TaskStore


def _ready_blueprint(home: Path, bid: str, *, analyzed: bool = False) -> None:
    path = home / "tasks" / bid
    ensure_task(path)
    task = TaskStore(path).load()
    task.title = "选用测试蓝本"
    task.sample_ready = True
    task.outline = OutlineTree(
        chapters=[
            OutlineNode(key="c1", title="章一", sequence="1", points=["p1"] if analyzed else []),
            OutlineNode(key="c2", title="章二", sequence="2"),
        ]
    )
    task.outline_confirmed = True
    TaskStore(path).save(task)


def test_seed_selected_skips_create_steps(tmp_path: Path):
    bid = "bp_sel_seed"
    _ready_blueprint(tmp_path, bid)
    session = ChatSession(id="s_sel", blueprint_id=bid, freeform={})
    wizard = seed_selected_blueprint_wizard(tmp_path, session, bid)
    assert wizard.get("mode") == WIZARD_MODE_SELECTED
    assert wizard.get("step") in {"study", "outline_confirm"}
    assert wizard.get("step") not in {"meta", "upload", "outline"}


def test_select_blueprint_seeds_dialogue_ops(tmp_path: Path):
    bid = "bp_sel_action"
    _ready_blueprint(tmp_path, bid, analyzed=False)
    session = ChatSession(id="s_sel2", phase="intent", freeform={})
    reply, cards = _action_select_blueprint(tmp_path, session, {"blueprint_id": bid})
    assert get_writing_path(session) == WRITING_PATH_BLUEPRINT
    assert is_selected_wizard_mode((session.freeform or {}).get("blueprint_wizard"))
    assert "开始分析" in reply or "分析" in reply or "开始学习" in reply or "学习" in reply
    assert any(c.type == "blueprint_wizard" for c in cards)
    wiz_card = next(c for c in cards if c.type == "blueprint_wizard")
    assert wiz_card.payload.get("mode") == WIZARD_MODE_SELECTED
    assert "创建蓝本" not in (wiz_card.title or "")
    steps = wiz_card.payload.get("steps") or []
    assert not any(s.get("id") == "meta" for s in steps)
    assert not any(c.type == "analysis_gate" for c in cards)


def test_selected_mode_blocks_reextract(tmp_path: Path):
    bid = "bp_sel_block"
    _ready_blueprint(tmp_path, bid)
    session = ChatSession(id="s_sel3", blueprint_id=bid, freeform={})
    seed_selected_blueprint_wizard(tmp_path, session, bid)
    try:
        execute_wizard_command(tmp_path, session, "re_extract_outline")
        assert False, "expected ValueError"
    except ValueError as err:
        assert "选用" in str(err) or "创建向导" in str(err)


def test_selected_done_still_active_for_dialogue(tmp_path: Path):
    bid = "bp_sel_done"
    _ready_blueprint(tmp_path, bid, analyzed=True)
    # 两章都有要点 → done
    path = tmp_path / "tasks" / bid
    task = TaskStore(path).load()
    task.outline.chapters[1].points = ["p2"]
    TaskStore(path).save(task)

    session = ChatSession(id="s_sel4", blueprint_id=bid, freeform={})
    seed_selected_blueprint_wizard(tmp_path, session, bid)
    sync_blueprint_wizard_session(tmp_path, session, blueprint_id=bid)
    wiz = (session.freeform or {}).get("blueprint_wizard") or {}
    assert wiz.get("mode") == WIZARD_MODE_SELECTED
    assert wiz.get("step") == "study"
    assert wiz.get("study_mode") == "complete"
    assert active_blueprint_wizard(session) is not None
