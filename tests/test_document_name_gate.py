"""开写前统一引导文档命名（匹配蓝本 / 创建蓝本 / 无蓝本共用）。"""

from __future__ import annotations

import json
from pathlib import Path

from report_writing.chat.clarify_flow import (
    build_document_name_clarify,
    build_entry_path_clarify,
    execute_clarify_spec,
    get_pending,
    try_handle_pending_message,
)
from report_writing.chat.session_documents import (
    confirm_writing_document_name,
    is_writing_document_named,
)
from report_writing.domain.models import ChatPhase, ChatSession


def test_entry_path_uses_working_title_not_name_card(tmp_path: Path):
    from report_writing.chat.session_documents import (
        focus_working_title,
        is_writing_document_named,
    )

    (tmp_path / "tasks").mkdir(parents=True)
    session = ChatSession(id="s1", phase=ChatPhase.INTENT.value)
    reply, cards = build_entry_path_clarify(
        session, message="帮我写一份安全报告", home=tmp_path
    )
    assert not is_writing_document_named(session)
    assert focus_working_title(session) == "安全报告"
    pending = get_pending(session) or {}
    assert pending.get("source") != "doc_name"
    assert "先给这份文档起个名字" not in (reply or "")


def test_confirm_suggested_name_then_entry_path(tmp_path: Path):
    from report_writing.chat.clarify_flow import build_document_name_clarify

    session = ChatSession(id="s2", phase=ChatPhase.INTENT.value)
    build_document_name_clarify(session, message="帮我写一份", title_hint="安全报告")

    def _invoke(system: str, user: str) -> str:
        return json.dumps(
            {
                "decision": "pick",
                "option_id": "use_suggested_name",
                "title": "",
                "question": "",
                "confidence": 0.9,
            }
        )

    hit = try_handle_pending_message(session, "就叫「安全报告」", invoke=_invoke)
    assert isinstance(hit, dict)
    reply, cards, _ = execute_clarify_spec(
        tmp_path, session, hit, message="就叫「安全报告」"
    )
    assert is_writing_document_named(session)
    assert "安全报告" in reply
    assert get_pending(session) and get_pending(session).get("source") == "entry_branch"
    assert any(getattr(c, "type", "") == "intent_clarify" for c in cards)


def test_typed_name_during_doc_name_clarify(tmp_path: Path):
    session = ChatSession(id="s3", phase=ChatPhase.INTENT.value)
    build_document_name_clarify(session, message="帮我写一份", title_hint="安全报告")

    def _invoke(system: str, user: str) -> str:
        return json.dumps(
            {
                "decision": "apply_title",
                "option_id": "",
                "title": "2026年度安全报告",
                "question": "",
                "confidence": 0.92,
            }
        )

    hit = try_handle_pending_message(session, "2026年度安全报告", invoke=_invoke)
    assert isinstance(hit, dict)
    assert hit.get("slots", {}).get("doc_name_action") == "apply"
    assert hit.get("slots", {}).get("title") == "2026年度安全报告"
    reply, _cards, _ = execute_clarify_spec(
        tmp_path, session, hit, message="2026年度安全报告"
    )
    assert is_writing_document_named(session)
    assert (session.freeform or {}).get("title") == "2026年度安全报告"
    assert get_pending(session) and get_pending(session).get("source") == "entry_branch"
    assert "2026年度安全报告" in reply


def test_confirm_name_does_not_reopen_doc_name_with_old_write_message(tmp_path: Path):
    """定名成功后不得用旧「写一份…」再清 named / 重挂 doc_name（死循环回归）。"""
    from report_writing.chat.clarify_flow import build_document_name_clarify

    (tmp_path / "tasks").mkdir(parents=True)
    session = ChatSession(id="s_loop", phase=ChatPhase.INTENT.value)
    build_document_name_clarify(session, message="帮我写一份述职报告", title_hint="述职报告")
    assert get_pending(session) and get_pending(session).get("source") == "doc_name"

    def _invoke(system: str, user: str) -> str:
        return json.dumps(
            {
                "decision": "apply_title",
                "option_id": "",
                "title": "2026季度述职",
                "question": "",
                "confidence": 0.95,
            }
        )

    hit = try_handle_pending_message(session, "2026季度述职", invoke=_invoke)
    assert isinstance(hit, dict)
    reply, cards, _ = execute_clarify_spec(
        tmp_path, session, hit, message="2026季度述职"
    )
    assert is_writing_document_named(session)
    assert (session.freeform or {}).get("title") == "2026季度述职"
    pending = get_pending(session)
    assert pending and pending.get("source") == "entry_branch"
    # 不得再次出现定名卡；建议名保持用户确认名
    assert all(
        str((getattr(c, "payload", {}) or {}).get("source") or "") != "doc_name"
        for c in (cards or [])
    )
    assert "2026季度述职" in reply
    # 入口问题应带已确认名，勿退回「述职报告」定名
    assert "起个名字" not in reply
    assert "述职报告」想怎么开始" not in reply or "2026季度述职" in reply


