"""对话总控 Agent：脚本模型走 Tool。"""

from __future__ import annotations

import json
from pathlib import Path

from report_writing.chat_orchestrator import _writing_chapter_keys, handle_chat
from report_writing.controller import ensure_task
from report_writing.domain.models import (
    ChapterState,
    ChatPhase,
    ChatRequest,
    OutlineNode,
    OutlineTree,
    WritingTask,
)
from report_writing.services.store import TaskStore


def _invoke(system: str, user: str) -> str:
    msg = ""
    if "用户本轮：" in user:
        msg = user.split("用户本轮：", 1)[-1].strip()
    elif "用户这句：" in user:
        msg = user.split("用户这句：", 1)[-1].strip()
    elif "用户说：" in user:
        msg = user.split("用户说：", 1)[-1].strip()
    elif "用户：" in user:
        msg = user.split("用户：", 1)[-1].strip()
    else:
        msg = user.strip()

    if "统一意图调度器" in system or "唯一大脑" in system:
        if "不用" in msg and "蓝本" in msg:
            return json.dumps(
                {"tools": [{"tool": "skip_blueprint", "args": {}}], "confidence": 0.92},
                ensure_ascii=False,
            )
        if "就用" in msg or "选用" in msg:
            return json.dumps(
                {"tools": [{"tool": "match_blueprints", "args": {}}], "confidence": 0.92},
                ensure_ascii=False,
            )
        if "创建" in msg and "蓝本" in msg:
            title = ""
            import re

            quoted = re.search(r"「([^」]+)」", msg)
            if quoted:
                title = quoted.group(1).strip()
            elif "名叫" in msg:
                title = msg.split("名叫", 1)[-1].strip()[:40]
            if title:
                args = {"step": "meta", "title": title}
                if "当前向导步骤：meta" in user:
                    args = {"command": "submit_meta", "title": title}
                return json.dumps(
                    {
                        "tools": [{"tool": "blueprint_wizard", "args": args}],
                        "confidence": 0.92,
                    },
                    ensure_ascii=False,
                )
            return json.dumps(
                {
                    "tools": [
                        {
                            "tool": "begin_blueprint_wizard",
                            "args": {"explicit_create": True},
                        }
                    ],
                    "confidence": 0.92,
                },
                ensure_ascii=False,
            )
        if "名叫" in msg:
            title = msg.split("名叫", 1)[-1].strip()[:40]
            return json.dumps(
                {
                    "tools": [
                        {
                            "tool": "blueprint_wizard",
                            "args": {"command": "submit_meta", "title": title},
                        }
                    ],
                    "confidence": 0.92,
                },
                ensure_ascii=False,
            )
        if "上传好了" in msg or "上传完成" in msg or "传好了" in msg:
            return json.dumps(
                {
                    "tools": [
                        {
                            "tool": "blueprint_wizard",
                            "args": {"command": "mark_upload_done"},
                        }
                    ],
                    "confidence": 0.92,
                },
                ensure_ascii=False,
            )
        if "重新提取" in msg or "再来一遍" in msg or "提取目录" in msg:
            return json.dumps(
                {
                    "tools": [
                        {
                            "tool": "blueprint_wizard",
                            "args": {"command": "re_extract_outline"},
                        }
                    ],
                    "confidence": 0.92,
                },
                ensure_ascii=False,
            )
        if "添加" in msg and "要素" in msg:
            return json.dumps(
                {"tools": [{"tool": "add_chapter_element", "args": {}}], "confidence": 0.92},
                ensure_ascii=False,
            )
        if "写一份" in msg or ("写报告" in msg and "章" not in msg):
            return json.dumps(
                {"tools": [{"tool": "match_blueprints", "args": {}}], "confidence": 0.9},
                ensure_ascii=False,
            )
        if "帮我写" in msg or (("写" in msg) and any(ch.isdigit() for ch in msg)):
            return json.dumps(
                {"tools": [{"tool": "select_write_chapters", "args": {}}], "confidence": 0.92},
                ensure_ascii=False,
            )
        if "学习" in msg and "章" in msg:
            return json.dumps(
                {"tools": [{"tool": "re_study_chapters", "args": {}}], "confidence": 0.9},
                ensure_ascii=False,
            )
        if "安全预评价" in msg:
            return json.dumps(
                {"tools": [{"tool": "match_blueprints", "args": {}}], "confidence": 0.9},
                ensure_ascii=False,
            )
        return json.dumps({"tools": [], "confidence": 0.2}, ensure_ascii=False)
    if "无蓝本写作流程的动作分类器" in system:
        if "跳过" in msg or "直接开写" in msg:
            return json.dumps({"intent": "skip_points", "confidence": 0.92}, ensure_ascii=False)
        if "生成" in msg and "要点" in msg:
            return json.dumps({"intent": "generate_points", "confidence": 0.92}, ensure_ascii=False)
        if "提取目录" in msg or "生成目录" in msg:
            return json.dumps({"intent": "generate_outline", "confidence": 0.92}, ensure_ascii=False)
    if "入口意图" in system:
        if "不用" in msg and "蓝本" in msg:
            return json.dumps(
                {"intent": "skip_blueprint", "confidence": 0.92, "is_vague_write": False},
                ensure_ascii=False,
            )
        if "就用" in msg or "选用" in msg:
            return json.dumps(
                {
                    "intent": "explicit_select",
                    "confidence": 0.92,
                    "agent_may_select": True,
                    "is_vague_write": False,
                },
                ensure_ascii=False,
            )
        if "创建" in msg and "蓝本" in msg:
            title = ""
            if "名叫" in msg:
                title = msg.split("名叫", 1)[-1].strip()[:40]
            return json.dumps(
                {"intent": "create_blueprint", "confidence": 0.92, "blueprint_title": title},
                ensure_ascii=False,
            )
        if "样" in msg and ("本" in msg or "板" in msg):
            title = ""
            if "安全预评价" in msg:
                title = "安全预评价报告"
            return json.dumps(
                {"intent": "create_blueprint", "confidence": 0.92, "blueprint_title": title},
                ensure_ascii=False,
            )
        if "安全预评价" in msg or "写一份" in msg:
            return json.dumps(
                {
                    "intent": "match_blueprints",
                    "confidence": 0.9,
                    "is_vague_write": True,
                    "agent_may_select": False,
                },
                ensure_ascii=False,
            )
    if "候选" in system:
        if "就用" in msg or "选用" in msg:
            return json.dumps({"blueprint_id": "bp_only", "confidence": 0.92}, ensure_ascii=False)
        return json.dumps({"blueprint_id": "", "confidence": 0.2}, ensure_ascii=False)
    if "向导的意图分类器" in system:
        if "重新学习" in msg or ("到" in msg and "章" in msg):
            seqs = []
            if "1到2" in msg or "1-2" in msg:
                seqs = ["1", "2"]
            return json.dumps(
                {"command": "re_study_chapters", "confidence": 0.92, "chapter_sequences": seqs},
                ensure_ascii=False,
            )
        if "提取目录" in msg or "重新提取" in msg or "再来一遍" in msg:
            return json.dumps({"command": "re_extract_outline", "confidence": 0.92}, ensure_ascii=False)
        if ("开始学习" in msg or "开始学" in msg) and ("目录" in msg or "这个" in msg or "确认" in msg):
            return json.dumps(
                {"commands": ["confirm_outline", "go_study"], "confidence": 0.92},
                ensure_ascii=False,
            )
        if "确认目录" in msg and ("学习" in msg or "开始" in msg):
            return json.dumps(
                {"commands": ["confirm_outline", "go_study"], "confidence": 0.92},
                ensure_ascii=False,
            )
        if (
            "上传好了" in msg
            or "上传完成" in msg
            or "传好了" in msg
            or ("上传" in msg and "好了" in msg)
        ):
            return json.dumps({"command": "mark_upload_done", "confidence": 0.92}, ensure_ascii=False)
        if "不用" in msg and "蓝本" in msg:
            return json.dumps({"command": "abandon_wizard", "confidence": 0.92}, ensure_ascii=False)
        if "名叫" in msg or "叫" in msg:
            title = msg.split("名叫", 1)[-1].strip() if "名叫" in msg else msg[:40]
            return json.dumps(
                {"command": "submit_meta", "title": title, "confidence": 0.92},
                ensure_ascii=False,
            )
        if "「" in msg and "」" in msg and ("创建" in msg or "蓝本" in msg):
            import re

            match = re.search(r"「([^」]+)」", msg)
            if match:
                return json.dumps(
                    {"command": "submit_meta", "title": match.group(1).strip(), "confidence": 0.92},
                    ensure_ascii=False,
                )
    return "{}"


