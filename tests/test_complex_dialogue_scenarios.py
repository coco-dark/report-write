"""复杂业务对话场景骨架：多文档隔离、目录修订、收录/同步调度、约束进简报。

不 mock 全链路 LLM 多轮；断言关键状态与 Tool 调度面，便于后续扩成 golden path。
"""

from __future__ import annotations

from pathlib import Path

from report_writing.chat.tool_dispatch import DISPATCH_TOOLS, execute_dispatch_tool
from report_writing.chat.session_documents import (
    confirm_writing_document_name,
    ensure_named_freeform_document,
    session_documents_map,
)
from report_writing.chat.writing_brief import get_writing_brief, merge_brief_patch, writing_brief_prompt_block
from report_writing.domain.models import ChatPhase, ChatSession


def test_scenario_new_doc_isolation_preserves_first_title(tmp_path: Path):
    home = tmp_path
    (home / "tasks").mkdir(parents=True)
    session = ChatSession(id="sc_iso", phase=ChatPhase.FREEFORM_OUTLINE.value)
    tid1 = ensure_named_freeform_document(
        session, home, title="工程安全报告", category="安全报告"
    )
    confirm_writing_document_name(
        session, "季度述职报告", home=home, category="述职报告", message="另写一份述职"
    )
    tid2 = ensure_named_freeform_document(
        session, home, title="季度述职报告", category="述职报告"
    )
    docs = session_documents_map(session)
    assert tid1 != tid2
    assert docs[f"task:{tid1}"]["title"] == "工程安全报告"
    assert docs[f"task:{tid2}"]["title"] == "季度述职报告"


def test_scenario_outline_revision_constraints_persist(tmp_path: Path):
    from report_writing.chat.actions import _collect_freeform_outline

    home = tmp_path
    (home / "tasks").mkdir(parents=True)
    session = ChatSession(
        id="sc_rev",
        phase=ChatPhase.FREEFORM_OUTLINE.value,
        freeform={
            "meta_confirmed": True,
            "writing_doc_named": True,
            "writing_path": "freeform",
            "category": "述职报告",
            "title": "季度述职",
            "outline_text": "1 概述\n2 工作\n3 计划",
        },
    )
    ensure_named_freeform_document(session, home, title="季度述职", category="述职报告")

    def invoke(system: str, user: str) -> str:
        if "动作分类" in system or '"intent"' in system:
            return '{"intent":"generate_outline","confidence":0.95}'
        assert "修改要求" in user
        return "1 概述\n1.1 a\n1.2 b\n1.3 c\n2 工作\n2.1 a\n2.2 b\n2.3 c"

    _collect_freeform_outline(
        home,
        session,
        "每个点下面有三小点",
        invoke=invoke,
        regenerate=True,
        revision_instructions="每个点下面有三小点",
    )
    assert "三小点" in str((session.freeform or {}).get("outline_constraints") or "")


def test_scenario_save_and_sync_tools_in_dispatch():
    assert "save_freeform_as_blueprint" in DISPATCH_TOOLS
    assert "sync_document_to_blueprint_library" in DISPATCH_TOOLS
    assert "confirm_chapter" in DISPATCH_TOOLS
    assert "start_writing" in DISPATCH_TOOLS
    assert "set_session_meta" in DISPATCH_TOOLS
    assert "match_blueprints" in DISPATCH_TOOLS


def test_scenario_user_constraints_in_writing_brief_prompt():
    session = ChatSession(
        id="sc_brief",
        freeform={"title": "测试", "meta_confirmed": True},
    )
    merge_brief_patch(session, {"user_constraints": "语气正式；每章先写结论"})
    block = writing_brief_prompt_block(session)
    assert "用户约束" in block
    assert "语气正式" in block
    brief = get_writing_brief(session)
    assert "语气正式" in str(brief.get("user_constraints") or "")


def test_scenario_dispatch_save_blueprint_missing_task_raises(tmp_path: Path):
    session = ChatSession(id="sc_save", task_id="")
    try:
        execute_dispatch_tool(
            tmp_path,
            session,
            {"tool": "save_freeform_as_blueprint", "args": {}},
            "收录到蓝本库",
        )
        raised = False
    except ValueError as err:
        raised = True
        assert "写作任务" in str(err) or "任务" in str(err)
    assert raised


def test_scenario_generate_outline_asks_entry_not_silent_wizard(tmp_path: Path):
    """已定名但未选路径时「生成目录」应入口三选，不得静默 begin_blueprint_wizard。"""
    from report_writing.chat.clarify_flow import build_entry_path_clarify, get_pending

    home = tmp_path
    (home / "tasks").mkdir(parents=True)
    session = ChatSession(
        id="sc_outline_path",
        phase=ChatPhase.INTENT.value,
        freeform={
            "writing_doc_named": True,
            "meta_confirmed": True,
            "title": "2026季度述职",
        },
    )

    def invoke(system: str, user: str) -> str:
        # 生成目录 → freeform intent；入口路由若被问到也必须 none
        if "入口意图" in system:
            return '{"intent":"create_blueprint","confidence":0.9,"blueprint_title":"述职"}'
        if "动作分类" in system or "generate_outline" in system or '"intent"' in system:
            return '{"intent":"generate_outline","confidence":0.95}'
        return "{}"

    reply, cards = build_entry_path_clarify(
        session,
        message="生成目录",
        title_hint="2026季度述职",
        invoke=invoke,
        home=home,
    )
    pending = get_pending(session)
    assert pending and pending.get("source") in {"entry", "entry_branch", "entry_freeform_mode"}
    assert any(getattr(c, "type", "") == "intent_clarify" for c in cards)
    assert not any(getattr(c, "type", "") == "blueprint_wizard" for c in cards)
    assert "自动" in reply or "样本" in reply or "怎么开始" in reply or "蓝本" in reply or "自己" in reply


def test_scenario_zai_gei_wo_xie_fen_is_force_new():
    from report_writing.chat.session_documents import message_wants_new_document_strong

    assert message_wants_new_document_strong("再给我写份述职报告")
    assert message_wants_new_document_strong("再写份述职报告")
