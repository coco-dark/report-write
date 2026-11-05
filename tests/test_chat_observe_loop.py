"""口语主路径：ReAct 观察闭环（改名结构化 title、缺参 observation、多跳）。"""

from __future__ import annotations

import json
from pathlib import Path

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage

from report_writing.agents.chat_control import (
    ChatControlState,
    build_chat_tools,
    run_chat_control,
)
from report_writing.agents.scripted import ScriptedChatModel
from report_writing.chat.session_goal import get_session_goal
from report_writing.controller import ensure_task, save_outline, save_spec_meta
from report_writing.domain.models import ChatPhase, ChatSession, OutlineNode


def _session_named(tmp_path: Path) -> ChatSession:
    tid = "w20260101140000obs"
    session = ChatSession(
        id="s-obs",
        phase=ChatPhase.FREEFORM_OUTLINE.value,
        task_id=tid,
        freeform={
            "meta_confirmed": True,
            "category": "工作汇报",
            "title": "工作汇报",
            "outline_pending_edit": True,
            "skip_blueprint": True,
        },
    )
    wdir = tmp_path / "tasks" / tid
    ensure_task(wdir)
    save_spec_meta(wdir, title="工作汇报", category="工作汇报")
    chapters = [
        OutlineNode(key=f"c{i}", sequence=str(i), title=f"第{i}章", level=1)
        for i in range(1, 3)
    ]
    save_outline(wdir, chapters)
    return session


def test_set_session_meta_tool_requires_structured_title(tmp_path: Path):
    session = _session_named(tmp_path)
    state = ChatControlState(home=tmp_path, session=session)
    tools = {t.name: t for t in build_chat_tools(state)}
    raw = tools["set_session_meta"].invoke({"message": "改个名字 2026个人述职报告"})
    data = json.loads(raw)
    assert data.get("ok") is False
    assert "title" in (data.get("missing_slots") or [])
    assert (data.get("observation") or "").strip()
    assert get_session_goal(session).get("doc_title") != "2026个人述职报告"


def test_set_session_meta_tool_with_title_returns_observation(tmp_path: Path):
    session = _session_named(tmp_path)
    state = ChatControlState(home=tmp_path, session=session)
    tools = {t.name: t for t in build_chat_tools(state)}
    raw = tools["set_session_meta"].invoke(
        {"message": "改个名字 2026个人述职报告", "title": "2026个人述职报告"}
    )
    data = json.loads(raw)
    assert data.get("ok") is True
    assert "observation" in data
    assert get_session_goal(session).get("doc_title") == "2026个人述职报告"


class _RenameObserveModel(ScriptedChatModel):
    """模拟 ReAct：先看缺参 observation，再带 title 重试。"""

    role: str = "rename_observe"

    def decide(self, messages: list[BaseMessage]) -> AIMessage:
        tool_msgs = [m for m in messages if isinstance(m, ToolMessage)]
        if tool_msgs:
            last = tool_msgs[-1]
            try:
                data = json.loads(last.content or "{}")
            except json.JSONDecodeError:
                data = {}
            if data.get("ok") is False and "title" in (data.get("missing_slots") or []):
                return self.call(
                    "set_session_meta",
                    {
                        "title": "2026个人述职报告",
                        "message": "改个名字 2026个人述职报告",
                    },
                )
            hint = str(data.get("reply_hint") or "").strip()
            return self.finish(hint or "好，改好了。")
        return self.call(
            "set_session_meta",
            {"message": "改个名字 2026个人述职报告"},
        )


def test_react_rename_retries_after_missing_title_observation(tmp_path: Path):
    session = _session_named(tmp_path)
    state = ChatControlState(home=tmp_path, session=session)
    user_text = json.dumps(
        {
            "action": "",
            "message": "改个名字 2026个人述职报告",
            "session": {"phase": session.phase, "task_id": session.task_id},
        },
        ensure_ascii=False,
    )
    reply = run_chat_control(
        model=_RenameObserveModel(),
        state=state,
        user_text=user_text,
        system_prompt="",
    )
    assert "跟我说一下文档叫什么" not in (reply or "")
    assert get_session_goal(session).get("doc_title") == "2026个人述职报告"


class _MultiHopWriteModel(ScriptedChatModel):
    """开写多跳：先 skip_blueprint（用户说不用蓝本），再读 observation。"""

    role: str = "multi_hop_write"

    def decide(self, messages: list[BaseMessage]) -> AIMessage:
        tool_msgs = [m for m in messages if isinstance(m, ToolMessage)]
        if tool_msgs:
            try:
                data = json.loads(tool_msgs[-1].content or "{}")
            except json.JSONDecodeError:
                data = {}
            assert (data.get("observation") or data.get("reply_hint") or "").strip()
            return self.finish(str(data.get("reply_hint") or "好，按无蓝本继续。"))
        return self.call("skip_blueprint", {"message": "不用蓝本"})