def _bind_robot_blueprints(home: Path, robot_id: str, *blueprint_ids: str) -> None:
    from report_writing.services.robots import get_robot, update_robot

    robot = get_robot(home, robot_id)
    assert robot is not None
    data = robot.to_dict()
    data["blueprint_ids"] = list(blueprint_ids)
    update_robot(home, robot_id, data)


def test_step_mode_progress_shows_focus_chapter_only():
    from report_writing.chat_orchestrator import WRITING_PACE_STEP, _visible_progress_chapters

    rows = [
        {"key": "c1", "status": "awaiting_confirm", "has_draft": True, "steps": []},
        *[{"key": f"c{i}", "status": "pending", "has_draft": False, "steps": []} for i in range(2, 112)],
    ]
    visible, collapsed = _visible_progress_chapters(rows, pace=WRITING_PACE_STEP)
    assert [r["key"] for r in visible] == ["c1", "c2"]
    assert visible[1].get("is_next") is True
    assert collapsed == 109


def test_batch_mode_progress_lists_all_chapters():
    from report_writing.chat_orchestrator import WRITING_PACE_BATCH, _visible_progress_chapters

    rows = [
        {"key": "c1", "status": "awaiting_confirm", "has_draft": True, "steps": []},
        {"key": "c2", "status": "pending", "has_draft": False, "steps": []},
    ]
    visible, collapsed = _visible_progress_chapters(rows, pace=WRITING_PACE_BATCH)
    assert len(visible) == 2
    assert collapsed == 0


