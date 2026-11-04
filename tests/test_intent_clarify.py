"""全局意图澄清内核与学习范围门闩。"""

from __future__ import annotations

from pathlib import Path

from report_writing.chat.intent_clarify import (
    apply_safe_default,
    begin_clarify,
    build_entry_path_clarify,
    build_freeform_next_clarify,
    build_study_scope_clarify,
    build_writing_decision_clarify,
    clear_pending,
    execute_clarify_spec,
    get_pending,
    looks_like_new_intent,
    merge_study_message_with_scope,
    message_skips_clarify,
    resolve_clarify,
    resolve_clear_entry_tool,
    study_scope_needs_clarify,
    try_handle_pending_message,
)
from report_writing.domain.models import ChatPhase, ChatSession, OutlineNode, OutlineTree, WritingTask


def _session() -> ChatSession:
    return ChatSession(id="s1", freeform={})


def _task_with_children() -> WritingTask:
    return WritingTask(
        id="bp1",
        outline=OutlineTree(
            chapters=[
                OutlineNode(key="c2", sequence="2", title="工程概况"),
                OutlineNode(key="c21", sequence="2.1", title="站址"),
                OutlineNode(key="c22", sequence="2.2", title="规模"),
            ]
        ),
    )


def test_begin_and_resolve_by_option_id():
    session = _session()
    reply, cards = begin_clarify(
        session,
        source="wizard",
        question="整章还是子章节？",
        options=[
            {"id": "whole", "label": "分析整章（含下属）", "slots": {"study_scope": "whole"}},
            {"id": "children", "label": "只要子章节", "slots": {"study_scope": "children"}},
        ],
        candidate={"kind": "wizard_command", "name": "re_study_chapters"},
    )
    assert "整章" in reply
    assert cards and cards[0].type == "intent_clarify"
    pending = get_pending(session)
    assert pending and pending["id"]
    hit = resolve_clarify(session, "whole")
    assert hit is not None
    assert hit["option_id"] == "whole"
    assert hit["slots"]["study_scope"] == "whole"
    assert get_pending(session) is None


def test_resolve_by_alias_label():
    session = _session()
    build_study_scope_clarify(session, sequences=["2"], study_message="学习第2章")
    hit = try_handle_pending_message(session, "只要子章节")
    assert isinstance(hit, dict)
    assert hit["slots"]["study_scope"] == "children"


def test_resolve_self_not_children_when_negating_subchapters():
    session = _session()
    build_study_scope_clarify(session, sequences=["1"], study_message="学习第1章")
    hit = try_handle_pending_message(session, "学习第一章就够了 不要子章节")
    assert isinstance(hit, dict)
    assert hit["slots"]["study_scope"] == "self"


def test_new_intent_discards_pending():
    session = _session()
    build_entry_path_clarify(session, message="写报告")
    # 「写报告」过泛：可能挂起名/选路，也可能只给工作名后不挂卡
    pending0 = get_pending(session)
    if pending0 is None:
        from report_writing.chat.intent_clarify import begin_clarify

        begin_clarify(
            session,
            source="control",
            question="要导出 Word 吗？",
            options=[{"id": "export", "label": "导出"}],
            candidate={"kind": "control_tool", "name": "prepare_export_word"},
        )
    assert get_pending(session) is not None
    assert looks_like_new_intent("导出 Word")
    hit = try_handle_pending_message(session, "导出 Word")
    assert hit is None
    assert get_pending(session) is None


