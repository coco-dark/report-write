"""已定名焦点遇上新写题材：追问新开/改名/继续，禁止静默改名。"""

from __future__ import annotations

import json
from pathlib import Path

from report_writing.chat.clarify_flow import (
    build_document_scope_clarify,
    execute_clarify_spec,
    get_pending,
)
from report_writing.chat.tool_dispatch import execute_dispatch_tool
from report_writing.chat.session_documents import (
    confirm_writing_document_name,
    resolve_document_scope,
    silent_rename_blocked_topic,
    upsert_session_document,
)
from report_writing.chat.session_goal_actions import run_session_goal_action
from report_writing.domain.models import ChatPhase, ChatSession

AI_LINE = "帮我写一份关于ai应用对于市场的影响"


def _named_session(title: str = "述职报告") -> ChatSession:
    session = ChatSession(id="ask_rename", phase=ChatPhase.FREEFORM_OUTLINE.value)
    confirm_writing_document_name(session, title, message=f"名字叫{title}")
    session.freeform = {
        **(session.freeform or {}),
        "writing_path": "freeform",
        "meta_confirmed": True,
        "writing_doc_named": True,
        "title": title,
    }
    upsert_session_document(
        session,
        kind="outline",
        task_id="w_ask1",
        title=title,
        clear_blueprint_id=True,
    )
    session.task_id = "w_ask1"
    return session


def test_ai_topic_hangs_scope_clarify_not_rename(tmp_path: Path):
    (tmp_path / "tasks").mkdir(parents=True)
    session = _named_session("述职报告")
    before = str((session.freeform or {}).get("title") or "")
    reply, cards = build_document_scope_clarify(
        session, message=AI_LINE, topic_hint="AI应用对市场的影响"
    )
    pending = get_pending(session) or {}
    assert pending.get("source") == "document_scope"
    assert "新开" in reply or "改" in reply or "继续" in reply
    assert len(pending.get("options") or []) >= 3
    assert str((session.freeform or {}).get("title") or "") == before
    assert cards


def test_resolve_scope_heuristic_asks_when_named_diff_topic_without_strong_new():
    """活跃焦点 + 异题写句（无强新开词）→ 先澄清，勿直办 force_new。"""
    session = _named_session("述职报告")
    scope = resolve_document_scope(session, AI_LINE, invoke=None)
    assert scope.get("need_clarify") is True
    assert scope.get("action") == "ask"
    assert scope.get("force_new") is False


def test_silent_rename_blocked_for_write_topic():
    session = _named_session("述职报告")
    blocked = silent_rename_blocked_topic(
        session, AI_LINE, title="AI应用对市场的影响"
    )
    assert blocked
    # 显式改名放行
    assert (
        silent_rename_blocked_topic(session, "改名叫AI市场影响报告", title="AI市场影响报告")
        == ""
    )


def test_set_session_meta_dispatch_asks_not_renames(tmp_path: Path):
    (tmp_path / "tasks").mkdir(parents=True)
    session = _named_session("述职报告")
    before = str((session.freeform or {}).get("title") or "")
    hit = execute_dispatch_tool(
        tmp_path,
        session,
        {
            "tool": "set_session_meta",
            "args": {"title": "AI应用对市场的影响", "message": AI_LINE},
        },
        AI_LINE,
        invoke=None,
    )
    assert hit is not None
    reply, cards, _ = hit
    assert str((session.freeform or {}).get("title") or "") == before
    pending = get_pending(session) or {}
    assert pending.get("source") == "document_scope"
    assert "新开" in (reply or "") or cards


def test_build_document_scope_clarify_on_named_diff_topic(tmp_path: Path):
    """异题新写：挂 document_scope，禁止静默改当前名。"""
    (tmp_path / "tasks").mkdir(parents=True)
    session = _named_session("述职报告")
    before = str((session.freeform or {}).get("title") or "")
    reply, cards = build_document_scope_clarify(
        session, message=AI_LINE, topic_hint="AI应用对市场的影响"
    )
    assert str((session.freeform or {}).get("title") or "") == before
    pending = get_pending(session) or {}
    assert pending.get("source") == "document_scope"
    assert "新开" in (reply or "") or "继续" in (reply or "") or cards
    assert "好，文档就叫" not in (reply or "")