def test_filter_outline_keeps_ancestors_only():
    from report_writing.chat_orchestrator import _filter_outline_by_selected_keys

    nodes = [
        OutlineNode(key="p", sequence="1", title="父", structure_only=True),
        OutlineNode(key="c1", sequence="1.1", title="子1", parent="p", points=["p"], keywords=["k"]),
        OutlineNode(key="c2", sequence="1.2", title="子2", parent="p", points=["p"], keywords=["k"]),
        OutlineNode(key="q", sequence="2", title="其他", structure_only=True),
        OutlineNode(key="c3", sequence="2.1", title="子3", parent="q", points=["p"], keywords=["k"]),
    ]
    kept = _filter_outline_by_selected_keys(nodes, ["c1"])
    assert [n.key for n in kept] == ["p", "c1"]


def test_scripted_chat_does_not_start_writing_on_smalltalk(tmp_path: Path):
    resp = handle_chat(tmp_path, ChatRequest(message="今天天气怎么样"), invoke=_invoke)
    assert resp.phase == ChatPhase.INTENT.value
    assert not any(c.type == "start_writing" for c in resp.cards)


def test_no_match_does_not_list_all_blueprints(tmp_path: Path):
    from report_writing.services.robots import create_robot

    create_robot(tmp_path, {"id": "r_report", "name": "报告机器人"})
    # 库里有蓝本，但与「航天器说明书」无关 → 不应兜底罗列
    path = tmp_path / "tasks" / "bp_noise"
    ensure_task(path)
    task = TaskStore(path).load()
    task.title = "安评蓝本"
    task.description = "安全预评价报告"
    task.outline = OutlineTree(
        chapters=[OutlineNode(key="c1", title="概况", points=["p"], sequence="1")]
    )
    task.outline_confirmed = True
    TaskStore(path).save(task)

    def invoke(system: str, user: str) -> str:
        if "候选蓝本" in user or "蓝本选型" in system or "挑蓝本" in system:
            return '{"reply":"没有对口蓝本","ids":[]}'
        return "{}"

    resp = handle_chat(
        tmp_path,
        ChatRequest(message="写一份航天器操作说明书", robot_id="r_report"),
        invoke=invoke,
    )
    assert not any(c.type == "blueprint_list" for c in resp.cards)
    assert resp.phase in {
        ChatPhase.INTENT.value,
        ChatPhase.FREEFORM_ASK.value,
        ChatPhase.FREEFORM_OUTLINE.value,
        ChatPhase.BLUEPRINT_SELECT.value,
    }
    if resp.phase == ChatPhase.INTENT.value:
        assert any(c.type == "intent_clarify" for c in resp.cards)


