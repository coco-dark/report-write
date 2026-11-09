"""面向用户的过程提示（非调试链路），总控调工具时说明「正在做什么」。"""

from __future__ import annotations

from typing import Any, Callable

from report_writing.domain.models import ChatActionPayload, ChatPayload

# tool 名 → 口语化过程说明（会出现在对话过程条）
USER_TOOL_HINTS: dict[str, str] = {
    "match_blueprints": "正在看看有没有合适的蓝本…",
    "list_blueprints": "正在列出可用蓝本…",
    "select_blueprint": "正在选用你定的蓝本…",
    "select_write_chapters": "正在整理要写的章节…",
    "write_analyzed_only": "正在准备只写已分析的章节…",
    "skip_blueprint": "好的，不走蓝本，按你的题目来写…",
    "begin_blueprint_wizard": "正在打开依据模版编写向导…",
    "submit_freeform_meta": "正在根据你的话整理文档类型和标题…",
    "set_session_meta": "正在根据你的话整理文档类型和标题…",
    "submit_outline": "正在处理章节目录…",
    "generate_freeform_points": "正在起草各章写作要点…",
    "start_writing": "正在准备开始写作…",
    "get_writing_status": "正在查看写作进度…",
    "prepare_export_word": "正在准备导出 Word…",
    "pause_writing": "正在暂停写作…",
    "confirm_chapter": "正在处理本章确认…",
    "confirm_all_chapters": "正在批量确认章节…",
    "configure_chapter": "正在更新章节配置…",
    "get_chapter_info": "正在查看章节配置…",
    "generate_chapter_describes": "正在生成章节说明…",
    "generate_chapter_boilerplate": "正在根据原文生成套话正文…",
    "add_chapter_element": "正在为本章添加要素…",
    "dissociate_chapter_element": "正在取消要素关联…",
    "remove_document_element": "正在从本文档移除要素…",
    "retry_chapter": "正在准备开始写作…",
    "retry_search_chapter": "正在重新检索本章资料…",
    "remember_writing_goal": "正在记住你的写作要求…",
    "blueprint_subagent": "正在处理蓝本相关事宜…",
    "blueprint_wizard_subagent": "正在引导蓝本创建步骤…",
    "render_subagent": "正在处理渲染模版…",
    "style_subagent": "正在处理 Word 样式…",
    "trim_outline_top_chapters": "正在按你的要求裁剪目录…",
    "submit_points": "正在整理写作要点…",
    "blueprint_wizard": "正在处理蓝本向导…",
    "template_wizard": "正在打开模版配置向导…",
    "save_freeform_as_blueprint": "正在把当前写作保存为蓝本…",
    "skip_freeform_points": "正在跳过要点配置…",
    "handoff_blueprint_page": "正在前往蓝本页面…",
    "resume_from_blueprint": "正在从蓝本继续写作…",
    "resource_choice": "正在处理资源配置…",
}

# 工具跑完后补一条完成态，避免过程条一直停在「正在…」
USER_TOOL_DONE_HINTS: dict[str, str] = {
    "generate_freeform_points": "各章写作要点已起草",
    "generate_chapter_boilerplate": "套话正文已生成",
    "get_chapter_info": "章节配置已列出",
    "generate_chapter_describes": "章节说明已生成",
    "skip_freeform_points": "已跳过要点配置",
    "submit_outline": "章节目录已处理",
    "start_writing": "已开始准备写作",
}

_DEFAULT_ACTION_HINT = "正在处理你的请求…"

CONTROL_WAIT_HINT = "正在联系写作助手思考下一步…（模型推理可能需要十几秒）"
CONTROL_RECEIVED_HINT = "收到，我先理解你的需求，再安排具体步骤。"


def user_hint_for_tool(tool_name: str) -> str:
    key = (tool_name or "").strip()
    return USER_TOOL_HINTS.get(key, "")


def emit_user_process(emit: Any, message: str, **extra: Any) -> None:
    if emit is None or not (message or "").strip():
        return
    payload: dict[str, Any] = {"type": "process", "message": (message or "").strip()}
    payload.update(extra)
    if callable(emit):
        emit(payload)


def emit_tool_step(emit: Any, tool_name: str) -> None:
    hint = user_hint_for_tool(tool_name)
    if hint:
        emit_user_process(emit, hint)


def emit_tool_done(emit: Any, tool_name: str) -> None:
    key = (tool_name or "").strip()
    hint = USER_TOOL_DONE_HINTS.get(key, "")
    if hint:
        emit_user_process(emit, hint)


def emit_action_process(emit: Any, action: str, payload: ChatPayload = None) -> None:
    """预路由 / UI action 过程条：禁止暴露 snake_case 工具名。"""
    key = (action or "").strip()
    from report_writing.domain.models import action_payload_dict

    body = action_payload_dict(payload)
    # 蓝本向导按步骤给过程条，避免一律「打开创建向导」
    if key == "blueprint_wizard":
        step = str(body.get("step") or "").strip()
        step_hints = {
            "meta": "正在创建依据样本文档…",
            "upload": "正在准备上传样板…",
            "upload_done": "正在确认样板并进入提取目录…",
            "outline": "正在提取目录…",
            "outline_done": "正在整理目录…",
            "outline_confirm": "正在确认目录…",
            "study": "正在准备分析要点…",
            "study_done": "正在汇总分析结果…",
        }
        cmd = str(body.get("wizard_command") or "").strip()
        if cmd == "re_extract_outline":
            emit_user_process(emit, "正在重新提取目录…")
            return
        if cmd == "re_upload_sample":
            emit_user_process(emit, "正在准备重新上传样板…")
            return
        if step in step_hints:
            emit_user_process(emit, step_hints[step])
            return
    if key in {"retry_chapter", "skip_writing_supplement_check", "start_writing"}:
        emit_user_process(emit, "正在准备开始写作…")
        return
    hint = user_hint_for_tool(key)
    if hint:
        emit_user_process(emit, hint)
        return
    from report_writing.chat.helpers import _user_action_label

    label = _user_action_label(key, payload)
    if label and label != "继续":
        emit_user_process(emit, f"正在{label}…")
    else:
        emit_user_process(emit, _DEFAULT_ACTION_HINT)