def test_react_open_write_multi_hop_reads_observation(tmp_path: Path):
    from report_writing.chat.intent_router import user_chose_skip_blueprint

    session = ChatSession(id="s-hop", phase=ChatPhase.INTENT.value)
    state = ChatControlState(home=tmp_path, session=session)
    user_text = json.dumps(
        {"action": "", "message": "写一份调研报告，不用蓝本", "session": {"phase": "intent"}},
        ensure_ascii=False,
    )
    reply = run_chat_control(
        model=_MultiHopWriteModel(),
        state=state,
        user_text=user_text,
        system_prompt="",
    )
    assert (reply or "").strip()
    assert user_chose_skip_blueprint(session)


class _FailThenClarifyModel(ScriptedChatModel):
    """Tool 失败后根据 observation 改口，不再死文案短路。"""

    role: str = "fail_observe"

    def decide(self, messages: list[BaseMessage]) -> AIMessage:
        for message in reversed(messages):
            if isinstance(message, ToolMessage):
                try:
                    data = json.loads(message.content or "{}")
                except json.JSONDecodeError:
                    data = {}
                obs = str(data.get("observation") or data.get("reply_hint") or "")
                if data.get("ok") is False:
                    return self.finish(f"这章还没分析好，我先帮你安排学习。（观察：{obs[:60]}）")
                return self.finish(str(data.get("reply_hint") or "好的。"))
            if isinstance(message, HumanMessage):
                break
        # 故意调一个会缺参的工具，模拟失败观察
        return self.call("set_session_meta", {"message": "改个名字"})


def test_tool_failure_observation_allows_reschedule_reply(tmp_path: Path):
    session = _session_named(tmp_path)
    state = ChatControlState(home=tmp_path, session=session)
    user_text = json.dumps(
        {"action": "", "message": "改个名字", "session": {"phase": session.phase}},
        ensure_ascii=False,
    )
    reply = run_chat_control(
        model=_FailThenClarifyModel(),
        state=state,
        user_text=user_text,
        system_prompt="",
    )
    assert "观察" in (reply or "") or "分析" in (reply or "") or "学习" in (reply or "")


def test_start_writing_requires_doc_name_observation(tmp_path: Path):
    session = ChatSession(id="s-unnamed", phase=ChatPhase.INTENT.value, freeform={})
    state = ChatControlState(home=tmp_path, session=session)
    tools = {t.name: t for t in build_chat_tools(state)}
    raw = tools["start_writing"].invoke({})
    data = json.loads(raw)
    assert data.get("ok") is False
    miss = data.get("missing_slots") or []
    assert "outline" in miss or "doc_name" in miss
    assert str(data.get("observation") or "")


def test_add_chapter_element_missing_slots(tmp_path: Path):
    session = _session_named(tmp_path)
    state = ChatControlState(home=tmp_path, session=session)
    tools = {t.name: t for t in build_chat_tools(state)}
    raw = tools["add_chapter_element"].invoke({"payload_json": "{}"})
    data = json.loads(raw)
    assert data.get("ok") is False
    missing = data.get("missing_slots") or []
    assert "chapter_key" in missing or "name" in missing


def test_select_blueprint_missing_id(tmp_path: Path):
    session = ChatSession(id="s-bp", phase=ChatPhase.BLUEPRINT_SELECT.value)
    state = ChatControlState(home=tmp_path, session=session, agent_may_select_blueprint=True)
    tools = {t.name: t for t in build_chat_tools(state)}
    raw = tools["select_blueprint"].invoke({"blueprint_id": ""})
    data = json.loads(raw)
    assert data.get("ok") is False
    assert "blueprint_id" in (data.get("missing_slots") or [])


def test_submit_outline_missing_text_observation(tmp_path: Path):
    session = ChatSession(
        id="s-ol",
        phase=ChatPhase.FREEFORM_ASK.value,
        freeform={"skip_blueprint": True, "meta_confirmed": True, "title": "调研"},
    )
    state = ChatControlState(home=tmp_path, session=session)
    tools = {t.name: t for t in build_chat_tools(state)}
    raw = tools["submit_outline"].invoke({})
    data = json.loads(raw)
    assert data.get("ok") is False
    assert "outline_text" in (data.get("missing_slots") or [])
    assert "regenerate" in (data.get("observation") or "")