def test_match_does_not_auto_bind_without_explicit_select(tmp_path: Path):
    from report_writing.chat.session_documents import confirm_writing_document_name
    from report_writing.controller import ensure_task
    from report_writing.domain.models import OutlineNode, OutlineTree
    from report_writing.services.chat_session import get_session, save_session
    from report_writing.services.robots import create_robot
    from report_writing.services.store import TaskStore

    create_robot(tmp_path, {"id": "r_report", "name": "报告机器人"})
    _bind_robot_blueprints(tmp_path, "r_report", "bp_only")
    path = tmp_path / "tasks" / "bp_only"
    ensure_task(path)
    task = TaskStore(path).load()
    task.title = "唯一候选"
    task.description = "安全预评价"
    task.outline = OutlineTree(
        chapters=[OutlineNode(key="c1", title="概况", points=["p"], sequence="1")]
    )
    task.outline_confirmed = True
    TaskStore(path).save(task)

    # 入口先 ask_entry_path（定名）；本测验证 match 本身不自动绑定
    first = handle_chat(
        tmp_path,
        ChatRequest(message="写一份安全预评价报告", robot_id="r_report"),
        invoke=_invoke,
    )
    session = get_session(tmp_path, first.session_id)
    assert session is not None
    assert not (session.blueprint_id or "").strip()
    confirm_writing_document_name(session, "安全预评价报告", message="写一份安全预评价报告")
    from report_writing.chat.intent_clarify import clear_pending

    clear_pending(session)
    save_session(tmp_path, session)

    resp = handle_chat(
        tmp_path,
        ChatRequest(
            session_id=session.id,
            message="写一份安全预评价报告",
            action="match_blueprints",
            robot_id="r_report",
        ),
        invoke=_invoke,
    )
    session = get_session(tmp_path, resp.session_id)
    assert session is not None
    assert not (session.blueprint_id or "").strip()
    assert any(c.type == "blueprint_list" for c in resp.cards)


def test_explicit_select_can_bind_matched_blueprint(tmp_path: Path):
    from report_writing.chat.intent_clarify import clear_pending
    from report_writing.chat.session_documents import confirm_writing_document_name
    from report_writing.controller import ensure_task
    from report_writing.domain.models import OutlineNode, OutlineTree
    from report_writing.services.chat_session import get_session, save_session
    from report_writing.services.robots import create_robot
    from report_writing.services.store import TaskStore

    create_robot(tmp_path, {"id": "r_report", "name": "报告机器人"})
    _bind_robot_blueprints(tmp_path, "r_report", "bp_only")
    path = tmp_path / "tasks" / "bp_only"
    ensure_task(path)
    task = TaskStore(path).load()
    task.title = "唯一候选"
    task.description = "安全预评价"
    task.outline = OutlineTree(
        chapters=[OutlineNode(key="c1", title="概况", points=["p"], sequence="1")]
    )
    task.outline_confirmed = True
    TaskStore(path).save(task)

    first = handle_chat(
        tmp_path,
        ChatRequest(message="就用这份蓝本写安全预评价", robot_id="r_report"),
        invoke=_invoke,
    )
    session = get_session(tmp_path, first.session_id)
    assert session is not None
    confirm_writing_document_name(session, "安全预评价", message="就用这份蓝本写安全预评价")
    clear_pending(session)
    save_session(tmp_path, session)

    resp = handle_chat(
        tmp_path,
        ChatRequest(
            session_id=session.id,
            message="就用这份蓝本写安全预评价",
            action="match_blueprints",
            robot_id="r_report",
        ),
        invoke=_invoke,
    )
    session = get_session(tmp_path, resp.session_id)
    assert session is not None
    # 显式「就用」允许自动订上唯一对口候选
    assert (session.blueprint_id or "").strip() == "bp_only"


def test_wizard_natural_language_submit_meta_title(tmp_path: Path):
    """向导 meta 步填名称 → upload（不经全链路 handle_chat，避免入口定名干扰）。"""
    from report_writing.chat.resource_actions import _action_blueprint_wizard
    from report_writing.domain.models import ChatSession

    (tmp_path / "tasks").mkdir(parents=True)
    session = ChatSession(
        id="wiz_meta_title",
        phase=ChatPhase.CREATE_HANDOFF.value,
        freeform={
            "writing_doc_named": True,
            "meta_confirmed": True,
            "title": "安全预评价模板",
            "blueprint_wizard": {"step": "meta", "title": "", "mode": "create"},
        },
    )
    reply, cards = _action_blueprint_wizard(
        tmp_path,
        session,
        {"step": "meta", "title": "安全预评价模板"},
    )
    assert "安全预评价模板" in reply or "上传" in reply
    assert any(c.type == "blueprint_wizard" for c in cards)
    assert (cards[0].payload.get("step") or "") == "upload"


