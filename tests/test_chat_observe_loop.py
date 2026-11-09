"""口语主路径：ReAct 观察闭环（改名结构化 title、缺参 missing_slots、多跳）。"""

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
    assert "observation" not in data
    assert not str(data.get("reply_hint") or "").strip()
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
    assert "observation" not in data
    assert str(data.get("reply_hint") or "").strip()
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
            assert (
                (data.get("reply_hint") or "").strip()
                or data.get("ok") is False
                or data.get("missing_slots")
                or data.get("needs_doc_name")
            )
            return self.finish(str(data.get("reply_hint") or "好，按无蓝本继续。"))
        return self.call("skip_blueprint", {"message": "不用蓝本"})


def test_react_open_write_multi_hop_reads_observation(tmp_path: Path):
    from report_writing.chat.session_state import user_chose_skip_blueprint

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
                hint = str(data.get("reply_hint") or "")
                if data.get("ok") is False:
                    return self.finish(f"这章还没分析好，我先帮你安排学习。（观察：{hint[:60]}）")
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
    assert not str(data.get("reply_hint") or "").strip()


def test_add_chapter_element_hops_when_classifier_picks_collect(tmp_path: Path):
    session = _session_named(tmp_path)
    session.phase = ChatPhase.WRITING.value

    def invoke(_system: str, _user: str) -> str:
        return json.dumps(
            {
                "tool": "collect_to_element_library",
                "confidence": 0.95,
                "payload": {"element_name": "知识库要素"},
            }
        )

    state = ChatControlState(
        home=tmp_path,
        session=session,
        clarify_invoke=invoke,
        last_user_message="将知识库要素添加到要素库",
    )
    tools = {t.name: t for t in build_chat_tools(state)}
    raw = tools["add_chapter_element"].invoke(
        {"payload_json": json.dumps({"name": "知识库要素", "chapter_key": "c1"})}
    )
    data = json.loads(raw)
    assert data.get("ok") is True
    assert "suggested_next_tool" not in data


def test_retry_chapter_missing_entry_suggests_ask_entry_path(tmp_path: Path):
    session = ChatSession(
        id="s-retry-path",
        phase=ChatPhase.WRITING.value,
        task_id="w20260101140000rty",
    )
    state = ChatControlState(
        home=tmp_path,
        session=session,
        last_user_message="重写这一章",
    )
    tools = {t.name: t for t in build_chat_tools(state)}
    raw = tools["retry_chapter"].invoke({"payload_json": json.dumps({"chapter_key": "c1"})})
    data = json.loads(raw)
    assert data.get("ok") is False
    assert "writing_path" in (data.get("missing_slots") or [])
    assert "suggested_next_tool" not in data
    assert not any(getattr(c, "type", None) == "intent_clarify" for c in (state.cards or []))


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
    assert not str(data.get("reply_hint") or "").strip()


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


def test_get_chapter_info_tool_bound_and_missing_key(tmp_path: Path):
    session = ChatSession(id="s-info-empty")
    state = ChatControlState(home=tmp_path, session=session)
    tools = {t.name: t for t in build_chat_tools(state)}
    assert "get_chapter_info" in tools
    assert "generate_chapter_describes" in tools
    raw = tools["get_chapter_info"].invoke({"payload_json": "{}"})
    data = json.loads(raw)
    assert data.get("ok") is False
    assert "chapter_key" in (data.get("missing_slots") or [])


def test_oral_entry_skips_domain_claim_modules():
    """口语主路径模块不得再导出 try_* 认领入口。"""
    import report_writing.chat.tool_dispatch as intent_dispatch
    import report_writing.chat.progress_continue as progress_continue
    import report_writing.chat.writing_control_route as writing_control_route

    assert not hasattr(intent_dispatch, "try_intent_dispatch_route")
    assert not hasattr(progress_continue, "try_progress_continue_clarify")
    assert not hasattr(writing_control_route, "try_writing_control_route")
    assert not hasattr(intent_dispatch, "execute_intent_plan")
    assert hasattr(writing_control_route, "apply_writing_control_classification")


class _FinishOnlyModel(ScriptedChatModel):
    role: str = "finish_only"

    def decide(self, messages: list[BaseMessage]) -> AIMessage:
        del messages
        return self.finish("好的，我继续。")