def test_generate_freeform_points_needs_outline_task(tmp_path: Path):
    session = ChatSession(
        id="s-pts",
        phase=ChatPhase.FREEFORM_ASK.value,
        freeform={"skip_blueprint": True},
    )
    state = ChatControlState(home=tmp_path, session=session)
    tools = {t.name: t for t in build_chat_tools(state)}
    raw = tools["generate_freeform_points"].invoke({})
    data = json.loads(raw)
    assert data.get("ok") is False
    assert "task_id" in (data.get("missing_slots") or [])


def test_configure_chapter_missing_key(tmp_path: Path):
    session = _session_named(tmp_path)
    state = ChatControlState(home=tmp_path, session=session)
    tools = {t.name: t for t in build_chat_tools(state)}
    raw = tools["configure_chapter"].invoke({"payload_json": "{}"})
    data = json.loads(raw)
    assert data.get("ok") is False
    assert "chapter_key" in (data.get("missing_slots") or [])


def test_oral_entry_skips_domain_claim_modules():
    """口语主路径模块不得再导出 try_* 认领入口。"""
    import report_writing.chat.intent_dispatch as intent_dispatch
    import report_writing.chat.progress_continue as progress_continue
    import report_writing.chat.writing_control_route as writing_control_route

    assert not hasattr(intent_dispatch, "try_intent_dispatch_route")
    assert not hasattr(progress_continue, "try_progress_continue_clarify")
    assert not hasattr(writing_control_route, "try_writing_control_route")
    assert hasattr(intent_dispatch, "execute_intent_plan")
    assert hasattr(writing_control_route, "apply_writing_control_classification")


class _FinishOnlyModel(ScriptedChatModel):
    role: str = "finish_only"

    def decide(self, messages: list[BaseMessage]) -> AIMessage:
        del messages
        return self.finish("好的，我继续。")


def test_run_chat_control_ignores_intent_plan_flag(tmp_path: Path, monkeypatch):
    """即使打开遗留开关，口语也不走 JSON 计划。"""
    monkeypatch.setenv("REPORT_WRITING_INTENT_PLAN", "1")
    from report_writing.infra import config_settings

    config_settings._application.cache_clear()
    assert config_settings.intent_plan_enabled() is True

    def _boom(*_a, **_k):
        raise AssertionError("run_chat_control must not call try_run_oral_multi_intent")

    monkeypatch.setattr(
        "report_writing.chat.intent_multi_plan.try_run_oral_multi_intent",
        _boom,
    )

    session = _session_named(tmp_path)
    state = ChatControlState(home=tmp_path, session=session)
    user_text = json.dumps(
        {
            "action": "",
            "message": "把第二章写一下",
            "session": {"phase": session.phase, "task_id": session.task_id},
        },
        ensure_ascii=False,
    )
    reply = run_chat_control(
        model=_FinishOnlyModel(),
        state=state,
        user_text=user_text,
        system_prompt="",
    )
    assert "好的" in (reply or "")
    config_settings._application.cache_clear()


def test_clarify_llm_reexports_narrow_classifiers():
    from report_writing.chat import clarify_llm, intent_llm

    assert clarify_llm.classify_clarify_reply_llm is intent_llm.classify_clarify_reply_llm
    assert not hasattr(clarify_llm, "classify_turn_plan_llm")


def test_run_chat_control_does_not_auto_dispatch_onboarding(tmp_path: Path, monkeypatch):
    def _boom(*_a, **_k):
        raise AssertionError("oral path must not auto-dispatch suggested_next_tools")

    monkeypatch.setattr(
        "report_writing.chat.intent_clarify.dispatch_suggested_onboarding_tools",
        _boom,
    )
    session = _session_named(tmp_path)
    session.freeform = {
        **dict(session.freeform or {}),
        "suggested_next_tools": [{"tool": "match_blueprints", "args": {}}],
    }
    state = ChatControlState(home=tmp_path, session=session)
    user_text = json.dumps(
        {
            "action": "",
            "message": "继续准备",
            "session": {"phase": session.phase, "task_id": session.task_id},
        },
        ensure_ascii=False,
    )
    reply = run_chat_control(
        model=_FinishOnlyModel(),
        state=state,
        user_text=user_text,
        system_prompt="",
    )
    assert "好的" in (reply or "")
    assert (session.freeform or {}).get("suggested_next_tools")


def test_agent_user_text_exposes_suggested_next_tools(tmp_path: Path):
    from report_writing.agents.chat_control import build_agent_user_text

    session = _session_named(tmp_path)
    session.freeform = {
        **dict(session.freeform or {}),
        "suggested_next_tools": [{"tool": "match_blueprints", "args": {}}],
    }
    body = json.loads(
        build_agent_user_text(
            action="",
            payload={},
            message="帮我写一份安全报告",
            session=session,
            home=tmp_path,
        )
    )
    assert body.get("suggested_next_tools")
    assert "suggested_next_tools" in str(body.get("routing_hint") or "")
