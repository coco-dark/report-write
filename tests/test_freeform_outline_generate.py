"""无蓝本：生成可编辑大纲。"""

from __future__ import annotations

import json
from pathlib import Path

from report_writing.chat.actions import _collect_freeform_outline
from report_writing.domain.models import ChatPhase, ChatSession


def test_generate_outline_returns_editable_preview(tmp_path: Path):
    session = ChatSession(
        id="s1",
        phase=ChatPhase.FREEFORM_OUTLINE.value,
        freeform={
            "meta_confirmed": True,
            "category": "工作说明",
            "title": "某某项目工作说明",
        },
    )

    def invoke(system: str, user: str) -> str:
        # 分类器要 JSON；目录正文生成要纯文本；确认/要点另判
        if "动作分类" in system or "无蓝本写作流程" in system or '"intent"' in system:
            u = user or ""
            if "确认" in u or "1 总则" in u or "1.1" in u:
                return json.dumps({"intent": "confirm_outline", "confidence": 0.95}, ensure_ascii=False)
            if "要点" in u or "开写" in u:
                return json.dumps({"intent": "generate_points", "confidence": 0.95}, ensure_ascii=False)
            return json.dumps({"intent": "generate_outline", "confidence": 0.95}, ensure_ascii=False)
        if "要点" in system or "写作信息" in system or "points" in system.lower():
            return json.dumps(
                [
                    {"key": "c1", "points": ["目的"], "describes": "总则", "keywords": ["目的"]},
                    {"key": "c2", "points": ["职责"], "describes": "内容", "keywords": ["职责"]},
                ],
                ensure_ascii=False,
            )
        return "1 总则\n1.1 目的\n2 工作内容\n2.1 岗位职责"

    reply, cards = _collect_freeform_outline(
        tmp_path,
        session,
        "你帮我生成目录大纲",
        invoke=invoke,
        regenerate=True,
    )
    assert (
        "草案" in reply
        or "生成" in reply
        or "起草" in reply
        or "拓扑" in reply
        or "搭了" in reply
        or "确认大纲" in reply
        or "目录" in reply
    )
    assert session.phase == ChatPhase.FREEFORM_OUTLINE.value
    assert session.freeform.get("outline_pending_edit") is True
    preview = next(c for c in cards if c.type == "outline_preview")
    assert preview.payload.get("editable") is True
    assert "1.1" in preview.payload.get("outline_text", "")

    reply2, cards2 = _collect_freeform_outline(
        tmp_path,
        session,
        preview.payload["outline_text"],
        invoke=invoke,
        confirm=True,
    )
    # 确认大纲后若有写作模型会自动生成要点并进入可开写；否则停在要点阶段
    assert session.phase in {
        ChatPhase.FREEFORM_POINTS.value,
        ChatPhase.READY_TO_WRITE.value,
    }
    assert any(
        c.type in {"freeform_points_help", "writing_pace", "outline_preview", "progress"}
        for c in cards2
    )