def test_run_chat_control_without_intent_multi_plan(tmp_path: Path):
    """口语只走 create_agent；整轮 JSON 计划模块不存在。"""
    import importlib.util

    assert importlib.util.find_spec("report_writing.chat.intent_multi_plan") is None

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


def test_click_action_is_in_agent_user_text(tmp_path: Path):
    from report_writing.agents.chat_control import build_agent_user_text

    session = ChatSession(id="s-click", phase=ChatPhase.INTENT.value)
    body = json.loads(
        build_agent_user_text(
            action="skip_blueprint",
            payload={},
            message="",
            session=session,
            home=tmp_path,
        )
    )
    assert body.get("action") == "skip_blueprint"
    assert "skip_blueprint" in str(body.get("hint") or "")
    assert "suggested_next_step" not in body


def test_handle_chat_click_selects_blueprint_via_agent(tmp_path: Path):
    from report_writing.chat import handle_chat
    from report_writing.controller import ensure_task
    from report_writing.domain.models import ChatRequest, OutlineNode, OutlineTree, WritingTask
    from report_writing.services.robots import create_robot
    from report_writing.services.store import TaskStore

    create_robot(tmp_path, {"id": "r-click", "name": "bot"})
    path = tmp_path / "tasks" / "bp_click"
    ensure_task(path)
    task = TaskStore(path).load()
    task.title = "点击蓝本"
    task.outline = OutlineTree(
        chapters=[OutlineNode(key="c1", title="概况", points=["p"], sequence="1")]
    )
    task.outline_confirmed = True
    TaskStore(path).save(task)
    resp = handle_chat(
        tmp_path,
        ChatRequest(
            message="",
            action="select_blueprint",
            action_payload={"blueprint_id": "bp_click"},
            robot_id="r-click",
        ),
        invoke=lambda _s, _u: "{}",
    )
    from report_writing.services.chat_session import load_or_create

    session = load_or_create(tmp_path, resp.session_id, robot_id="r-click")
    assert session.blueprint_id == "bp_click" or "蓝本" in (resp.reply or "")


def test_clarify_llm_narrow_classifiers_module():
    import inspect

    from report_writing.chat import clarify_llm

    assert inspect.getmodule(clarify_llm.classify_clarify_reply_llm).__name__ == "report_writing.chat.clarify_llm"
    assert not hasattr(clarify_llm, "classify_turn_plan_llm")
    assert not hasattr(clarify_llm, "classify_chat_control_plan_llm")
    assert not hasattr(clarify_llm, "classify_entry_routing_llm")
    assert not hasattr(clarify_llm, "classify_freeform_action_llm")
    assert not hasattr(clarify_llm, "classify_explicit_doc_name_given_llm")
    assert not hasattr(clarify_llm, "extract_write_subject_title_llm")
    assert not hasattr(clarify_llm, "classify_doc_meta_line_llm")
    assert not hasattr(clarify_llm, "pick_blueprint_id_llm")


def test_clarify_flow_and_tool_dispatch_have_no_legacy_shims():
    import importlib.util

    from report_writing.chat import clarify_flow, tool_dispatch

    assert callable(clarify_flow.get_pending)
    assert callable(tool_dispatch.execute_dispatch_tool)
    assert importlib.util.find_spec("report_writing.chat.intent_clarify") is None
    assert importlib.util.find_spec("report_writing.chat.intent_dispatch") is None


