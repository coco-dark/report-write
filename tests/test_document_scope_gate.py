"""同会话文档意图门闩：新开/续写/本章重写/裸重新写/入口硬拦。"""

from __future__ import annotations

import json
from pathlib import Path

from report_writing.agents.chat_control import build_chat_tools
from report_writing.agents.state import ChatControlState
from report_writing.chat.intent_clarify import (
    build_document_scope_clarify,
    execute_clarify_spec,
    get_pending,
)
from report_writing.chat.intent_turn_plan import TurnPlan, apply_turn_plan
from report_writing.chat.session_documents import (
    confirm_writing_document_name,
    focus_needs_entry_path,
    message_looks_like_chapter_revise,
    message_looks_like_weak_rewrite,
    resolve_document_scope,
    upsert_session_document,
)
from report_writing.domain.models import ChatPhase, ChatSession


def _named_session(title: str = "安全评价报告") -> ChatSession:
    session = ChatSession(id="scope_gate", phase=ChatPhase.FREEFORM_OUTLINE.value)
    confirm_writing_document_name(session, title, message=f"名字叫{title}")
    session.freeform = {
        **(session.freeform or {}),
        "writing_path": "freeform",
        "meta_confirmed": True,
        "writing_doc_named": True,
        "title": title,
        "freeform_mode": "auto_outline",
    }
    upsert_session_document(
        session,
        kind="outline",
        task_id="w_scope1",
        title=title,
        clear_blueprint_id=True,
    )
    session.task_id = "w_scope1"
    # 模拟入口已齐
    from report_writing.chat.session_documents import patch_focus_onboarding

    patch_focus_onboarding(
        session, name_confirmed=True, entry_path="freeform", preferred_entry_path="freeform"
    )
    return session


def test_obvious_diff_topic_asks_on_active_focus():
    session = _named_session("安全评价报告")
    scope = resolve_document_scope(
        session, "写一份lzb个人述职", invoke=None
    )
    assert scope.get("action") == "ask"
    assert scope.get("need_clarify") is True
    assert scope.get("force_new") is False


def test_continue_same_topic_not_force_new():
    session = _named_session("安全评价报告")
    scope = resolve_document_scope(session, "继续写", invoke=None)
    assert scope.get("action") == "continue"
    assert scope.get("force_new") is False
    assert scope.get("need_clarify") is False


def test_weak_rewrite_asks_not_continue():
    session = _named_session("安全评价报告")
    assert message_looks_like_weak_rewrite("重新写")
    scope = resolve_document_scope(session, "重新写", invoke=None)
    assert scope.get("action") == "ask"
    assert scope.get("need_clarify") is True


def test_chapter_revise_heuristic():
    assert message_looks_like_chapter_revise("重写第2章")
    assert message_looks_like_chapter_revise("这一章退回重写")
    session = _named_session("安全评价报告")
    scope = resolve_document_scope(session, "重写第2章", invoke=None)
    assert scope.get("action") == "chapter_revise"
    assert scope.get("need_clarify") is False


def test_weak_rewrite_clarify_has_rewrite_current_option():
    session = _named_session("安全评价报告")
    reply, cards = build_document_scope_clarify(session, message="重新写")
    assert "重写" in reply or "新开" in reply
    pending = get_pending(session) or {}
    ids = [str(o.get("id") or "") for o in (pending.get("options") or [])]
    assert "scope_rewrite_current" in ids
    assert "scope_force_new" in ids
    assert cards


def test_rewrite_current_option_ladder(tmp_path: Path):
    (tmp_path / "tasks").mkdir(parents=True)
    session = _named_session("安全评价报告")
    build_document_scope_clarify(session, message="重新写")
    hit = {
        "source": "document_scope",
        "option_id": "scope_rewrite_current",
        "slots": {"document_scope_action": "rewrite_current"},
        "candidate": {
            "kind": "document_scope",
            "name": "document_scope",
            "payload": {
                "message": "重新写",
                "title": "",
                "current_title": "安全评价报告",
            },
        },
    }
    reply, cards, _ = execute_clarify_spec(
        tmp_path, session, hit, message="清空重写当前这份", invoke=None
    )
    assert "重写" in (reply or "")
    assert (session.freeform or {}).get("rewrite_current_pending") is True
    assert (get_pending(session) or {}).get("source") != "document_scope" or cards