def test_wizard_abandon_does_not_keep_showing_wizard(tmp_path: Path):
    from report_writing.services.chat_session import get_session, save_session

    resp = handle_chat(tmp_path, ChatRequest(message="我想在对话里创建蓝本"), invoke=_invoke)
    session = get_session(tmp_path, resp.session_id)
    assert session is not None
    session.freeform = {
        **(session.freeform or {}),
        "blueprint_wizard": {"step": "outline_confirm", "blueprint_id": "bp_wiz", "title": "安全报告"},
    }
    session.blueprint_id = "bp_wiz"
    session.phase = ChatPhase.CREATE_HANDOFF.value
    save_session(tmp_path, session)

    again = handle_chat(
        tmp_path,
        ChatRequest(session_id=session.id, message="不用这个蓝本"),
        invoke=_invoke,
    )
    session = get_session(tmp_path, again.session_id)
    assert session is not None
    assert not (session.blueprint_id or "").strip()
    assert not (session.freeform or {}).get("blueprint_wizard")
    assert not any(c.type == "blueprint_wizard" for c in again.cards)
    assert (
        "不用" in again.reply
        or "先不用" in again.reply
        or "对口" in again.reply
        or "怎么开始" in again.reply
        or "跳过" in again.reply
    )


def test_wizard_create_quoted_title_after_meta_wizard(tmp_path: Path):
    """已有 meta 向导 + 填名称 → upload，且 blueprint_id 落地。"""
    from report_writing.chat.resource_actions import _action_blueprint_wizard
    from report_writing.domain.models import ChatSession

    (tmp_path / "tasks").mkdir(parents=True)
    session = ChatSession(
        id="wiz_quoted_title",
        phase=ChatPhase.CREATE_HANDOFF.value,
        freeform={
            "writing_doc_named": True,
            "meta_confirmed": True,
            "title": "工程安全预评价",
            "blueprint_wizard": {"step": "meta", "title": "", "mode": "create"},
        },
    )
    reply, cards = _action_blueprint_wizard(
        tmp_path,
        session,
        {"step": "meta", "title": "工程安全预评价"},
    )
    assert "工程安全预评价" in reply or "上传" in reply
    wiz_cards = [c for c in cards if c.type == "blueprint_wizard"]
    assert wiz_cards, "expected blueprint_wizard card"
    assert wiz_cards[0].payload.get("step") == "upload"
    bid = str(wiz_cards[0].payload.get("blueprint_id") or "")
    assert bid.startswith("bp") or bid.startswith("w")
    wiz = (session.freeform or {}).get("blueprint_wizard") or {}
    assert wiz.get("step") == "upload"
    assert str(session.blueprint_id or wiz.get("blueprint_id") or bid)


def test_wizard_upload_done_ack(tmp_path: Path):
    from report_writing.services.chat_session import get_session, save_session

    resp = handle_chat(tmp_path, ChatRequest(message="创建蓝本"), invoke=_invoke)
    session = get_session(tmp_path, resp.session_id)
    session.freeform = {
        **(session.freeform or {}),
        "blueprint_wizard": {"step": "upload", "blueprint_id": "bp_up", "title": "T"},
    }
    session.phase = ChatPhase.CREATE_HANDOFF.value
    save_session(tmp_path, session)

    again = handle_chat(
        tmp_path,
        ChatRequest(session_id=session.id, message="样板上传好了"),
        invoke=_invoke,
    )
    assert any(c.type == "blueprint_wizard" for c in again.cards)
    assert again.cards[0].payload.get("step") == "outline"


def test_wizard_colloquial_extract_outline(tmp_path: Path):
    from report_writing.services.chat_session import get_session, save_session

    resp = handle_chat(tmp_path, ChatRequest(message="我想在对话里创建蓝本"), invoke=_invoke)
    session = get_session(tmp_path, resp.session_id)
    assert session is not None
    session.freeform = {
        **(session.freeform or {}),
        "blueprint_wizard": {"step": "outline", "blueprint_id": "bp_wiz", "title": "测试"},
    }
    session.blueprint_id = "bp_wiz"
    session.phase = ChatPhase.CREATE_HANDOFF.value
    save_session(tmp_path, session)

    again = handle_chat(
        tmp_path,
        ChatRequest(session_id=session.id, message="提取目录吧"),
        invoke=_invoke,
    )
    assert any(c.type == "blueprint_wizard" for c in again.cards)
    assert again.cards[0].payload.get("trigger_outline_extract") is True