def test_regenerate_outline_passes_revision_and_nests_subsections(tmp_path: Path):
    """重新生成须把「每章三小节」写入 LLM 提示，且结果含 1.1～1.3 嵌套。"""
    from report_writing.chat.session_documents import ensure_named_freeform_document

    home = tmp_path
    (home / "tasks").mkdir(parents=True)
    session = ChatSession(
        id="s_rev",
        phase=ChatPhase.FREEFORM_OUTLINE.value,
        freeform={
            "meta_confirmed": True,
            "writing_doc_named": True,
            "writing_path": "freeform",
            "category": "述职报告",
            "title": "2026季度述职报告",
            "outline_text": "1 个人岗位\n2 季度工作\n3 下步计划",
            "outline_pending_edit": True,
        },
    )
    ensure_named_freeform_document(
        session, home, title="2026季度述职报告", category="述职报告"
    )

    seen: dict[str, str] = {}

    def invoke(system: str, user: str) -> str:
        if "动作分类" in system or '"intent"' in system:
            return json.dumps({"intent": "generate_outline", "confidence": 0.95}, ensure_ascii=False)
        seen["system"] = system
        seen["user"] = user
        assert "修改要求" in user or "三小" in user or "小节" in user
        assert "当前目录" in user or "1 个人岗位" in user
        return (
            "1 个人岗位\n"
            "1.1 职责概览\n"
            "1.2 能力要求\n"
            "1.3 协作关系\n"
            "2 季度工作\n"
            "2.1 重点项目\n"
            "2.2 日常履职\n"
            "2.3 亮点成果\n"
            "3 下步计划\n"
            "3.1 目标\n"
            "3.2 举措\n"
            "3.3 风险"
        )

    reply, cards = _collect_freeform_outline(
        home,
        session,
        "重新生成 每个点下面有三小点 你帮我生成",
        invoke=invoke,
        regenerate=True,
        revision_instructions="重新生成 每个点下面有三小点 你帮我生成",
    )
    assert "修改要求" in seen.get("user", "") or "三小" in seen.get("user", "")
    assert session.freeform.get("outline_constraints")
    preview = next(c for c in cards if c.type == "outline_preview")
    body = str(preview.payload.get("outline_text") or "")
    assert "1.1" in body and "1.2" in body and "1.3" in body
    assert "2.1" in body and "3.3" in body
    assert reply
    # regenerate 须复用定名 task，不得再 mint 同标题第二份
    from report_writing.chat.session_documents import session_documents_map

    tid1 = str(session.task_id or "").strip()
    assert tid1
    docs1 = session_documents_map(session)
    assert len([k for k in docs1 if str(k).startswith("task:")]) == 1

    reply2, _cards2 = _collect_freeform_outline(
        home,
        session,
        "每个章节5个小点",
        invoke=invoke,
        regenerate=True,
        revision_instructions="每个章节5个小点",
    )
    assert reply2
    assert str(session.task_id or "").strip() == tid1
    docs2 = session_documents_map(session)
    assert len([k for k in docs2 if str(k).startswith("task:")]) == 1
    assert docs2[f"task:{tid1}"]["title"] == "2026季度述职报告"


def test_first_outline_reuses_named_shell_and_prunes_duplicate(tmp_path: Path):
    """定名壳 + 首次生成目录：复用同一 w*；人为多登记同标题时 prune。"""
    from report_writing.chat.session_documents import (
        ensure_named_freeform_document,
        session_documents_map,
        upsert_session_document,
    )

    home = tmp_path
    (home / "tasks").mkdir(parents=True)
    session = ChatSession(
        id="s_one_doc",
        phase=ChatPhase.FREEFORM_OUTLINE.value,
        freeform={"meta_confirmed": True, "writing_doc_named": True, "writing_path": "freeform"},
    )
    tid = ensure_named_freeform_document(session, home, title="1101述职报告", category="述职")
    assert tid
    # 模拟历史误开的第二份同标题空壳
    upsert_session_document(
        session, kind="writing", task_id="w999oldshell", title="1101述职报告", clear_blueprint_id=True
    )
    assert len(session_documents_map(session)) >= 2

    def invoke(system: str, user: str) -> str:
        if "动作分类" in system or '"intent"' in system:
            return json.dumps({"intent": "generate_outline", "confidence": 0.95}, ensure_ascii=False)
        return "1 概述\n1.1 a\n2 工作\n2.1 b"

    _collect_freeform_outline(
        home,
        session,
        "帮我生成目录",
        invoke=invoke,
        regenerate=True,
    )
    assert session.task_id == tid
    docs = session_documents_map(session)
    task_keys = [k for k in docs if str(k).startswith("task:")]
    assert task_keys == [f"task:{tid}"]


def test_generate_directory_keeps_chapter_count_constraint(tmp_path: Path):
    from report_writing.chat.freeform_outline import outline_top_chapter_count
    from report_writing.chat.session_documents import ensure_named_freeform_document

    assert outline_top_chapter_count("再给我写一份述职报告 三个章节") == 3
    assert outline_top_chapter_count("作lzb述职报告 自动生成章节目录 三个大章节就可以") == 3
    assert outline_top_chapter_count("生成目录") == 0
    assert outline_top_chapter_count("学习第3章") == 0

    home = tmp_path
    (home / "tasks").mkdir(parents=True)
    session = ChatSession(
        id="s_three",
        phase=ChatPhase.FREEFORM_OUTLINE.value,
        freeform={
            "meta_confirmed": True,
            "writing_doc_named": True,
            "writing_path": "freeform",
            "category": "述职报告",
            "title": "lzb述职报告",
            "outline_constraints": "再给我写一份述职报告 三个章节 名字叫做lzb述职报告",
        },
    )
    ensure_named_freeform_document(session, home, title="lzb述职报告", category="述职报告")

    def invoke(system: str, user: str) -> str:
        if "动作分类" in system or '"intent"' in system:
            return json.dumps({"intent": "generate_outline", "confidence": 0.95}, ensure_ascii=False)
        assert "三个章节" in user or "一级章数量必须为 3" in system
        return (
            "1 履职概况\n1.1 a\n2 重点工作\n2.1 b\n3 能力建设\n3.1 c\n"
            "4 存在问题\n4.1 d\n5 下一步\n5.1 e\n6 总结\n6.1 f"
        )

    _collect_freeform_outline(
        home,
        session,
        "生成目录",
        invoke=invoke,
        regenerate=True,
    )
    constraints = str((session.freeform or {}).get("outline_constraints") or "")
    assert "三个章节" in constraints
    assert constraints.strip() != "生成目录"
    outline = str((session.freeform or {}).get("outline_text") or "")
    assert "1 履职概况" in outline
    assert "3 能力建设" in outline
    assert "4 存在问题" not in outline
    assert "6 总结" not in outline