def test_explicit_rename_still_works(tmp_path: Path):
    (tmp_path / "tasks").mkdir(parents=True)
    session = _named_session("述职报告")
    reply, _cards = run_session_goal_action(
        tmp_path,
        session,
        "set_session_meta",
        {"message": "改名叫AI市场影响报告", "title": "AI市场影响报告"},
        invoke=None,
    )
    assert "AI市场影响报告" in str((session.freeform or {}).get("title") or "")
    assert "AI" in (reply or "") or "改" in (reply or "") or "叫" in (reply or "")


def test_strong_force_new_not_treated_as_ask():
    session = _named_session("述职报告")
    scope = resolve_document_scope(
        session, "再帮我写一份关于ai应用对于市场的影响", invoke=None
    )
    assert scope.get("action") == "force_new"
    assert scope.get("need_clarify") is False
    assert scope.get("force_new") is True


def test_scope_option_force_new_opens_onboarding(tmp_path: Path):
    (tmp_path / "tasks").mkdir(parents=True)
    session = _named_session("述职报告")
    build_document_scope_clarify(session, message=AI_LINE, topic_hint="AI题材")
    hit = {
        "source": "document_scope",
        "option_id": "scope_force_new",
        "slots": {"document_scope_action": "force_new", "title": "AI题材"},
        "candidate": {
            "kind": "document_scope",
            "name": "document_scope",
            "payload": {"message": AI_LINE, "title": "AI题材", "current_title": "述职报告"},
        },
    }
    reply, cards, _ = execute_clarify_spec(
        tmp_path, session, hit, message="新开一份", invoke=None
    )
    # 新开后应进入定名/选路，而不是把原「述职报告」改成 AI 题材
    # begin_document_onboarding 会清 named；随后 entry 可能挂 doc_name
    pending = get_pending(session) or {}
    assert pending.get("source") in {"doc_name", "entry_branch", "entry_freeform_mode", "document_scope", ""}
    assert reply or cards


def test_scope_option_continue_keeps_title(tmp_path: Path):
    (tmp_path / "tasks").mkdir(parents=True)
    session = _named_session("述职报告")
    build_document_scope_clarify(session, message=AI_LINE, topic_hint="AI题材")
    hit = {
        "source": "document_scope",
        "option_id": "scope_continue",
        "slots": {"document_scope_action": "continue"},
        "candidate": {
            "kind": "document_scope",
            "name": "document_scope",
            "payload": {
                "message": AI_LINE,
                "title": "AI题材",
                "current_title": "述职报告",
            },
        },
    }
    reply, cards, _ = execute_clarify_spec(
        tmp_path, session, hit, message="继续当前这份", invoke=None
    )
    assert str((session.freeform or {}).get("title") or "") == "述职报告"
    assert "继续" in (reply or "")
    assert "AI" in (reply or "") or "题材" in (reply or "")
    pending = get_pending(session) or {}
    assert pending.get("source") in {
        "entry_branch",
        "entry_freeform_mode",
        "doc_name",
        "entry_path",
        "freeform",
    } or any(getattr(c, "type", "") == "intent_clarify" for c in (cards or []))


def test_scope_option_force_new_does_not_reask_scope(tmp_path: Path):
    (tmp_path / "tasks").mkdir(parents=True)
    session = _named_session("述职报告")
    session.freeform = {
        **(session.freeform or {}),
        "blueprint_wizard": {
            "blueprint_id": "bp_old",
            "step": "study",
            "trigger_chapter_study": ["c_old"],
            "study_stream_active": True,
        },
    }
    build_document_scope_clarify(session, message=AI_LINE, topic_hint="智能写作项目建设报告")
    hit = {
        "source": "document_scope",
        "option_id": "scope_force_new",
        "slots": {
            "document_scope_action": "force_new",
            "title": "智能写作项目建设报告",
        },
        "candidate": {
            "kind": "document_scope",
            "name": "document_scope",
            "payload": {
                "message": AI_LINE,
                "title": "智能写作项目建设报告",
                "current_title": "述职报告",
            },
        },
    }
    reply, cards, _ = execute_clarify_spec(
        tmp_path, session, hit, message="新开一份 · 智能写作项目建设报告", invoke=None
    )
    assert "新开" in (reply or "")
    pending = get_pending(session) or {}
    assert pending.get("source") != "document_scope"
    assert not ((session.freeform or {}).get("blueprint_wizard") or {}).get(
        "trigger_chapter_study"
    )
    assert "智能写作项目建设报告" in str((session.freeform or {}).get("title") or "")
    assert cards or pending.get("source") in {
        "entry_branch",
        "entry_freeform_mode",
        "doc_name",
        "freeform",
    }