def test_wizard_natural_language_re_extract_outline(tmp_path: Path):
    from report_writing.services.chat_session import get_session

    resp = handle_chat(tmp_path, ChatRequest(message="我想在对话里创建蓝本"), invoke=_invoke)
    session = get_session(tmp_path, resp.session_id)
    assert session is not None
    session.freeform = {
        **(session.freeform or {}),
        "blueprint_wizard": {"step": "outline_confirm", "blueprint_id": "bp_wiz", "title": "测试"},
    }
    session.blueprint_id = "bp_wiz"
    session.phase = ChatPhase.CREATE_HANDOFF.value
    from report_writing.services.chat_session import save_session

    save_session(tmp_path, session)

    again = handle_chat(
        tmp_path,
        ChatRequest(session_id=session.id, message="重新提取目录"),
        invoke=_invoke,
    )
    assert "重新提取" in again.reply or "提取目录" in again.reply
    assert any(c.type == "blueprint_wizard" for c in again.cards)
    assert again.cards[0].payload.get("step") == "outline"
    assert again.cards[0].payload.get("trigger_outline_extract") is True
    assert not any(c.type == "handoff" for c in again.cards)


def test_wizard_colloquial_re_extract_via_llm(tmp_path: Path):
    from report_writing.services.chat_session import get_session, save_session

    resp = handle_chat(tmp_path, ChatRequest(message="我想在对话里创建蓝本"), invoke=_invoke)
    session = get_session(tmp_path, resp.session_id)
    assert session is not None
    session.freeform = {
        **(session.freeform or {}),
        "blueprint_wizard": {
            "step": "outline_confirm",
            "blueprint_id": "bp_wiz",
            "title": "测试蓝本",
        },
    }
    session.blueprint_id = "bp_wiz"
    session.phase = ChatPhase.CREATE_HANDOFF.value
    save_session(tmp_path, session)

    def invoke(system: str, user: str) -> str:
        if "统一意图调度器" in system or "唯一大脑" in system:
            return json.dumps(
                {
                    "tools": [
                        {
                            "tool": "blueprint_wizard",
                            "args": {"command": "re_extract_outline"},
                        }
                    ],
                    "confidence": 0.91,
                },
                ensure_ascii=False,
            )
        if "意图分类器" in system or "wizard" in system.lower():
            return json.dumps({"command": "re_extract_outline", "confidence": 0.91}, ensure_ascii=False)
        return "{}"

    again = handle_chat(
        tmp_path,
        ChatRequest(session_id=session.id, message="目录不太对，按样板再来一遍"),
        invoke=invoke,
    )
    assert any(c.type == "blueprint_wizard" for c in again.cards)
    assert again.cards[0].payload.get("step") == "outline"
    assert not any(c.type == "handoff" for c in again.cards)


def test_create_blueprint_intent_opens_wizard(tmp_path: Path):
    from report_writing.services.chat_session import get_session

    resp = handle_chat(tmp_path, ChatRequest(message="我想在对话里创建蓝本"), invoke=_invoke)
    assert any(c.type == "blueprint_wizard" for c in resp.cards)
    session = get_session(tmp_path, resp.session_id)
    assert session is not None
    assert (session.freeform or {}).get("blueprint_wizard", {}).get("step") == "meta"


def test_writing_chapter_keys_follow_outline_order():
    task = WritingTask(
        id="w1",
        outline=OutlineTree(
            chapters=[
                OutlineNode(key="b", sequence="2", title="后"),
                OutlineNode(key="a", sequence="1", title="先"),
            ]
        ),
        chapters=[
            ChapterState(key="a", title="先"),
            ChapterState(key="b", title="后"),
            ChapterState(key="c", title="不在大纲"),
        ],
    )
    assert _writing_chapter_keys(task) == ["b", "a"]