def test_force_new_confirm_opens_freeform_without_rename_loop(tmp_path: Path):
    """「再给我写份」定名确认后挂入口，不再弹定名。"""
    from report_writing.chat.clarify_flow import build_document_name_clarify

    (tmp_path / "tasks").mkdir(parents=True)
    session = ChatSession(id="s_force_new", phase=ChatPhase.INTENT.value)
    build_document_name_clarify(session, message="再给我写份述职报告", title_hint="述职报告")

    def _invoke(system: str, user: str) -> str:
        return json.dumps(
            {
                "decision": "apply_title",
                "option_id": "",
                "title": "2026季度述职",
                "question": "",
                "confidence": 0.95,
            }
        )

    hit = try_handle_pending_message(session, "2026季度述职", invoke=_invoke)
    reply, cards, _ = execute_clarify_spec(
        tmp_path, session, hit, message="2026季度述职"
    )
    assert is_writing_document_named(session)
    assert (session.freeform or {}).get("title") == "2026季度述职"
    assert get_pending(session) and get_pending(session).get("source") == "entry_branch"
    assert "起个名字" not in reply
    assert not any(
        str((getattr(c, "payload", {}) or {}).get("source") or "") == "doc_name"
        for c in (cards or [])
    )


def test_affirmation_confirms_suggested_doc_name_via_llm(tmp_path: Path):
    """「是的」由上下文模型判为确认建议名，不能把文档叫成「是的」。"""
    session = ChatSession(id="s3b", phase=ChatPhase.INTENT.value)
    # 故意不走首句显式定名，先挂 doc_name，再测确认回复
    build_entry_path_clarify(session, message="帮我写一份安全报告", title_hint="")
    # 手动挂带 tip 的定名澄清
    from report_writing.chat.clarify_flow import build_document_name_clarify

    build_document_name_clarify(
        session, message="帮我写一份安全报告", title_hint="1101安全预评价"
    )
    pending = get_pending(session)
    assert pending and pending.get("source") == "doc_name"

    def _invoke(system: str, user: str) -> str:
        assert "1101安全预评价" in system or "1101安全预评价" in user
        return json.dumps(
            {
                "decision": "pick",
                "option_id": "use_suggested_name",
                "title": "",
                "question": "",
                "confidence": 0.95,
            }
        )

    hit = try_handle_pending_message(session, "是的", invoke=_invoke)
    assert isinstance(hit, dict)
    assert hit.get("slots", {}).get("title") == "1101安全预评价"
    assert hit.get("option_id") == "use_suggested_name"
    reply, _cards, _ = execute_clarify_spec(tmp_path, session, hit, message="是的")
    assert is_writing_document_named(session)
    assert (session.freeform or {}).get("title") == "1101安全预评价"
    assert "是的」" not in reply
    assert "1101安全预评价" in reply


def test_explicit_name_in_first_message_skips_doc_name_confirm(tmp_path: Path):
    """首句已「名字叫做…」→ 直接定名并问路径，不再二次确认。"""
    (tmp_path / "tasks").mkdir(parents=True)
    session = ChatSession(id="s3c", phase=ChatPhase.INTENT.value)
    reply, _cards = build_entry_path_clarify(
        session,
        message="帮我写一份安全报告，名字叫做1101安全预评价",
        home=tmp_path,
    )
    pending = get_pending(session)
    assert pending and pending.get("source") == "entry_branch"
    assert is_writing_document_named(session)
    assert (session.freeform or {}).get("title") == "1101安全预评价"
    assert "1101安全预评价" in reply
    assert "起个名字" not in reply


