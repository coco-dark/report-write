"""handle_chat 入职三路 + 自由对话：无蓝本 / 有蓝本 / 依据模版创作。"""

from __future__ import annotations

from pathlib import Path

from report_writing.chat.agent_chain_log import session_invoked_tools
from report_writing.chat.session_state import (
    WRITING_PATH_BLUEPRINT,
    WRITING_PATH_FREEFORM,
    get_writing_path,
    user_chose_skip_blueprint,
)
from report_writing.chat import handle_chat
from report_writing.controller import ensure_task
from report_writing.domain.models import ChatPhase, ChatRequest, OutlineNode, OutlineTree
from report_writing.services.chat_session import load_or_create
from report_writing.services.robots import create_robot, get_robot, update_robot
from report_writing.services.store import TaskStore


def _invoke(_s: str, _u: str) -> str:
    return "{}"


def _chat(home: Path, **kwargs):
    return handle_chat(home, ChatRequest(**kwargs), invoke=_invoke)


def _session(home: Path, resp):
    return load_or_create(home, resp.session_id, robot_id=getattr(resp, "robot_id", "") or "")


def _card_types(resp) -> list[str]:
    return [str(getattr(c, "type", "") or "") for c in (resp.cards or [])]


def _clarify_option_ids(resp) -> list[str]:
    ids: list[str] = []
    for card in resp.cards or []:
        if str(getattr(card, "type", "") or "") != "intent_clarify":
            continue
        payload = dict(getattr(card, "payload", None) or {})
        for opt in payload.get("options") or []:
            if isinstance(opt, dict) and str(opt.get("id") or "").strip():
                ids.append(str(opt.get("id") or "").strip())
    return ids


def _click_option(home: Path, session_id: str, option_id: str, label: str, *, robot_id: str = ""):
    return _chat(
        home,
        session_id=session_id,
        message=label,
        action="resolve_intent_clarify",
        action_payload={"option_id": option_id, "option_label": label},
        robot_id=robot_id,
    )


def _seed_blueprint(home: Path, bid: str, title: str) -> None:
    path = home / "tasks" / bid
    ensure_task(path)
    task = TaskStore(path).load()
    task.title = title
    task.description = title
    task.outline = OutlineTree(
        chapters=[OutlineNode(key="c1", title="概况", points=["背景"], sequence="1")]
    )
    task.outline_confirmed = True
    task.sample_ready = True
    TaskStore(path).save(task)


def _bind_robot(home: Path, robot_id: str, *blueprint_ids: str) -> None:
    create_robot(home, {"id": robot_id, "name": "对话机器人"})
    robot = get_robot(home, robot_id)
    data = robot.to_dict()
    data["blueprint_ids"] = list(blueprint_ids)
    update_robot(home, robot_id, data)


def test_without_blueprint_click_then_auto_outline(tmp_path: Path):
    first = _chat(tmp_path, message="写一份工作汇报 自动生成目录")
    assert "ask_entry_path" in session_invoked_tools(_session(tmp_path, first))
    assert "intent_clarify" in _card_types(first)
    assert "with_blueprint" in _clarify_option_ids(first)
    assert "without_blueprint" in _clarify_option_ids(first)

    sid = first.session_id
    picked = _click_option(
        tmp_path, sid, "without_blueprint", "无蓝本 · 自己写"
    )
    session = _session(tmp_path, picked)
    tools = session_invoked_tools(session)
    assert "resolve_intent_clarify" in tools
    ids = _clarify_option_ids(picked)
    if "freeform" in ids:
        picked = _click_option(tmp_path, sid, "freeform", "自动生成大纲")
        session = _session(tmp_path, picked)
        tools = session_invoked_tools(session)
    assert user_chose_skip_blueprint(session) or get_writing_path(session) == WRITING_PATH_FREEFORM
    assert "skip_blueprint" in tools or "submit_outline" in tools or "set_session_meta" in tools
    assert "begin_blueprint_wizard" not in tools
    assert picked.phase in {
        ChatPhase.INTENT.value,
        ChatPhase.FREEFORM_ASK.value,
        ChatPhase.FREEFORM_OUTLINE.value,
        ChatPhase.FREEFORM_POINTS.value,
        ChatPhase.READY_TO_WRITE.value,
    }
    assert not any(t == "blueprint_wizard" for t in _card_types(picked))

    follow = _chat(tmp_path, session_id=sid, message="第一章写概况，第二章写进展")
    session = _session(tmp_path, follow)
    assert get_writing_path(session) != WRITING_PATH_BLUEPRINT
    assert not any(t == "blueprint_list" for t in _card_types(follow))