def test_pending_study_scope_does_not_swallow_add_element():
    """加要素是新意图：澄清 LLM 判 unclear / 无 invoke 时清 pending；不再靠正则抢先。"""
    session = _session()
    msg = "为2.2添加知识库要素 关联安全 检索词为建设规模、站址、工程总投资"

    def _unclear(_system: str, _user: str) -> str:
        return '{"decision":"unclear","option_id":"","question":"","confidence":0.2}'

    def _new_intent(_system: str, _user: str) -> str:
        return '{"decision":"new_intent","option_id":"","question":"","confidence":0.9}'

    build_study_scope_clarify(session, sequences=["2"], study_message="学习第2章")
    hit = try_handle_pending_message(session, msg, invoke=_unclear)
    assert hit is None
    assert get_pending(session) is None

    build_study_scope_clarify(session, sequences=["2"], study_message="学习第2章")
    hit2 = try_handle_pending_message(session, msg, invoke=_new_intent)
    assert hit2 is None
    assert get_pending(session) is None

    build_study_scope_clarify(session, sequences=["2"], study_message="学习第2章")
    hit3 = try_handle_pending_message(session, msg)
    assert hit3 is None
    assert get_pending(session) is None


def test_paraphrase_create_blueprint_via_llm(tmp_path: Path):
    from report_writing.chat.session_documents import confirm_writing_document_name

    session = _session()
    confirm_writing_document_name(session, "安全报告", message="帮我写一份安全报告")
    build_entry_path_clarify(session, message="帮我写一份安全报告")

    hit_branch = try_handle_pending_message(session, "无蓝本 · 自己写")
    assert isinstance(hit_branch, dict)
    execute_clarify_spec(tmp_path, session, hit_branch, message="无蓝本 · 自己写")
    assert get_pending(session) and get_pending(session).get("source") == "entry_freeform_mode"

    def _invoke(system: str, user: str) -> str:
        assert "模版" in user or "create_or_freeform" in user or "依据" in user
        return (
            '{"decision":"pick","option_id":"template",'
            '"question":"","confidence":0.9}'
        )

    hit = try_handle_pending_message(session, "创建新的蓝本", invoke=_invoke)
    assert isinstance(hit, dict)
    assert hit["option_id"] == "template"
    assert hit["slots"]["entry_path"] in {"create_blueprint", "template"}
    assert get_pending(session) is None


def test_llm_followup_question_then_default():
    from report_writing.chat.session_documents import confirm_writing_document_name

    session = _session()
    confirm_writing_document_name(session, "报告", message="写报告")
    build_entry_path_clarify(session, message="写报告")

    def _unclear(system: str, user: str) -> str:
        return (
            '{"decision":"unclear","option_id":"",'
            '"question":"你是想用现成蓝本，还是自己建一份？","confidence":0.4}'
        )

    first = try_handle_pending_message(session, "都行吧", invoke=_unclear)
    assert first == "你是想用现成蓝本，还是自己建一份？"
    assert get_pending(session) is not None
    second = try_handle_pending_message(session, "随便", invoke=_unclear)
    assert isinstance(second, dict)
    assert second["defaulted"] is True


def test_safe_default_after_second_miss():
    session = _session()
    build_study_scope_clarify(session, sequences=["2"], study_message="学习第2章")
    first = try_handle_pending_message(session, "嗯？")
    assert isinstance(first, str)
    assert get_pending(session) is not None
    second = try_handle_pending_message(session, "随便吧")
    assert isinstance(second, dict)
    assert second["defaulted"] is True
    assert second["slots"]["study_scope"] == "whole"
    assert get_pending(session) is None


def test_apply_safe_default_writing():
    session = _session()
    build_writing_decision_clarify(session, task_id="t1", chapter_key="c1")
    hit = apply_safe_default(session)
    assert hit and hit["slots"]["writing_decision"] == "approve"
    assert get_pending(session) is None


def test_study_scope_needs_clarify_when_children_exist():
    task = _task_with_children()
    assert study_scope_needs_clarify(task, "学习第2章", ["2"]) is True
    assert study_scope_needs_clarify(task, "学习第2章", ["2"], resolved_scope="whole") is False
    assert study_scope_needs_clarify(task, "学习第2章", ["2"], resolved_scope="children") is False
    assert study_scope_needs_clarify(task, "学习第2章", ["2"], resolved_scope="self") is False