def test_rename_phrase_applies_without_naming_clarify(tmp_path: Path):
    """「名字改一下叫做 X」是改名/显式定名，禁止再挂「起个名字」卡。"""
    (tmp_path / "tasks").mkdir(parents=True)
    session = ChatSession(id="s_rename", phase=ChatPhase.INTENT.value)
    confirm_writing_document_name(session, "旧安全报告", home=tmp_path)
    reply, cards = build_document_name_clarify(
        session,
        message="名字改一下叫做 lzb 安全报告",
        home=tmp_path,
    )
    assert is_writing_document_named(session)
    title = str((session.freeform or {}).get("title") or "")
    assert "lzb" in title and "安全报告" in title.replace(" ", "")
    assert "起个名字" not in reply
    assert (
        "已更新" in reply
        or "就叫" in reply
        or "打算走哪条路" in reply
    )
    assert not any(
        str((getattr(c, "payload", {}) or {}).get("source") or "") == "doc_name"
        for c in (cards or [])
    )


def test_rename_while_doc_name_pending_applies_offline(tmp_path: Path):
    """定名澄清挂起时用户改口改名 → 离线也应直接落盘，勿再问起名。"""
    session = ChatSession(id="s_rename_pending", phase=ChatPhase.INTENT.value)
    build_document_name_clarify(session, message="帮我写一份", title_hint="安全报告")
    assert get_pending(session) and get_pending(session).get("source") == "doc_name"
    hit = try_handle_pending_message(
        session, "名字改一下叫做 lzb 安全报告", invoke=None
    )
    assert isinstance(hit, dict)
    assert "lzb" in str(hit.get("slots", {}).get("title") or "")
    reply, _cards, _ = execute_clarify_spec(
        tmp_path, session, hit, message="名字改一下叫做 lzb 安全报告"
    )
    assert is_writing_document_named(session)
    assert "起个名字" not in reply


def test_write_with_own_template_asks_name_without_topic_suggestion(tmp_path: Path):
    """「写一份安全报告 用我的模版」未起名：下发向导名称卡，勿建议「就叫安全报告」。"""
    from report_writing.chat.session_documents import focus_onboarding

    (tmp_path / "tasks").mkdir(parents=True)
    session = ChatSession(id="s_tpl", phase=ChatPhase.INTENT.value)
    reply, cards = build_entry_path_clarify(
        session, message="帮我写一份安全报告 用我的模版", home=tmp_path
    )
    assert not is_writing_document_named(session)
    wiz = [c for c in (cards or []) if getattr(c, "type", "") == "blueprint_wizard"]
    suggested = [
        str(t.get("tool") or "")
        for t in (session.freeform or {}).get("suggested_next_tools") or []
    ]
    assert not wiz
    assert "begin_blueprint_wizard" in suggested
    assert "就叫「安全报告」" not in reply
    assert "模版" in reply or "样板" in reply or "自动生成大纲" in reply or "对齐" in reply
    ob = focus_onboarding(session)
    assert str(ob.get("preferred_entry_path") or "") == "template" or str(
        ob.get("entry_path") or ""
    ) == "template"


def test_already_named_does_not_rehang_doc_name_clarify(tmp_path: Path):
    """已定名后再走 build_document_name_clarify，禁止重复「先给这份文档起个名字」。"""
    (tmp_path / "tasks").mkdir(parents=True)
    session = ChatSession(id="s_named_loop", phase=ChatPhase.INTENT.value)
    confirm_writing_document_name(session, "lzb的述职报告", home=tmp_path)
    assert is_writing_document_named(session)
    reply, cards = build_document_name_clarify(
        session,
        message="lzb的述职报告",
        title_hint="lzb的述职报告",
        follow_entry=False,
        home=tmp_path,
    )
    assert is_writing_document_named(session)
    assert "先给这份文档起个名字" not in (reply or "")
    assert not any(
        str((getattr(c, "payload", {}) or {}).get("source") or "") == "doc_name"
        for c in (cards or [])
    )
    pending = get_pending(session) or {}
    assert pending.get("source") != "doc_name"


