"""入职三路 + 自由对话的 handle_chat 全链路：定名 → 选路 → 目录/向导 → 开写。

无现成对话模版时使用 examples/simple-report-template.json 与 simple-report-sample.md。
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from report_writing.chat.agent_chain_log import session_invoked_tools
from report_writing.chat.intent_router import (
    WRITING_PATH_BLUEPRINT,
    get_writing_path,
    user_chose_skip_blueprint,
)
from report_writing.chat_orchestrator import handle_chat
from report_writing.controller import confirm_outline, ensure_task
from report_writing.domain.models import ChatPhase, ChatRequest, OutlineNode, OutlineTree
from report_writing.services.chat_session import load_or_create
from report_writing.services.corpus import load_template
from report_writing.services.robots import create_robot, get_robot, update_robot
from report_writing.services.store import TaskStore

ROOT = Path(__file__).resolve().parents[1]
SIMPLE_TEMPLATE = ROOT / "examples" / "simple-report-template.json"
SIMPLE_SAMPLE = ROOT / "examples" / "simple-report-sample.md"
OUTLINE_TEXT = "1 概况\n2 工作进展"


def _invoke(system: str, user: str) -> str:
    blob = f"{system}\n{user}"
    if any(k in blob for k in ("要点", "points", "写作信息")):
        return json.dumps(
            [
                {
                    "key": "overview",
                    "points": ["汇报目的"],
                    "describes": "概况",
                    "keywords": ["概况"],
                },
                {
                    "key": "progress",
                    "points": ["已完成事项"],
                    "describes": "工作进展",
                    "keywords": ["进展"],
                },
            ],
            ensure_ascii=False,
        )
    if any(k in blob for k in ("目录", "大纲", "章节")):
        return OUTLINE_TEXT
    return "{}"


def _chat(home: Path, **kwargs):
    return handle_chat(home, ChatRequest(**kwargs), invoke=_invoke)


def _session(home: Path, resp):
    return load_or_create(home, resp.session_id, robot_id=getattr(resp, "robot_id", "") or "")


def _card_types(resp) -> list[str]:
    return [str(getattr(c, "type", "") or "") for c in (resp.cards or [])]


def _clarify_options(resp) -> list[dict]:
    rows: list[dict] = []
    for card in resp.cards or []:
        if str(getattr(card, "type", "") or "") != "intent_clarify":
            continue
        payload = dict(getattr(card, "payload", None) or {})
        for opt in payload.get("options") or []:
            if isinstance(opt, dict) and str(opt.get("id") or "").strip():
                rows.append(opt)
    return rows


def _option_ids(resp) -> list[str]:
    return [str(o.get("id") or "").strip() for o in _clarify_options(resp)]


def _click_option(home: Path, session_id: str, option_id: str, label: str = "", *, robot_id: str = ""):
    label = label or option_id
    return _chat(
        home,
        session_id=session_id,
        message=label,
        action="resolve_intent_clarify",
        action_payload={"option_id": option_id, "option_label": label},
        robot_id=robot_id,
    )


def _click_if_present(home: Path, resp, option_id: str, *, robot_id: str = ""):
    opts = {str(o.get("id") or ""): o for o in _clarify_options(resp)}
    if option_id not in opts:
        return resp
    label = str(opts[option_id].get("label") or option_id)
    return _click_option(home, resp.session_id, option_id, label, robot_id=robot_id)


def _action(home: Path, session_id: str, action: str, payload: dict | None = None, *, message: str = "", robot_id: str = ""):
    return _chat(
        home,
        session_id=session_id,
        action=action,
        action_payload=dict(payload or {}),
        message=message,
        robot_id=robot_id,
    )


def _skip_open_gates(home: Path, resp, *, robot_id: str = ""):
    """资料包 / 待补要素卡：点跳过，避免挡住开写。"""
    sid = resp.session_id
    types = set(_card_types(resp))
    if "materials_pack_gate" in types:
        resp = _action(home, sid, "materials_pack_skip", robot_id=robot_id)
        types = set(_card_types(resp))
    if "writing_supplement_check" in types:
        resp = _action(home, sid, "skip_writing_supplement_check", robot_id=robot_id)
    return resp


def _outline_from_simple_template() -> OutlineTree:
    tmpl = load_template(SIMPLE_TEMPLATE)
    chapters = [
        OutlineNode(
            key=ch.key,
            title=ch.title,
            sequence=ch.sequence or str(idx),
            level=ch.level,
            parent=ch.parent,
            points=list(ch.points),
            keywords=list(getattr(ch, "keywords", None) or []),
        )
        for idx, ch in enumerate(tmpl.chapters, start=1)
    ]
    return OutlineTree(chapters=chapters)


def _seed_blueprint_from_simple_template(home: Path, bid: str) -> None:
    tmpl = load_template(SIMPLE_TEMPLATE)
    path = home / "tasks" / bid
    ensure_task(path)
    sample_dest = path / "sample.md"
    shutil.copyfile(SIMPLE_SAMPLE, sample_dest)
    store = TaskStore(path)
    task = store.load()
    task.title = tmpl.title
    task.description = tmpl.title
    task.template_path = str(SIMPLE_TEMPLATE)
    task.sample_path = str(sample_dest)
    task.sample_name = SIMPLE_SAMPLE.name
    task.sample_ready = True
    task.outline = _outline_from_simple_template()
    task.outline_confirmed = True
    store.save(task)


def _bind_robot(home: Path, robot_id: str, *blueprint_ids: str) -> None:
    create_robot(home, {"id": robot_id, "name": "全链路机器人"})
    robot = get_robot(home, robot_id)
    data = robot.to_dict()
    data["blueprint_ids"] = list(blueprint_ids)
    update_robot(home, robot_id, data)


def _chapter_keys(home: Path, task_id: str) -> list[str]:
    task = TaskStore(home / "tasks" / task_id).load()
    return [
        str(n.key)
        for n in list(getattr(getattr(task, "outline", None), "chapters", None) or [])
        if str(getattr(n, "key", "") or "").strip()
    ]


def _confirm_doc_name(home: Path, resp, title: str):
    session = _session(home, resp)
    if (session.freeform or {}).get("writing_doc_named") or (session.freeform or {}).get(
        "meta_confirmed"
    ):
        return resp
    return _chat(home, session_id=resp.session_id, message=f"就叫{title}")


def _wrote_ok(resp, session) -> bool:
    return session.phase in {ChatPhase.WRITING.value, ChatPhase.CONFIRM_EXPORT.value} and bool(
        session.task_id
    )


def _finish_writing(home: Path, resp, *, task_id: str = "", robot_id: str = ""):
    session = _session(home, resp)
    tid = task_id or str(session.task_id or session.blueprint_id or "").strip()
    keys = _chapter_keys(home, tid) if tid else []
    payload = {
        "writing_pace": "batch",
        "chapter_keys": keys,
        "skip_supplement_check": True,
        "blueprint_id": tid,
    }
    # 依据模版 / 已有蓝本：优先写已分析章；无蓝本走 start_writing
    if session.blueprint_id or str((session.freeform or {}).get("blueprint_wizard") or {}):
        resp = _action(home, resp.session_id, "write_analyzed_only", payload, robot_id=robot_id)
        resp = _skip_open_gates(home, resp, robot_id=robot_id)
        session = _session(home, resp)
        if _wrote_ok(resp, session):
            return resp
    resp = _action(
        home,
        resp.session_id,
        "start_writing",
        payload,
        message="开始写",
        robot_id=robot_id,
    )
    resp = _skip_open_gates(home, resp, robot_id=robot_id)
    return resp


def test_simple_template_files_load():
    tmpl = load_template(SIMPLE_TEMPLATE)
    assert tmpl.id == "simple-work-report"
    assert [c.title for c in tmpl.chapters] == ["概况", "工作进展"]
    assert SIMPLE_SAMPLE.is_file()
    assert "1 概况" in SIMPLE_SAMPLE.read_text(encoding="utf-8")


def test_full_chain_without_blueprint(tmp_path: Path):
    resp = _chat(tmp_path, message="写一份工作汇报 自动生成目录")
    sid = resp.session_id
    resp = _click_if_present(tmp_path, resp, "without_blueprint")
    resp = _click_if_present(tmp_path, resp, "freeform")
    resp = _confirm_doc_name(tmp_path, resp, "工作汇报")
    session = _session(tmp_path, resp)
    assert user_chose_skip_blueprint(session) or get_writing_path(session) != WRITING_PATH_BLUEPRINT

    resp = _action(
        tmp_path,
        sid,
        "submit_outline",
        {"outline_text": OUTLINE_TEXT, "confirm": True},
        message=OUTLINE_TEXT,
    )
    session = _session(tmp_path, resp)
    assert (session.freeform or {}).get("outline_confirmed") or session.phase in {
        ChatPhase.FREEFORM_POINTS.value,
        ChatPhase.READY_TO_WRITE.value,
        ChatPhase.WRITING.value,
        ChatPhase.CONFIRM_EXPORT.value,
    }
    if session.phase == ChatPhase.FREEFORM_POINTS.value:
        resp = _action(tmp_path, sid, "skip_freeform_points", message="先跳过要点直接开写")
        session = _session(tmp_path, resp)

    tid = str(session.task_id or "").strip()
    assert tid
    resp = _finish_writing(tmp_path, resp, task_id=tid)
    session = _session(tmp_path, resp)
    assert _wrote_ok(resp, session)
    assert "progress" in _card_types(resp) or session.task_id
    task = TaskStore(tmp_path / "tasks" / session.task_id).load()
    titles = [str(n.title) for n in (task.outline.chapters if task.outline else [])]
    assert any("概况" in t or "进展" in t for t in titles)


def test_full_chain_with_blueprint(tmp_path: Path):
    _seed_blueprint_from_simple_template(tmp_path, "bp_simple")
    _bind_robot(tmp_path, "r_full", "bp_simple")
    resp = _chat(tmp_path, message="写一份工作汇报", robot_id="r_full")
    sid = resp.session_id
    resp = _click_if_present(tmp_path, resp, "use_suggested_name", robot_id="r_full")
    resp = _click_if_present(tmp_path, resp, "with_blueprint", robot_id="r_full")
    session = _session(tmp_path, resp)
    if session.blueprint_id != "bp_simple":
        resp = _action(
            tmp_path,
            sid,
            "select_blueprint",
            {"blueprint_id": "bp_simple"},
            robot_id="r_full",
        )
        session = _session(tmp_path, resp)
    assert session.blueprint_id == "bp_simple" or get_writing_path(session) == WRITING_PATH_BLUEPRINT
    assert "select_blueprint" in session_invoked_tools(session) or session.blueprint_id == "bp_simple"

    resp = _finish_writing(tmp_path, resp, task_id="bp_simple", robot_id="r_full")
    session = _session(tmp_path, resp)
    if not _wrote_ok(resp, session):
        resp = _finish_writing(tmp_path, resp, task_id="bp_simple", robot_id="r_full")
        session = _session(tmp_path, resp)
    assert _wrote_ok(resp, session)
    writing = TaskStore(tmp_path / "tasks" / session.task_id).load()
    assert writing.outline and writing.outline.chapters


def test_full_chain_template_wizard(tmp_path: Path):
    resp = _chat(tmp_path, message="写一份环境报告")
    sid = resp.session_id
    resp = _click_if_present(tmp_path, resp, "without_blueprint")
    assert "template" in _option_ids(resp)
    resp = _click_if_present(tmp_path, resp, "template")
    session = _session(tmp_path, resp)
    assert "blueprint_wizard" in _card_types(resp)

    resp = _action(
        tmp_path,
        sid,
        "blueprint_wizard",
        {"step": "meta", "title": "环境报告"},
        message="环境报告",
    )
    session = _session(tmp_path, resp)
    wiz = dict((session.freeform or {}).get("blueprint_wizard") or {})
    wid = str(wiz.get("working_task_id") or wiz.get("blueprint_id") or session.task_id or "").strip()
    assert wid
    path = tmp_path / "tasks" / wid
    ensure_task(path)
    sample_dest = path / "sample.md"
    shutil.copyfile(SIMPLE_SAMPLE, sample_dest)
    store = TaskStore(path)
    task = store.load()
    task.title = "环境报告"
    task.sample_path = str(sample_dest)
    task.sample_name = SIMPLE_SAMPLE.name
    task.sample_ready = True
    task.template_path = str(SIMPLE_TEMPLATE)
    task.outline = _outline_from_simple_template()
    store.save(task)

    resp = _action(
        tmp_path,
        sid,
        "blueprint_wizard",
        {"step": "upload_done", "blueprint_id": wid},
    )
    confirm_outline(path)
    session = _session(tmp_path, resp)
    resp = _finish_writing(tmp_path, resp, task_id=wid)
    session = _session(tmp_path, resp)
    assert _wrote_ok(resp, session)
    assert not user_chose_skip_blueprint(session)
    writing = TaskStore(tmp_path / "tasks" / (session.task_id or wid)).load()
    assert writing.outline and len(writing.outline.chapters) >= 2


def test_full_chain_free_dialogue_then_write(tmp_path: Path):
    hello = _chat(tmp_path, message="你好，先聊两句")
    session = _session(tmp_path, hello)
    assert "skip_blueprint" not in session_invoked_tools(session)
    assert get_writing_path(session) == ""
    assert hello.phase == ChatPhase.INTENT.value

    resp = _chat(tmp_path, session_id=hello.session_id, message="写一份工作汇报 自动生成目录")
    sid = resp.session_id
    resp = _click_if_present(tmp_path, resp, "without_blueprint")
    resp = _click_if_present(tmp_path, resp, "freeform")
    resp = _confirm_doc_name(tmp_path, resp, "工作汇报")
    resp = _action(
        tmp_path,
        sid,
        "submit_outline",
        {"outline_text": OUTLINE_TEXT, "confirm": True},
        message=OUTLINE_TEXT,
    )
    session = _session(tmp_path, resp)
    if session.phase == ChatPhase.FREEFORM_POINTS.value:
        resp = _action(tmp_path, sid, "skip_freeform_points")
        session = _session(tmp_path, resp)
    tid = str(session.task_id or "").strip()
    assert tid
    resp = _finish_writing(tmp_path, resp, task_id=tid)
    session = _session(tmp_path, resp)
    assert _wrote_ok(resp, session)
    follow = _chat(tmp_path, session_id=sid, message="第一章写得再简洁一点")
    session = _session(tmp_path, follow)
    assert session.phase in {
        ChatPhase.WRITING.value,
        ChatPhase.CONFIRM_EXPORT.value,
        ChatPhase.READY_TO_WRITE.value,
    }