def test_collect_freeform_meta_strips_multi_action_sentence(tmp_path: Path):
    from report_writing.chat.actions import _collect_freeform_meta, _freeform_outline_doc_title
    from report_writing.chat.freeform_outline import outline_top_chapter_count

    (tmp_path / "tasks").mkdir(parents=True)
    session = ChatSession(id="s_multi", phase=ChatPhase.FREEFORM_ASK.value)
    msg = "作lzb述职报告 自动生成章节目录 三个大章节就可以"

    def invoke(system: str, user: str) -> str:
        if "动作分类" in system or "无蓝本写作流程" in system or '"intent"' in system:
            return json.dumps({"intent": "generate_outline", "confidence": 0.95}, ensure_ascii=False)
        if "has_explicit_name" in system:
            return json.dumps(
                {
                    "has_explicit_name": False,
                    "title": "",
                    "residual": msg,
                    "confidence": 0.9,
                },
                ensure_ascii=False,
            )
        if "doc_title" in system or "题目抽取" in system:
            return json.dumps({"doc_title": "lzb述职报告", "confidence": 0.9}, ensure_ascii=False)
        return (
            "1 履职概况\n1.1 a\n2 重点工作\n2.1 b\n3 能力建设\n3.1 c\n"
            "4 存在问题\n4.1 d\n5 下一步\n5.1 e\n6 总结\n6.1 f"
        )

    reply, _cards = _collect_freeform_meta(session, msg, home=tmp_path, invoke=invoke)
    title = str((session.freeform or {}).get("title") or "")
    assert title == "lzb述职报告"
    assert "自动生成" not in title
    assert "三个大章节" not in title
    assert "自动生成章节目录" not in (reply or "")
    constraints = str((session.freeform or {}).get("outline_constraints") or "")
    assert outline_top_chapter_count(constraints) == 3
    assert _freeform_outline_doc_title(session, None) == "lzb述职报告"


def test_collect_outline_does_not_infer_generate_without_flag(tmp_path: Path):
    session = ChatSession(
        id="s_no_infer",
        phase=ChatPhase.FREEFORM_OUTLINE.value,
        freeform={
            "meta_confirmed": True,
            "category": "工作说明",
            "title": "某某项目工作说明",
        },
    )

    def invoke(_s: str, _u: str) -> str:
        return json.dumps({"intent": "generate_outline", "confidence": 0.95}, ensure_ascii=False)

    reply, cards = _collect_freeform_outline(
        tmp_path,
        session,
        "帮我生成目录大纲",
        invoke=invoke,
    )
    assert not any(c.type == "outline_preview" for c in cards)
    assert session.freeform.get("outline_pending_edit") is not True
    assert "目录" in (reply or "") or "粘贴" in (reply or "")


def test_resolve_outline_mode_flags_fills_confirm_and_generate():
    from report_writing.chat.freeform_intent import resolve_outline_mode_flags

    session = ChatSession(
        id="s_flags",
        phase=ChatPhase.FREEFORM_OUTLINE.value,
        freeform={"outline_pending_edit": True, "outline_text": "1 总则\n2 正文"},
    )
    assert resolve_outline_mode_flags("帮我生成目录", invoke=None) == (False, True)
    assert resolve_outline_mode_flags("确认大纲", invoke=None) == (True, False)
    assert resolve_outline_mode_flags("继续", session=session, invoke=None) == (True, False)
    assert resolve_outline_mode_flags(
        "帮我生成目录", confirm=True, regenerate=True, invoke=None
    ) == (True, False)