def test_typed_name_after_clarify_continues_entry_not_rename_loop(tmp_path: Path):
    """澄清中用户打字起名 → 确认后进入选路，不再二次起名。"""
    (tmp_path / "tasks").mkdir(parents=True)
    session = ChatSession(id="s_typed_loop", phase=ChatPhase.INTENT.value)
    build_document_name_clarify(
        session, message="帮我写一份述职报告", title_hint="述职报告"
    )
    assert get_pending(session) and get_pending(session).get("source") == "doc_name"
    hit = try_handle_pending_message(session, "lzb的述职报告", invoke=None)
    assert isinstance(hit, dict)
    reply, cards, _ = execute_clarify_spec(
        tmp_path, session, hit, message="lzb的述职报告", invoke=None
    )
    assert is_writing_document_named(session)
    assert (session.freeform or {}).get("title") == "lzb的述职报告"
    assert "好，文档就叫「lzb的述职报告」" in (reply or "")
    assert "先给这份文档起个名字" not in (reply or "")
    pending = get_pending(session) or {}
    assert pending.get("source") != "doc_name"
    sources = {
        str((getattr(c, "payload", {}) or {}).get("source") or "") for c in (cards or [])
    }
    assert "doc_name" not in sources


def test_write_subject_asks_name_without_asserting_done(tmp_path: Path):
    """「帮我写一份××」：落工作名，禁止先断言「好，文档就叫」。"""
    from report_writing.chat.session_documents import focus_working_title
    from report_writing.chat.session_goal_actions import run_session_goal_action

    (tmp_path / "tasks").mkdir(parents=True)
    session = ChatSession(id="s_subj_ask", phase=ChatPhase.INTENT.value)
    msg = "帮我写一份 关于建设智能文档的必要性"
    tip = "关于建设智能文档的必要性"
    reply, cards = run_session_goal_action(
        tmp_path,
        session,
        "set_session_meta",
        {"message": msg, "title": tip},
        invoke=None,
    )
    assert not is_writing_document_named(session)
    assert "好，文档就叫" not in (reply or "")
    assert focus_working_title(session)
    pending = get_pending(session) or {}
    assert pending.get("source") != "doc_name"
    assert not any(
        str((getattr(c, "payload", {}) or {}).get("source") or "") == "doc_name"
        for c in (cards or [])
    )


def test_hang_missing_name_does_not_also_hang_entry_path(tmp_path: Path):
    """写意图有工作名时不得再挂定名卡；选路可另轮，禁止同轮双挂。"""
    from report_writing.chat.intent_turn_plan import hang_missing_slots
    from report_writing.chat.session_documents import focus_working_title

    (tmp_path / "tasks").mkdir(parents=True)
    session = ChatSession(id="s_hang_once", phase=ChatPhase.INTENT.value)
    reply, cards = hang_missing_slots(
        session,
        missing=["doc_name", "writing_path"],
        message="帮我写一份关于建设智能文档的必要性",
        title_hint="关于建设智能文档的必要性",
        home=tmp_path,
    )
    assert "先给这份文档起个名字" not in (reply or "")
    sources = {
        str((getattr(c, "payload", {}) or {}).get("source") or "") for c in (cards or [])
    }
    assert "doc_name" not in sources
    assert focus_working_title(session)
    pending = get_pending(session) or {}
    assert pending.get("source") != "doc_name"


def test_set_session_meta_rejects_template_topic_title(tmp_path: Path):
    """set_session_meta(title=安全报告) + 用模版原话 → 不起名、不说题目记下了。"""
    from report_writing.chat.session_goal_actions import run_session_goal_action
    from report_writing.chat.session_documents import title_is_write_subject_not_doc_name

    (tmp_path / "tasks").mkdir(parents=True)
    for msg in (
        "我有模版你根据我的模版 写一份安全报告",
        "安全报告 用我的模版",
        "根据我的模版写安全报告",
    ):
        assert title_is_write_subject_not_doc_name(msg, "安全报告"), msg
        session = ChatSession(id=f"s_meta_{hash(msg) & 0xffff}", phase=ChatPhase.INTENT.value)
        reply, cards = run_session_goal_action(
            tmp_path,
            session,
            "set_session_meta",
            {"message": msg, "title": "安全报告", "category": "安全报告"},
            invoke=None,
        )
        assert not is_writing_document_named(session), msg
        assert "题目记下了" not in (reply or ""), msg
        assert "好，文档就叫" not in (reply or ""), msg
        named_src = any(
            str((getattr(c, "payload", {}) or {}).get("source") or "") == "doc_name"
            for c in (cards or [])
        )
        assert not named_src, msg