def test_study_scope_no_clarify_without_children():
    task = WritingTask(
        id="bp1",
        outline=OutlineTree(
            chapters=[OutlineNode(key="c3", sequence="3", title="结论")]
        ),
    )
    assert study_scope_needs_clarify(task, "学习第3章", ["3"]) is False


def test_message_skips_clarify_only_meta_line():
    assert not message_skips_clarify("确认目录", source="wizard")
    assert not message_skips_clarify("选用这个蓝本", source="entry")
    assert not message_skips_clarify("通过本章", source="writing")
    assert message_skips_clarify("工作说明｜对话操作冒烟报告", source="entry")


def test_merge_study_message_with_scope():
    assert "整章" in merge_study_message_with_scope("学习第2章", "whole")
    assert "子章节" in merge_study_message_with_scope("学习第2章", "children")
    # 已明示则不重复追加
    assert merge_study_message_with_scope("学习第2章整章", "whole") == "学习第2章整章"


def test_resolve_clear_entry_tool_structural_only():
    assert resolve_clear_entry_tool("不用蓝本") is None
    assert resolve_clear_entry_tool("工作说明｜对话操作冒烟报告") == "skip_blueprint"
    assert resolve_clear_entry_tool("帮我新建一个蓝本") is None
    assert resolve_clear_entry_tool("帮我写一份报告") is None


def test_entry_pending_meta_line_resolves_freeform():
    session = _session()
    begin_clarify(
        session,
        source="entry",
        question="怎么走？",
        options=[
            {"id": "match_blueprint", "label": "匹配蓝本", "slots": {"entry_path": "match_blueprint"}},
            {"id": "freeform", "label": "自己定大纲", "slots": {"entry_path": "freeform"}},
        ],
        candidate={"kind": "entry_intent", "name": "entry_path"},
    )
    hit = try_handle_pending_message(session, "工作说明｜对话操作冒烟报告")
    assert isinstance(hit, dict)
    assert hit["slots"].get("entry_path") == "freeform"
    assert get_pending(session) is None


def test_control_pending_skip_phrase_not_misclick_generate_points():
    """id=freeform 实为 generate_points 时，「不用蓝本」别名不得误点；应交意图清 pending。"""
    session = _session()
    begin_clarify(
        session,
        source="control",
        question="你想写的是什么文档？",
        options=[
            {
                "id": "freeform",
                "label": "我直接说主题，帮我搭目录",
                "slots": {"tool": "generate_freeform_points"},
            },
            {
                "id": "explain",
                "label": "先说说不用蓝本怎么个流程",
                "slots": {"tool": "skip_blueprint", "entry_path": "freeform"},
            },
        ],
        candidate={
            "kind": "control_tool",
            "name": "ask_intent",
            "payload": {"message": "嗨，我想写个东西，不太想找蓝本"},
        },
    )
    hit = try_handle_pending_message(session, "不用蓝本，我自己定目录就行")
    # 新意图：清澄清，交回调度（不再用正则硬映射 skip_blueprint）
    assert hit is None
    assert get_pending(session) is None


def test_clear_pending():
    session = _session()
    begin_clarify(
        session,
        source="entry",
        question="q",
        options=[{"id": "a", "label": "A"}],
        candidate={},
    )
    clear_pending(session)
    assert get_pending(session) is None


def test_freeform_clarify_and_resolve():
    session = _session()
    session.phase = ChatPhase.FREEFORM_OUTLINE.value
    reply, cards = build_freeform_next_clarify(
        session, phase=ChatPhase.FREEFORM_OUTLINE.value, message="继续"
    )
    assert "目录" in reply
    assert cards and cards[0].type == "intent_clarify"
    hit = try_handle_pending_message(session, "确认目录")
    assert isinstance(hit, dict)
    assert hit["slots"]["freeform_intent"] == "confirm_outline"