def test_analysis_choice_cards_survive_handoff_overwrite(tmp_path: Path):
    """选蓝本后即使 Agent 再调 handoff，仍应保留「仅写已分析」。"""
    from report_writing.chat_orchestrator import _ensure_analysis_choice_cards
    from report_writing.domain.models import ChatCard

    path = tmp_path / "tasks" / "bp_keep"
    ensure_task(path)
    task = TaskStore(path).load()
    task.title = "保留选项"
    task.outline = OutlineTree(
        chapters=[
            OutlineNode(key="c1", sequence="1", title="已分析", points=["p"], keywords=["k"]),
            OutlineNode(key="c2", sequence="2", title="未分析"),
        ]
    )
    task.outline_confirmed = True
    TaskStore(path).save(task)

    selected = handle_chat(
        tmp_path,
        ChatRequest(action="select_blueprint", action_payload={"blueprint_id": "bp_keep"}),
        invoke=_invoke,
    )
    assert any(c.type == "doc_window_hint" for c in selected.cards)
    assert not any(c.type == "chapter_picker" for c in selected.cards)

    # 模拟只剩 handoff 时的兜底：改为文档小窗提示，不再塞目录树
    from report_writing.services.chat_session import get_session

    session = get_session(tmp_path, selected.session_id)
    assert session is not None
    restored = _ensure_analysis_choice_cards(
        tmp_path,
        session,
        [ChatCard(type="handoff", title="去蓝本分析配置", payload={"blueprint_id": "bp_keep"})],
    )
    assert any(c.type == "doc_window_hint" for c in restored)
    assert not any(c.type == "chapter_picker" for c in restored)
    assert not any(c.type == "handoff" for c in restored)


def test_scripted_select_partial_analysis_gate(tmp_path: Path):
    path = tmp_path / "tasks" / "bp_gate"
    ensure_task(path)
    task = TaskStore(path).load()
    task.title = "门闸蓝本"
    task.outline = OutlineTree(
        chapters=[
            OutlineNode(key="c1", sequence="1", title="已分析", points=["p"], keywords=["k"]),
            OutlineNode(key="c2", sequence="2", title="未分析"),
        ]
    )
    task.outline_confirmed = True
    TaskStore(path).save(task)

    selected = handle_chat(
        tmp_path,
        ChatRequest(action="select_blueprint", action_payload={"blueprint_id": "bp_gate"}),
        invoke=_invoke,
    )
    assert selected.phase == ChatPhase.CREATE_HANDOFF.value
    assert any(c.type == "doc_window_hint" for c in selected.cards)
    assert not any(c.type == "chapter_picker" for c in selected.cards)

    confirmed = handle_chat(
        tmp_path,
        ChatRequest(
            session_id=selected.session_id,
            action="select_write_chapters",
            action_payload={"blueprint_id": "bp_gate", "chapter_keys": ["c1"]},
        ),
        invoke=_invoke,
    )
    assert not any(c.type == "writing_pace" for c in confirmed.cards)


def test_select_subset_of_analyzed_chapters(tmp_path: Path):
    path = tmp_path / "tasks" / "bp_subset"
    ensure_task(path)
    task = TaskStore(path).load()
    task.title = "子集蓝本"
    task.outline = OutlineTree(
        chapters=[
            OutlineNode(key="c1", sequence="1", title="一", points=["p"], keywords=["k"]),
            OutlineNode(key="c2", sequence="2", title="二", points=["p"], keywords=["k"]),
            OutlineNode(key="c3", sequence="3", title="三", points=["p"], keywords=["k"]),
        ]
    )
    task.outline_confirmed = True
    TaskStore(path).save(task)

    selected = handle_chat(
        tmp_path,
        ChatRequest(action="select_blueprint", action_payload={"blueprint_id": "bp_subset"}),
        invoke=_invoke,
    )
    assert any(c.type == "doc_window_hint" for c in selected.cards)
    assert not any(c.type == "chapter_picker" for c in selected.cards)
    hint = next(c for c in selected.cards if c.type == "doc_window_hint")
    assert hint.payload.get("blueprint_id") == "bp_subset"
    assert hint.payload.get("auto_open") is True

    confirmed = handle_chat(
        tmp_path,
        ChatRequest(
            session_id=selected.session_id,
            action="select_write_chapters",
            action_payload={"blueprint_id": "bp_subset", "chapter_keys": ["c2"]},
        ),
        invoke=_invoke,
    )
    assert not any(c.type == "writing_pace" for c in confirmed.cards)
    from report_writing.services.chat_session import get_session as _get_session

    sess = _get_session(tmp_path, confirmed.session_id)
    assert sess is not None
    assert sess.freeform.get("write_chapter_keys") == ["c2"]