def test_run_chat_control_does_not_auto_dispatch_onboarding(tmp_path: Path, monkeypatch):
    def _boom(*_a, **_k):
        raise AssertionError("oral path must not auto-dispatch suggested_next_tools")

    monkeypatch.setattr(
        "report_writing.chat.clarify_flow.dispatch_suggested_onboarding_tools",
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
    assert "suggested_next_step" not in body


def test_materials_pack_and_supplement_tools_are_bound(tmp_path: Path):
    session = ChatSession(id="s-mp-bound")
    state = ChatControlState(home=tmp_path, session=session)
    names = {t.name for t in build_chat_tools(state)}
    assert "materials_pack_skip" in names
    assert "materials_pack_use" in names
    assert "check_writing_supplements" in names
    assert "skip_writing_supplement_check" in names


def test_handle_chat_materials_pack_skip_via_agent(tmp_path: Path):
    from report_writing.chat import handle_chat
    from report_writing.domain.models import ChatRequest, WritingTask
    from report_writing.services.chat_session import load_or_create
    from report_writing.services.store import TaskStore

    TaskStore(tmp_path / "tasks" / "w-mp").save(WritingTask(id="w-mp", title="资料"))
    resp = handle_chat(
        tmp_path,
        ChatRequest(
            action="materials_pack_skip",
            action_payload={"task_id": "w-mp"},
        ),
        invoke=lambda _s, _u: "{}",
    )
    session = load_or_create(tmp_path, resp.session_id)
    skipped = (session.freeform or {}).get("materials_pack_skipped_tasks") or {}
    assert skipped.get("w-mp") or (session.freeform or {}).get("materials_pack_skipped")
    assert "资料包" in (resp.reply or "") or "不用" in (resp.reply or "")


def test_submit_points_suggests_other_tool_without_silent_reroute(tmp_path: Path):
    session = _session_named(tmp_path)
    session.phase = ChatPhase.FREEFORM_POINTS.value

    def invoke(system: str, user: str) -> str:
        if "写作对话总控" in system or "选工具" in system:
            return json.dumps(
                {
                    "tool": "add_chapter_element",
                    "confidence": 0.92,
                    "payload": {"name": "气候", "kind": "kv"},
                },
                ensure_ascii=False,
            )
        return "{}"

    state = ChatControlState(
        home=tmp_path,
        session=session,
        invoke=invoke,
        clarify_invoke=invoke,
    )
    tools = {t.name: t for t in build_chat_tools(state)}
    raw = tools["submit_points"].invoke({"message": "给这章加个气候要素"})
    data = json.loads(raw)
    assert "suggested_next_tool" not in data


def test_submit_points_skip_phrase_does_not_hop(tmp_path: Path):
    """跳过要点由总控改调 skip_freeform_points；submit_points 内不再 LLM 改道。"""
    session = _session_named(tmp_path)
    session.phase = ChatPhase.FREEFORM_POINTS.value
    state = ChatControlState(home=tmp_path, session=session)
    tools = {t.name: t for t in build_chat_tools(state)}
    raw = tools["submit_points"].invoke({"message": "先跳过要点直接开写"})
    data = json.loads(raw)
    assert "suggested_next_tool" not in data


def test_submit_points_without_outline_suggests_submit_outline(tmp_path: Path):
    session = ChatSession(id="s-pts", phase=ChatPhase.FREEFORM_POINTS.value, freeform={})
    state = ChatControlState(home=tmp_path, session=session, invoke=lambda _s, _u: "{}")
    tools = {t.name: t for t in build_chat_tools(state)}
    raw = tools["submit_points"].invoke({"message": "第一章写背景"})
    data = json.loads(raw)
    assert data.get("ok") is False
    assert "outline" in (data.get("missing_slots") or [])
    assert "suggested_next_tool" not in data


def test_final_text_prefers_model_when_doc_name_missing(tmp_path: Path):
    from report_writing.agents.chat_control import _final_text

    session = ChatSession(id="s-final", phase=ChatPhase.FREEFORM_ASK.value)
    state = ChatControlState(home=tmp_path, session=session)
    canned = "先给这份文档起个名字吧。直接说题目就行，口语或「类型｜标题」都可以。"
    oral = "行，不用蓝本。这份打算叫什么？"
    messages = [
        HumanMessage(content="不用蓝本直接写"),
        ToolMessage(
            content=json.dumps(
                {
                    "ok": True,
                    "needs_doc_name": True,
                    "missing_slots": ["title"],
                    "reply_hint": canned,
                },
                ensure_ascii=False,
            ),
            name="skip_blueprint",
            tool_call_id="1",
        ),
        AIMessage(content=oral),
    ]
    assert _final_text(messages, state) == oral


def test_final_text_does_not_read_canned_hint_when_doc_name_missing(tmp_path: Path):
    from report_writing.agents.chat_control import _final_text

    session = ChatSession(id="s-final2", phase=ChatPhase.FREEFORM_ASK.value)
    state = ChatControlState(home=tmp_path, session=session)
    canned = "先给这份文档起个名字吧。直接说题目就行，口语或「类型｜标题」都可以。"
    messages = [
        HumanMessage(content="不用蓝本直接写"),
        ToolMessage(
            content=json.dumps(
                {
                    "ok": True,
                    "needs_doc_name": True,
                    "missing_slots": ["title"],
                    "reply_hint": canned,
                },
                ensure_ascii=False,
            ),
            name="skip_blueprint",
            tool_call_id="1",
        ),
    ]
    assert canned not in (_final_text(messages, state) or "")