def test_entry_and_writing_builders_have_options():
    from report_writing.chat.session_documents import confirm_writing_document_name, is_writing_document_named

    session = _session()
    # 「写一份××」只是题材：先定名，确认后再选主路
    _, entry_cards = build_entry_path_clarify(session, message="帮我写一份安全报告")
    assert entry_cards[0].payload.get("options")
    assert get_pending(session).get("source") in {"doc_name", "entry_branch"}
    assert not is_writing_document_named(session)
    clear_pending(session)
    confirm_writing_document_name(session, "安全报告", message="帮我写一份安全报告")
    _, entry_cards2 = build_entry_path_clarify(session, message="帮我写一份安全报告")
    assert get_pending(session).get("source") == "entry_branch"
    assert len(entry_cards2[0].payload["options"]) >= 2
    clear_pending(session)
    _, writing_cards = build_writing_decision_clarify(session, task_id="t1", chapter_key="c1")
    assert {o["id"] for o in writing_cards[0].payload["options"]} >= {"approve", "revise", "skip"}


def test_ask_intent_llm_options_enrich_freeform_slots():
    """截图复现：大脑生成「不用蓝本，直接搭目录写」但未填 tool → 点选后应走到 skip_blueprint。"""
    from report_writing.chat.intent_clarify import (
        begin_clarify,
        enrich_control_option_slots,
        execute_clarify_spec,
    )

    slots = enrich_control_option_slots(
        "freeform",
        "不用蓝本，直接搭目录写",
        {},
    )
    assert slots.get("entry_path") == "freeform"
    assert slots.get("tool") == "skip_blueprint"

    session = _session()
    begin_clarify(
        session,
        source="control",
        question="想怎么写这份述职报告？",
        options=[
            {"id": "match", "label": "从蓝本库里找述职报告模板", "slots": {}},
            {"id": "create", "label": "新建一份述职报告蓝本", "slots": {}},
            {"id": "freeform", "label": "不用蓝本，直接搭目录写", "slots": {}},
        ],
        candidate={
            "kind": "control_tool",
            "name": "ask_intent",
            "payload": {"message": "写一份述职报告"},
        },
    )
    hit = try_handle_pending_message(session, "不用蓝本，直接搭目录写")
    assert isinstance(hit, dict)
    assert hit["slots"].get("tool") == "skip_blueprint"
    assert hit["slots"].get("entry_path") == "freeform"
    # 明确不用蓝本时优先走 entry_intent，避免误点 id=freeform 的错误 tool
    assert hit["candidate"].get("kind") in {"entry_intent", "control_tool"}

    calls: list[str] = []

    def _fake_dispatch(home, sess, plan, text, emit=None, invoke=None):
        calls.append(str((plan or {}).get("tool") or ""))
        return "行，不用蓝本。接下来起草目录。", [], []

    def _fake_blueprint(home, sess, action, payload, emit=None, invoke=None):
        calls.append(str(action or ""))
        return "行，不用蓝本。接下来起草目录。", []

    import report_writing.chat.intent_dispatch as dispatch_mod
    import report_writing.chat.blueprint_actions as bp_mod

    orig = dispatch_mod.execute_dispatch_tool
    orig_bp = bp_mod.run_blueprint_action
    dispatch_mod.execute_dispatch_tool = _fake_dispatch  # type: ignore[assignment]
    bp_mod.run_blueprint_action = _fake_blueprint  # type: ignore[assignment]
    try:
        reply, _cards, _ = execute_clarify_spec(
            None,  # type: ignore[arg-type]
            session,
            hit,
            message="不用蓝本，直接搭目录写",
        )
    finally:
        dispatch_mod.execute_dispatch_tool = orig  # type: ignore[assignment]
        bp_mod.run_blueprint_action = orig_bp  # type: ignore[assignment]
    assert "skip_blueprint" in calls
    assert "再说一下想做什么" not in reply