def test_with_blueprint_click_matches_then_select(tmp_path: Path):
    _seed_blueprint(tmp_path, "bp_exist", "工作汇报蓝本")
    _bind_robot(tmp_path, "r_path", "bp_exist")
    first = _chat(tmp_path, message="写一份工作汇报", robot_id="r_path")
    assert "intent_clarify" in _card_types(first) or "ask_entry_path" in session_invoked_tools(
        _session(tmp_path, first)
    )
    sid = first.session_id
    picked = _click_option(
        tmp_path,
        sid,
        "with_blueprint",
        "有蓝本 · 选用已有",
        robot_id="r_path",
    )
    session = _session(tmp_path, picked)
    tools = session_invoked_tools(session)
    assert "resolve_intent_clarify" in tools
    assert "match_blueprints" in tools or "select_blueprint" in tools or "list_blueprints" in tools
    assert not user_chose_skip_blueprint(session)
    types = _card_types(picked)
    assert (
        "blueprint_list" in types
        or "blueprint_wizard" in types
        or session.blueprint_id == "bp_exist"
        or "select_blueprint" in tools
    )

    if session.blueprint_id != "bp_exist":
        selected = _chat(
            tmp_path,
            session_id=sid,
            robot_id="r_path",
            action="select_blueprint",
            action_payload={"blueprint_id": "bp_exist"},
            message="",
        )
        session = _session(tmp_path, selected)
        assert "select_blueprint" in session_invoked_tools(session)
    assert session.blueprint_id == "bp_exist" or get_writing_path(session) == WRITING_PATH_BLUEPRINT


def test_template_creation_from_freeform_mode_card(tmp_path: Path):
    first = _chat(tmp_path, message="写一份环境报告")
    sid = first.session_id
    assert "without_blueprint" in _clarify_option_ids(first)
    branch = _click_option(tmp_path, sid, "without_blueprint", "无蓝本 · 自己写")
    ids = _clarify_option_ids(branch)
    if "template" in ids:
        picked = _click_option(tmp_path, sid, "template", "依据模版编写（上传提取）")
    else:
        picked = _chat(
            tmp_path,
            session_id=sid,
            message="依据模版编写",
            action="begin_blueprint_wizard",
            action_payload={"force_new": True, "explicit_create": True},
        )
    session = _session(tmp_path, picked)
    tools = session_invoked_tools(session)
    assert "begin_blueprint_wizard" in tools or "blueprint_wizard" in tools or "blueprint_subagent" in tools
    types = _card_types(picked)
    assert "blueprint_wizard" in types or session.phase == ChatPhase.CREATE_HANDOFF.value
    assert not user_chose_skip_blueprint(session)
    wiz = dict((session.freeform or {}).get("blueprint_wizard") or {})
    assert wiz.get("step") in {"meta", "upload", "outline", "study", ""} or "blueprint_wizard" in types


def test_oral_template_phrase_starts_create_path(tmp_path: Path):
    """口语「依据模版写一份…」应走创建向导，而不是 skip / 代选库蓝本。"""
    resp = _chat(tmp_path, message="依据模版写一份环境报告")
    session = _session(tmp_path, resp)
    tools = session_invoked_tools(session)
    types = _card_types(resp)
    assert "skip_blueprint" not in tools
    assert "select_blueprint" not in tools
    assert not user_chose_skip_blueprint(session)
    assert (
        "begin_blueprint_wizard" in tools
        or "blueprint_wizard" in types
        or "ask_entry_path" in tools
        or "intent_clarify" in types
    )
    if "intent_clarify" in types:
        ids = _clarify_option_ids(resp)
        assert "with_blueprint" in ids or "template" in ids or "without_blueprint" in ids


def test_free_dialogue_greeting_does_not_lock_path(tmp_path: Path):
    hello = _chat(tmp_path, message="你好，先聊两句")
    session = _session(tmp_path, hello)
    tools = session_invoked_tools(session)
    assert "skip_blueprint" not in tools
    assert "match_blueprints" not in tools
    assert "begin_blueprint_wizard" not in tools
    assert "select_blueprint" not in tools
    assert not user_chose_skip_blueprint(session)
    assert get_writing_path(session) == ""
    types = _card_types(hello)
    assert "blueprint_wizard" not in types
    assert "blueprint_list" not in types
    assert hello.phase == ChatPhase.INTENT.value
    assert "收到" in (hello.reply or "") or "嗨" in (hello.reply or "") or (hello.reply or "").strip()

    write = _chat(tmp_path, session_id=hello.session_id, message="写一份工作汇报 自动生成目录")
    session = _session(tmp_path, write)
    assert "ask_entry_path" in session_invoked_tools(session)
    assert "intent_clarify" in _card_types(write)
    assert "with_blueprint" in _clarify_option_ids(write)
    assert "without_blueprint" in _clarify_option_ids(write)
