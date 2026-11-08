"""根据 phase、任务进度、卡片状态推测用户下一步（供总控与前端提示）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from report_writing.domain.models import ChapterStatus, ChatPhase, ChatSession


def infer_suggested_next_step(
    session: ChatSession,
    *,
    home: Path | None = None,
    message: str = "",
) -> dict[str, Any]:
    """返回结构化下一步建议（规则为主，不挡总控自行判断）。"""
    phase = str(session.phase or ChatPhase.INTENT.value).strip()
    ff = dict(session.freeform or {})
    blueprint_id = (session.blueprint_id or "").strip()
    task_id = (session.task_id or "").strip()
    from report_writing.chat.session_state import user_chose_skip_blueprint

    out: dict[str, Any] = {
        "label": "",
        "detail": "",
        "priority_tools": [],
        "ui_action": "",
        "confidence": "medium",
    }

    if phase == ChatPhase.INTENT.value:
        from report_writing.chat.session_documents import (
            focus_needs_entry_path,
            is_writing_document_named,
        )

        # 已定名仍缺入口：优先选路，勿再建议 remember_writing_goal
        if is_writing_document_named(session) and focus_needs_entry_path(session):
            return _fill(
                out,
                label="选择写作方式",
                detail=(
                    "文档已定名但缺 writing_path：下一步唯一优先 ask_entry_path；"
                    "用户说「继续/好的」也推进入口澄清，禁止再 remember_writing_goal 只回「记下了」。"
                ),
                tools=["ask_entry_path"],
                confidence="high",
            )
        if user_chose_skip_blueprint(session) or ff.get("writing_path") == "freeform":
            if not ff.get("meta_confirmed") and not (ff.get("category") and ff.get("title")):
                return _fill(
                    out,
                    label="补全要写什么",
                    detail="用户倾向无蓝本：口语问清写什么/叫什么，够了再 submit_freeform_meta；勿强迫「类型｜标题」。",
                    tools=["submit_freeform_meta", "remember_writing_goal"],
                )
            return _fill(
                out,
                label="选写作路径或搭目录",
                detail="已有写作题目：优先 ask_entry_path 观察缺口；用户明确匹配后再 match_blueprints；matched=0 时两步澄清（有蓝本/无蓝本），禁止静默自动无蓝本。有题目则可 submit_outline。",
                tools=["ask_entry_path", "match_blueprints", "submit_outline", "skip_blueprint"],
            )
        if blueprint_id:
            return _fill(
                out,
                label="选章节并开写",
                detail="已选蓝本：select_write_chapters 或卡片选章，再 start_writing。",
                tools=["select_write_chapters", "start_writing", "get_writing_status"],
            )
        return _fill(
            out,
            label="起名并选写作路径",
            detail="新会话：ask_entry_path 读 gaps，只补缺的要素；用户已说「现有蓝本」等时可 match_blueprints，勿默认无脑 match。",
            tools=["ask_entry_path", "set_session_meta", "remember_writing_goal"],
            confidence="low",
        )

    if phase == ChatPhase.BLUEPRINT_SELECT.value:
        return _fill(
            out,
            label="选定或跳过蓝本",
            detail="用户在选蓝本：select_blueprint 或 skip_blueprint / 无蓝本写作。",
            tools=["select_blueprint", "skip_blueprint", "list_blueprints"],
        )

    if phase == ChatPhase.CREATE_HANDOFF.value:
        return _fill(
            out,
            label="补全蓝本分析",
            detail="蓝本未分析完：引导去蓝本页或 write_analyzed_only 选已分析章。",
            tools=["write_analyzed_only", "handoff_blueprint_page"],
        )

    if phase == ChatPhase.FREEFORM_ASK.value:
        return _fill(
            out,
            label="说清写什么、叫什么",
            detail="无蓝本问元信息：多轮口语收集后 submit_freeform_meta；有 pipe 行也可，但不强迫固定句式。",
            tools=["submit_freeform_meta"],
            ui_action="submit_freeform_meta",
        )

    if phase == ChatPhase.FREEFORM_OUTLINE.value:
        has_outline = bool(str(ff.get("outline_text") or "").strip() or ff.get("outline_pending_edit"))
        if not has_outline and not ff.get("outline_confirmed"):
            return _fill(
                out,
                label="生成或粘贴目录",
                detail="题目已定、尚无目录：用户说「生成目录」或粘贴章节后再 submit_outline；勿在定名后擅自生成。",
                tools=["submit_outline"],
            )
        if ff.get("outline_pending_edit") or not ff.get("outline_confirmed"):
            return _fill(
                out,
                label="在卡片里改目录并确认",
                detail="大纲待确认：引导点卡片「确认大纲」或 submit_outline；勿让用户粘贴整段目录到聊天。",
                tools=["submit_outline", "trim_outline_top_chapters"],
                ui_action="confirm_outline",
            )
        return _fill(
            out,
            label="确认大纲后进要点",
            detail="目录已定：generate_freeform_points 或 skip_freeform_points，再 start_writing。",
            tools=["generate_freeform_points", "skip_freeform_points"],
        )

    if phase == ChatPhase.FREEFORM_POINTS.value:
        step = str(ff.get("points_step") or "").strip()
        if step in {"", "ask", "pending"}:
            return _fill(
                out,
                label="生成或填写写作要点",
                detail="要点阶段：用户同意则 generate_freeform_points；说跳过则 skip_freeform_points。",
                tools=["generate_freeform_points", "skip_freeform_points"],
            )
        return _fill(
            out,
            label="开始写作",
            detail="要点已就绪：start_writing。",
            tools=["start_writing"],
        )

    if phase == ChatPhase.READY_TO_WRITE.value:
        return _fill(
            out,
            label="点击开始写作",
            detail="已就绪：start_writing（可带 chapter_keys / writing_pace）。",
            tools=["start_writing"],
            ui_action="start_writing",
        )

    if phase in {ChatPhase.WRITING.value, ChatPhase.CONFIRM_EXPORT.value}:
        step = _writing_step(session, home=home, message=message)
        if step:
            return step

    if phase == ChatPhase.CONFIRM_EXPORT.value:
        return _fill(
            out,
            label="导出 Word",
            detail="写作收尾：prepare_export_word。",
            tools=["prepare_export_word"],
            ui_action="prepare_export_word",
        )

    if not out["label"]:
        out["label"] = "继续说明你的需求"
        out["detail"] = "结合 writing_brief 与 phase 选最合理的 Tool；用户闲聊可短答并问一句澄清。"
        out["confidence"] = "low"
    return out


def _writing_step(
    session: ChatSession,
    *,
    home: Path | None,
    message: str,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "label": "",
        "detail": "",
        "priority_tools": [],
        "ui_action": "",
        "confidence": "high",
    }
    ff = dict(session.freeform or {})
    if ff.get("writing_paused"):
        return _fill(
            out,
            label="已暂停，可继续或改章",
            detail="writing_paused：用户说继续则 start_writing；改章用 configure_chapter / retry_chapter。",
            tools=["start_writing", "get_writing_status", "get_chapter_info", "configure_chapter"],
        )

    tid = (session.task_id or "").strip()
    task = None
    if tid and home is not None:
        from report_writing.chat.helpers import _load_task

        task = _load_task(home, tid)

    if task is not None:
        from report_writing.chat.chapter_resolve import pick_awaiting_confirm_chapter_key, pick_focus_chapter_key

        focus = pick_focus_chapter_key(task, message) or pick_awaiting_confirm_chapter_key(task)
        awaiting = pick_awaiting_confirm_chapter_key(task)
        for ch in task.chapters or []:
            st = ch.status.value if hasattr(ch.status, "value") else ch.status
            if st == ChapterStatus.NEEDS_HUMAN.value:
                return _fill(
                    out,
                    label="处理需人工介入的章节",
                    detail=f"章 {ch.key} 需人工：get_writing_status，configure_chapter 或 retry_chapter。",
                    tools=["get_writing_status", "get_chapter_info", "configure_chapter", "retry_chapter"],
                )
            if st in {ChapterStatus.SEARCHING.value, ChapterStatus.WRITING.value, ChapterStatus.CHECKING.value}:
                return _fill(
                    out,
                    label="等待本章写完",
                    detail="后台在写：get_writing_status；用户急改可 pause_writing。",
                    tools=["get_writing_status", "pause_writing"],
                )
        if awaiting or focus:
            key = focus or awaiting
            title = ""
            if task.outline and task.outline.chapters:
                for node in task.outline.chapters:
                    if str(node.key) == key:
                        title = f"{node.sequence or ''} {node.title or ''}".strip()
                        break
            return _fill(
                out,
                label=f"确认本章{' · ' + title if title else ''}",
                detail=f"进度卡有待确认章（{key}）：用户说确认则 confirm_chapter；要改要点/要素用 configure_chapter、add_chapter_element。",
                tools=["confirm_chapter", "get_chapter_info", "configure_chapter", "add_chapter_element", "retry_chapter"],
                ui_action="confirm_chapter",
            )
        confirmed = sum(
            1
            for ch in task.chapters or []
            if (ch.status.value if hasattr(ch.status, "value") else ch.status) == ChapterStatus.CONFIRMED.value
        )
        total = len(task.chapters or [])
        if total and confirmed >= total:
            return _fill(
                out,
                label="全部确认，可导出",
                detail="各章已确认：prepare_export_word 或 confirm_all_chapters 收尾。",
                tools=["prepare_export_word", "get_writing_status"],
                ui_action="prepare_export_word",
            )

    return _fill(
        out,
        label="看写作进度卡片",
        detail="写作中：get_writing_status；改章 configure_chapter；加要素 add_chapter_element。",
        tools=["get_writing_status", "get_chapter_info", "configure_chapter", "add_chapter_element"],
    )


def _fill(
    out: dict[str, Any],
    *,
    label: str,
    detail: str,
    tools: list[str],
    ui_action: str = "",
    confidence: str = "medium",
) -> dict[str, Any]:
    out["label"] = label.strip()
    out["detail"] = detail.strip()
    out["priority_tools"] = list(tools or [])
    out["ui_action"] = (ui_action or "").strip()
    out["confidence"] = confidence
    return out