def test_explicit_name_via_set_session_meta_multi_intent_line(tmp_path: Path):
    """多意图走 set_session_meta 时，句中「名字叫…」也应直接定名。"""
    from report_writing.chat.session_goal_actions import run_session_goal_action

    (tmp_path / "tasks").mkdir(parents=True)
    session = ChatSession(id="s3e", phase=ChatPhase.INTENT.value)
    msg = "帮我生成一份述职报告 名字叫lzb述职报告"
    reply, cards = run_session_goal_action(
        tmp_path,
        session,
        "set_session_meta",
        {"message": msg},
        invoke=None,
    )
    assert is_writing_document_named(session)
    assert (session.freeform or {}).get("title") == "lzb述职报告"
    assert "起个名字" not in (reply or "")
    pending = get_pending(session) or {}
    assert pending.get("source") != "doc_name"
    assert not any(getattr(c, "payload", {}) and (c.payload or {}).get("source") == "doc_name" for c in cards)


def test_offline_doc_name_safe_default_uses_tip(tmp_path: Path):
    """文档名这一问：「是的 / 好的」确认建议名，不把确认语本身当成标题。"""
    from report_writing.chat.clarify_flow import build_document_name_clarify

    session = ChatSession(id="s3d", phase=ChatPhase.INTENT.value)
    build_document_name_clarify(session, message="帮我写一份安全报告", title_hint="安全报告")
    hit = try_handle_pending_message(session, "是的", invoke=None)
    assert isinstance(hit, dict)
    assert hit.get("slots", {}).get("title") == "安全报告"
    assert hit.get("option_id") == "use_suggested_name"
    reply, _cards, _ = execute_clarify_spec(tmp_path, session, hit, message="是的")
    assert (session.freeform or {}).get("title") == "安全报告"
    assert "安全报告" in reply
    assert "是的」" not in reply


def test_named_session_goes_straight_to_entry_path():
    session = ChatSession(id="s4", phase=ChatPhase.INTENT.value)
    confirm_writing_document_name(session, "安全报告", message="帮我写一份安全报告")
    reply, _cards = build_entry_path_clarify(session, message="帮我写一份安全报告")
    assert get_pending(session) and get_pending(session).get("source") == "entry_branch"
    assert "第二步" in reply or "有蓝本" in reply or "第一步" in reply or "模版" in reply


def test_specific_doc_name_with_blueprint_tail_beats_suggestion(tmp_path: Path):
    """「2026年工程安全报告 用蓝本库的」定这个名字，并去匹配蓝本，不退回建议名。"""
    from report_writing.chat.clarify_flow import build_document_name_clarify
    from report_writing.chat.session_documents import focus_onboarding

    session = ChatSession(id="s_name_split", phase=ChatPhase.INTENT.value)
    build_document_name_clarify(session, message="帮我写一份安全报告", title_hint="安全报告")

    def _unclear(system: str, user: str) -> str:
        return json.dumps(
            {"decision": "unclear", "option_id": "", "title": "", "question": "", "confidence": 0.2},
            ensure_ascii=False,
        )

    hit = try_handle_pending_message(
        session, "2026年工程安全报告 用蓝本库的", invoke=_unclear
    )
    assert isinstance(hit, dict)
    assert hit.get("slots", {}).get("title") == "2026年工程安全报告"
    assert "蓝本" in str(hit.get("slots", {}).get("residual_message") or "")
    assert hit.get("defaulted") is not True
    reply, _cards, _ = execute_clarify_spec(
        tmp_path,
        session,
        hit,
        message="2026年工程安全报告 用蓝本库的",
        invoke=_unclear,
    )
    assert (session.freeform or {}).get("title") == "2026年工程安全报告"
    assert "2026年工程安全报告" in reply
    assert "我先按「安全报告」" not in reply
    ob = focus_onboarding(session)
    assert ob.get("preferred_entry_path") == "match" or ob.get("entry_path") == "match"

    session2 = ChatSession(id="s_name_split2", phase=ChatPhase.INTENT.value)
    build_document_name_clarify(session2, message="帮我写一份安全报告", title_hint="安全报告")
    pending = get_pending(session2)
    pending["ask_count"] = 1
    session2.freeform = {**(session2.freeform or {}), "intent_clarify": pending}
    again = try_handle_pending_message(session2, "2026年工程安全报告", invoke=_unclear)
    assert isinstance(again, dict)
    assert again.get("slots", {}).get("title") == "2026年工程安全报告"
    assert again.get("defaulted") is not True