def test_same_title_write_topic_not_blocked():
    title = "关于智能报告写作项目的研究报告"
    session = _named_session(title)
    assert silent_rename_blocked_topic(session, f"写一份{title}", title=title) == ""


def test_jiu_jiao_is_explicit_doc_title():
    from report_writing.chat.session_documents import explicit_doc_title_from_message

    assert (
        explicit_doc_title_from_message("就叫「关于智能报告写作项目的研究报告」")
        == "关于智能报告写作项目的研究报告"
    )


def test_scope_llm_ask_maps_need_clarify():
    session = _named_session("述职报告")

    def invoke(system: str, user: str) -> str:
        assert "文档作用域" in system
        return json.dumps(
            {"action": "ask", "title": "AI应用对市场的影响", "confidence": 0.7}
        )

    scope = resolve_document_scope(session, AI_LINE, invoke=invoke)
    assert scope.get("need_clarify") is True
    assert scope.get("action") == "ask"
    assert scope.get("force_new") is False


def test_active_blueprint_unnamed_write_subject_asks_not_renames(tmp_path: Path):
    """已选蓝本在写、未定名，「写一份述职报告」→ scope 澄清，不改标题。"""
    from report_writing.chat.session_documents import (
        set_focus_document_key,
        upsert_session_document,
    )
    from report_writing.chat.session_goal_actions import run_session_goal_action

    (tmp_path / "tasks").mkdir(parents=True)
    session = ChatSession(id="s_shot", phase=ChatPhase.CONFIRM_EXPORT.value)
    session.blueprint_id = "bp1"
    session.task_id = "w1"
    upsert_session_document(
        session,
        kind="writing",
        task_id="w1",
        blueprint_id="bp1",
        writing_task_id="w1",
        title="某安全预评价",
        registry_key="doc:shot1",
    )
    set_focus_document_key(session, "doc:shot1")
    session.freeform = {
        **dict(session.freeform or {}),
        "title": "某安全预评价",
    }
    msg = "写一份述职报告"
    reply, cards = run_session_goal_action(
        tmp_path,
        session,
        "set_session_meta",
        {"message": msg, "title": "述职报告"},
        invoke=None,
    )
    assert str((session.freeform or {}).get("title") or "") == "某安全预评价"
    assert "好，文档就叫「述职报告」" not in (reply or "")
    pending = get_pending(session) or {}
    assert pending.get("source") == "document_scope" or "新开" in (reply or "") or cards


def test_same_topic_write_sentence_no_scope_clarify():
    session = _named_session("述职报告")
    scope = resolve_document_scope(session, "写一份述职报告", invoke=None)
    assert scope.get("action") == "continue"
    assert scope.get("need_clarify") is False


def test_should_update_title_in_place_blocks_write_subject_on_active():
    from report_writing.chat.session_documents import (
        set_focus_document_key,
        should_update_title_in_place,
        upsert_session_document,
    )

    session = ChatSession(id="s_inplace", phase=ChatPhase.CONFIRM_EXPORT.value)
    session.blueprint_id = "bp1"
    upsert_session_document(
        session,
        kind="writing",
        task_id="w1",
        blueprint_id="bp1",
        writing_task_id="w1",
        title="某安全预评价",
        registry_key="doc:inplace",
    )
    set_focus_document_key(session, "doc:inplace")
    assert should_update_title_in_place(
        session,
        "某安全预评价",
        "述职报告",
        message="写一份述职报告",
    ) is False