def test_force_new_hangs_entry_path(tmp_path: Path):
    (tmp_path / "tasks").mkdir(parents=True)
    session = _named_session("安全评价报告")
    plan = TurnPlan(
        confidence=0.92,
        uncertain=False,
        slots={"doc_name": "lzb个人述职"},
        tools=[{"tool": "start_writing", "args": {}}],
        missing=[],
    )
    hit = apply_turn_plan(
        tmp_path,
        session,
        "写一份lzb个人述职",
        invoke=None,
        plan=plan,
    )
    assert hit is not None
    reply, cards, events = hit
    assert "新开" in (reply or "")
    assert str((session.freeform or {}).get("title") or "") == "安全评价报告"
    pending = get_pending(session) or {}
    assert pending.get("source") == "document_scope" or "新开" in (reply or "")
    assert not any(
        e.get("type") == "turn_plan" and "start_writing" in (e.get("tools") or [])
        for e in (events or [])
        if isinstance(e, dict) and e.get("type") == "turn_plan" and e.get("tools")
    )


def test_chapter_revise_blocks_start_writing_in_plan(tmp_path: Path):
    (tmp_path / "tasks").mkdir(parents=True)
    session = _named_session("安全评价报告")
    plan = TurnPlan(
        confidence=0.9,
        uncertain=False,
        slots={},
        tools=[{"tool": "start_writing", "args": {}}],
        missing=[],
    )
    hit = apply_turn_plan(
        tmp_path, session, "重写第2章", invoke=None, plan=plan
    )
    assert hit is not None
    reply, _cards, events = hit
    assert "章" in (reply or "")
    assert any(
        "chapter_key" in (e.get("missing") or [])
        for e in (events or [])
        if isinstance(e, dict)
    )


def test_llm_high_confidence_force_new_promotes_from_ask():
    session = _named_session("述职报告")

    def invoke(system: str, user: str) -> str:
        assert "文档作用域" in system
        return json.dumps(
            {
                "action": "ask",
                "title": "AI应用对市场的影响",
                "confidence": 0.9,
            }
        )

    scope = resolve_document_scope(
        session, "帮我写一份关于ai应用对于市场的影响", invoke=invoke
    )
    assert scope.get("action") == "ask"
    assert scope.get("need_clarify") is True


def test_start_writing_tool_blocks_when_entry_missing(tmp_path: Path):
    (tmp_path / "tasks").mkdir(parents=True)
    session = ChatSession(id="entry_block", phase=ChatPhase.INTENT.value)
    confirm_writing_document_name(session, "测试文档", message="名字叫测试文档")
    # 刻意不设 writing_path
    session.freeform = {
        **(session.freeform or {}),
        "writing_doc_named": True,
        "title": "测试文档",
    }
    from report_writing.chat.session_documents import patch_focus_onboarding

    patch_focus_onboarding(session, name_confirmed=True, entry_path="")
    assert focus_needs_entry_path(session)

    state = ChatControlState(
        home=tmp_path,
        session=session,
        last_user_message="开始写",
        invoke=None,
        emit=None,
        user_action="",
    )
    tools = {t.name: t for t in build_chat_tools(state)}
    out = json.loads(tools["start_writing"].invoke({"writing_pace": "", "chapter_keys_json": "[]"}))
    assert out.get("ok") is False
    miss = out.get("missing_slots") or []
    assert "outline" in miss or "writing_path" in miss