def test_confirm_name_preserves_entry_branch_when_path_not_yet_chosen(tmp_path: Path):
    """已点「无蓝本」后细化文档名：勿再挂有蓝本/无蓝本主路卡。"""
    from report_writing.chat.clarify_flow import build_entry_path_clarify
    from report_writing.chat.session_documents import (
        confirm_writing_document_name,
        focus_needs_entry_branch,
        mark_focus_entry_branch,
    )

    (tmp_path / "tasks").mkdir(parents=True)
    session = ChatSession(id="s_keep_branch", phase=ChatPhase.INTENT.value)
    confirm_writing_document_name(
        session, "述职报告", home=tmp_path, message="新开一份述职报告"
    )
    mark_focus_entry_branch(session, "without_blueprint")
    assert not focus_needs_entry_branch(session)
    confirm_writing_document_name(
        session, "我的述职报告", home=tmp_path, message="我的述职报告"
    )
    assert not focus_needs_entry_branch(session)
    reply, cards = build_entry_path_clarify(
        session,
        message="",
        title_hint="我的述职报告",
        home=tmp_path,
        after_doc_name_confirm=True,
        apply_plan=False,
    )
    assert focus_needs_entry_branch(session) is False
    assert "打算走哪条路" not in (reply or "")
    assert not any(
        str((getattr(c, "payload", {}) or {}).get("source") or "") == "entry_branch"
        for c in (cards or [])
    )
    """挂着起名卡时，「新写一份述职报告」不是文档名，也不套用建议名。"""
    from report_writing.chat.clarify_flow import build_document_name_clarify

    session = ChatSession(id="s_subj_name", phase=ChatPhase.INTENT.value)
    build_document_name_clarify(session, message="帮我写一份安全报告", title_hint="安全报告")

    def _unclear(system: str, user: str) -> str:
        return json.dumps(
            {
                "decision": "apply_title",
                "option_id": "",
                "title": "述职报告",
                "question": "",
                "confidence": 0.9,
            },
            ensure_ascii=False,
        )

    hit = try_handle_pending_message(session, "新写一份述职报告", invoke=_unclear)
    assert isinstance(hit, str)
    assert get_pending(session) and get_pending(session).get("source") == "doc_name"
    assert "述职报告" not in str((session.freeform or {}).get("title") or "")
    pending = get_pending(session)
    pending["ask_count"] = 1
    session.freeform = {**(session.freeform or {}), "intent_clarify": pending}
    again = try_handle_pending_message(session, "新写一份述职报告", invoke=_unclear)
    assert isinstance(again, str)
    assert not is_writing_document_named(session)


def test_doc_name_residual_continues_entry_path(tmp_path: Path):
    """一句多意图：定名后 residual 继续消歧入口路径。"""
    from report_writing.chat.clarify_flow import build_document_name_clarify

    session = ChatSession(id="s5", phase=ChatPhase.INTENT.value)
    build_document_name_clarify(
        session, message="帮我写报告", title_hint="1101安全预评价"
    )

    def _invoke(system: str, user: str) -> str:
        if "文档起名" in system or "建议文档名" in user or "建议名" in system:
            return json.dumps(
                {
                    "decision": "apply_title",
                    "option_id": "",
                    "title": "1101安全预评价",
                    "residual": "然后匹配已有蓝本",
                    "question": "",
                    "confidence": 0.93,
                },
                ensure_ascii=False,
            )
        # 入口路径澄清
        return json.dumps(
            {
                "decision": "pick",
                "option_id": "match_blueprint",
                "title": "",
                "residual": "",
                "question": "",
                "confidence": 0.9,
            },
            ensure_ascii=False,
        )

    hit = try_handle_pending_message(
        session, "就叫1101安全预评价，然后匹配已有蓝本", invoke=_invoke
    )
    assert isinstance(hit, dict)
    assert hit.get("slots", {}).get("title") == "1101安全预评价"
    assert "匹配" in str(hit.get("slots", {}).get("residual_message") or "")
    reply, cards, _ = execute_clarify_spec(
        tmp_path,
        session,
        hit,
        message="就叫1101安全预评价，然后匹配已有蓝本",
        invoke=_invoke,
    )
    assert is_writing_document_named(session)
    assert (session.freeform or {}).get("title") == "1101安全预评价"
    # 残余路径应被消歧：不再停在 entry 澄清，或已带上匹配意图
    pending = get_pending(session)
    assert pending is None or pending.get("source") != "doc_name"
    assert "1101安全预评价" in reply