def test_select_write_chapters_starts_writing_without_pace_card(tmp_path: Path):
    """勾选章节后直接按批开写，不再弹出书写节奏卡。"""
    path = tmp_path / "tasks" / "bp_pace"
    ensure_task(path)
    task = TaskStore(path).load()
    task.title = "节奏蓝本"
    task.outline = OutlineTree(
        chapters=[
            OutlineNode(key="c1", sequence="1", title="章一", points=["p"], keywords=["k"]),
            OutlineNode(key="c2", sequence="2", title="章二", points=["p"], keywords=["k"]),
        ]
    )
    task.outline_confirmed = True
    TaskStore(path).save(task)

    selected = handle_chat(
        tmp_path,
        ChatRequest(action="select_blueprint", action_payload={"blueprint_id": "bp_pace"}),
        invoke=_invoke,
    )
    assert any(c.type == "doc_window_hint" for c in selected.cards)

    picked = handle_chat(
        tmp_path,
        ChatRequest(
            session_id=selected.session_id,
            action="select_write_chapters",
            action_payload={"blueprint_id": "bp_pace", "chapter_keys": ["c1", "c2"]},
        ),
        invoke=_invoke,
    )
    assert not any(c.type == "writing_pace" for c in picked.cards)
    assert picked.task_id
    assert any(c.type in {"progress", "export", "resource_choice"} for c in picked.cards)


def test_select_write_chapters_runs_batch(tmp_path: Path, monkeypatch):
    """勾选多章后走批写；run_chapters 收到全部所选章。"""
    from report_writing.domain.models import ChapterStatus

    path = tmp_path / "tasks" / "bp_step"
    ensure_task(path)
    task = TaskStore(path).load()
    task.title = "逐步蓝本"
    task.outline = OutlineTree(
        chapters=[
            OutlineNode(key="c1", sequence="1", title="章一", points=["p"], keywords=["k"]),
            OutlineNode(key="c2", sequence="2", title="章二", points=["p"], keywords=["k"]),
        ]
    )
    task.outline_confirmed = True
    TaskStore(path).save(task)

    ran: list[list[str]] = []

    def fake_run_chapters(task_path, *, chapter_keys=None, **kwargs):
        keys = list(chapter_keys or [])
        ran.append(keys)
        store = TaskStore(task_path)
        writing = store.load()
        for key in keys:
            ch = next((c for c in (writing.chapters or []) if c.key == key), None)
            if ch is None:
                ch = ChapterState(key=key, title=key)
                writing.chapters = list(writing.chapters or []) + [ch]
            ch.status = ChapterStatus.AWAITING_CONFIRM
            ch.official_text = f"正文-{key}"
        store.save(writing)
        return store.load()

    monkeypatch.setattr("report_writing.services.pipeline.run_chapters", fake_run_chapters)

    selected = handle_chat(
        tmp_path,
        ChatRequest(action="select_blueprint", action_payload={"blueprint_id": "bp_step"}),
        invoke=_invoke,
    )
    started = handle_chat(
        tmp_path,
        ChatRequest(
            session_id=selected.session_id,
            action="select_write_chapters",
            action_payload={"blueprint_id": "bp_step", "chapter_keys": ["c1", "c2"]},
        ),
        invoke=_invoke,
    )
    # 批写可能按章串行调用 run_chapters，但所选章都应写到
    flat = [k for call in ran for k in call]
    assert flat == ["c1", "c2"]
    assert started.task_id
    assert any(c.type == "progress" for c in started.cards)


def test_later_tool_keeps_blueprint_choice_cards():
    """同轮先列出蓝本、再改名，选蓝本卡不能被空卡片列表清掉。"""
    from report_writing.agents.chat_control import _set_cards
    from report_writing.agents.state import ChatControlState
    from report_writing.domain.models import ChatCard, ChatSession

    state = ChatControlState(home=Path("."), session=ChatSession(id="s"))
    _set_cards(
        state,
        [
            ChatCard(type="blueprint_list", title="点击选用蓝本", payload={"items": [{"id": "bp1"}]}),
            ChatCard(type="skip_blueprint", title="跳过蓝本", payload={}),
        ],
    )
    _set_cards(state, [])
    types = [c.type for c in state.cards]
    assert types == ["blueprint_list", "skip_blueprint"]