def test_document_scope_clarify_not_rehung_when_pending():
    from report_writing.domain.models import ChatMessage

    session = _named_session("述职报告")
    session.messages = [
        ChatMessage(role="user", text="根据模版写环境报告"),
    ]
    reply1, _cards1 = build_document_scope_clarify(
        session, message="根据模版写环境报告", topic_hint="环境报告"
    )
    pending_id = str((get_pending(session) or {}).get("id") or "")
    assert pending_id
    reply2, cards2 = build_document_scope_clarify(
        session, message="根据模版写环境报告", topic_hint="环境报告"
    )
    assert reply1 == reply2
    assert str((get_pending(session) or {}).get("id") or "") == pending_id
    assert len(cards2) == 1


def test_wizard_meta_creates_new_task_on_title_conflict(tmp_path: Path):
    from report_writing.chat.resource_actions import _action_blueprint_wizard
    from report_writing.controller import ensure_task
    from report_writing.services.store import TaskStore

    (tmp_path / "tasks").mkdir(parents=True)
    old_id = "w_oldsafe01"
    path = tmp_path / "tasks" / old_id
    ensure_task(path, biz_type="002")
    store = TaskStore(path)
    task = store.load()
    task.title = "我的安全报告"
    store.save(task)

    session = ChatSession(id="wiz_meta")
    session.task_id = old_id
    session.blueprint_id = old_id
    session.freeform = {
        "blueprint_wizard": {
            "step": "meta",
            "title": "环境报告",
            "fresh_meta": True,
            "mode": "create",
        },
        "title": "环境报告",
    }
    upsert_session_document(
        session,
        kind="outline",
        task_id=old_id,
        title="我的安全报告",
        clear_blueprint_id=True,
    )

    reply, cards = _action_blueprint_wizard(
        tmp_path,
        session,
        {"step": "meta", "title": "环境报告"},
    )
    assert "我的安全报告" not in reply
    assert "环境报告" in reply
    wiz = dict((session.freeform or {}).get("blueprint_wizard") or {})
    assert str(wiz.get("title") or "") == "环境报告"
    assert str(wiz.get("step") or "") == "upload"
    assert cards


def test_strong_new_doc_phrase_beats_llm_continue():
    session = _named_session("环境报告")
    session.task_id = "w_env_old"
    session.freeform = {
        **(session.freeform or {}),
        "focus_document_key": "task:w_env_old",
        "blueprint_wizard": {
            "step": "study",
            "mode": "freeform_sample",
            "blueprint_id": "w_env_old",
            "working_task_id": "w_env_old",
            "outline_confirmed": True,
        },
    }

    def invoke(_s: str, _u: str) -> str:
        return json.dumps({"action": "continue", "title": "", "confidence": 0.95})

    scope = resolve_document_scope(session, "再写一份述职报告", invoke=invoke)
    assert scope.get("action") == "force_new"
    assert scope.get("force_new") is True
    assert "述职" in str(scope.get("title") or "")


def test_apply_turn_plan_force_new_during_wizard_study(tmp_path: Path):
    (tmp_path / "tasks").mkdir(parents=True)
    session = _named_session("环境报告")
    old_id = "w_env_study"
    session.task_id = old_id
    session.freeform = {
        **(session.freeform or {}),
        "focus_document_key": f"task:{old_id}",
        "blueprint_wizard": {
            "step": "study",
            "mode": "freeform_sample",
            "blueprint_id": old_id,
            "working_task_id": old_id,
            "outline_confirmed": True,
            "title": "环境报告",
        },
    }
    upsert_session_document(
        session,
        kind="outline",
        task_id=old_id,
        title="环境报告",
        clear_blueprint_id=True,
    )

    plan = TurnPlan(
        confidence=0.9,
        tools=[{"tool": "write_analyzed_only", "args": {}}],
        missing=[],
        uncertain=False,
        slots={},
    )

    def invoke(_s: str, _u: str) -> str:
        return json.dumps({"action": "continue", "title": "", "confidence": 0.95})

    hit = apply_turn_plan(
        tmp_path,
        session,
        "再写一份述职报告",
        invoke=invoke,
        plan=plan,
    )
    assert hit is not None
    reply, _cards, _events = hit
    assert "新开" in (reply or "")
    assert str(session.task_id or "") != old_id
    assert str(session.freeform.get("focus_document_key") or "") != f"task:{old_id}"