def test_new_write_subject_does_not_name_document(tmp_path: Path):
    """「新写一份述职报告」只是题材：新开文档，不订名，也不沿用上一份的标题。"""
    from report_writing.chat.session_documents import begin_document_onboarding
    from report_writing.chat.session_goal import merge_session_goal

    (tmp_path / "tasks").mkdir(parents=True)
    session = ChatSession(id="s_new_subj", phase=ChatPhase.FREEFORM_OUTLINE.value)
    session.task_id = "w_old"
    session.freeform = {
        "title": "安全预评价",
        "category": "安全报告",
        "writing_doc_named": True,
        "meta_confirmed": True,
        "focus_document_key": "task:w_old",
    }
    merge_session_goal(
        session,
        {"doc_category": "安全报告", "doc_title": "安全预评价", "summary": "安全报告｜安全预评价"},
    )
    begin_document_onboarding(
        session,
        tmp_path,
        message="新写一份述职报告",
        title_hint="述职报告",
        force_new=True,
    )
    assert not is_writing_document_named(session)
    assert str((session.freeform or {}).get("title") or "").strip() not in {
        "述职报告",
        "安全预评价",
    }
    goal = ((session.freeform or {}).get("session_goal") or {})
    assert str(goal.get("doc_title") or "") != "安全预评价"
    assert str(goal.get("doc_title") or "") != "述职报告"
    assert str(session.task_id or "") != "w_old"


def test_explicit_name_still_opens_named_shell(tmp_path: Path):
    from report_writing.chat.session_documents import begin_document_onboarding

    (tmp_path / "tasks").mkdir(parents=True)
    session = ChatSession(id="s_named_new", phase=ChatPhase.INTENT.value)
    session.freeform = {"title": "安全预评价", "writing_doc_named": True, "meta_confirmed": True}
    begin_document_onboarding(
        session,
        tmp_path,
        message="新写一份安全报告 名字叫lzb安全报告",
        title_hint="安全报告",
        force_new=True,
    )
    assert (session.freeform or {}).get("title") == "lzb安全报告"
    assert session.task_id


def test_collect_meta_does_not_confirm_subject_with_old_title(tmp_path: Path):
    """未定名时「新写一份述职报告」不能回「收到，是【述职报告】安全预评价」。"""
    from report_writing.chat.actions import _collect_freeform_meta
    from report_writing.chat.session_goal import merge_session_goal

    (tmp_path / "tasks").mkdir(parents=True)
    session = ChatSession(id="s_collect_subj", phase=ChatPhase.FREEFORM_ASK.value)
    session.freeform = {"title": "安全预评价", "category": "述职报告"}
    merge_session_goal(
        session,
        {"doc_category": "述职报告", "doc_title": "安全预评价", "summary": "述职报告｜安全预评价"},
    )
    reply, cards = _collect_freeform_meta(
        session,
        "新写一份述职报告",
        home=tmp_path,
    )
    assert "收到，是【" not in (reply or "")
    assert not is_writing_document_named(session)
    assert "好，文档就叫" not in (reply or "")


def test_assistant_echo_is_not_user_input():
    from report_writing.chat.dialogue import (
        drop_assistant_echo_user_messages,
        format_full_dialogue,
        message_repeats_assistant_reply,
    )
    from report_writing.domain.models import ChatMessage

    ai = "收到，是【述职报告】安全预评价。可以说「帮我生成目录」。"
    session = ChatSession(id="s_echo", phase=ChatPhase.INTENT.value)
    session.messages = [
        ChatMessage(role="user", text="新写一份述职报告"),
        ChatMessage(role="assistant", text=ai),
        ChatMessage(role="user", text=ai),
        ChatMessage(role="assistant", text=ai),
        ChatMessage(role="user", text="继续"),
        ChatMessage(role="assistant", text="好，继续写。"),
    ]
    assert message_repeats_assistant_reply(session, ai)
    assert drop_assistant_echo_user_messages(session)
    roles = [m.role for m in session.messages]
    texts = [m.text for m in session.messages]
    assert texts[0] == "新写一份述职报告"
    assert roles[1] == "assistant"
    assert ai not in [m.text for m in session.messages if m.role == "user"]
    assert "继续" in texts
    dialogue = format_full_dialogue(session, max_turns=12)
    user_lines = [row["text"] for row in dialogue if row["role"] == "user"]
    assert any("新写一份述职报告" in line for line in user_lines)
    assert not any("收到，是【述职报告】" in line for line in user_lines)
