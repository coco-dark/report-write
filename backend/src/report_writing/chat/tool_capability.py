"""Tool 能力前置：只拦该 Tool 真正缺的槽，不把整段会话钉成固定流水线。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from report_writing.domain.models import ChatSession

FORMAL_NAME_TOOLS = frozenset(
    {
        "prepare_export_word",
        "save_freeform_as_blueprint",
        "sync_document_to_blueprint_library",
    }
)

WRITABLE_CHAPTER_TOOLS = frozenset(
    {
        "start_writing",
        "select_write_chapters",
        "write_analyzed_only",
        "retry_chapter",
    }
)

# 无会话级前置：浏览、匹配、向导、闲聊均可在未定名时调用
NO_PREREQ_TOOLS = frozenset(
    {
        "list_blueprints",
        "match_blueprints",
        "open_blueprint_library",
        "open_element_library",
        "open_knowledge",
        "begin_blueprint_wizard",
        "blueprint_wizard",
        "ask_intent",
        "ask_entry_path",
        "set_session_meta",
        "remember_writing_goal",
        "skip_blueprint",
        "submit_outline",
        "generate_freeform_points",
    }
)


def missing_for_tool(
    session: ChatSession,
    tool: str,
    *,
    home: Path | None = None,
) -> list[str]:
    """返回该 Tool 当前真正缺的 canonical 槽；空列表表示可以执行。"""
    name = (tool or "").strip()
    if not name or name in NO_PREREQ_TOOLS:
        return []

    from report_writing.chat.session_documents import (
        focus_needs_doc_name,
        focus_outline_chapter_count,
        session_blueprint_already_selected,
    )

    missing: list[str] = []
    if name in FORMAL_NAME_TOOLS and focus_needs_doc_name(session):
        missing.append("doc_name")

    if name in WRITABLE_CHAPTER_TOOLS:
        if session_blueprint_already_selected(session):
            return missing
        if focus_outline_chapter_count(session, home) <= 0:
            missing.append("outline")
    return missing


def capability_reply_hint(missing: list[str]) -> str:
    """缺槽时给模型/用户的短提示（不展示 Tool 名）。"""
    if "doc_name" in missing:
        return "收录或导出前，先确认这份文档的正式名称（直接说题目即可）。"
    if "outline" in missing:
        return "这份文档还没有目录。可以说「生成目录」、粘贴目录，或先选一份蓝本。"
    if "writing_path" in missing:
        return "可以匹配蓝本、用自己的模版，或让我按题目生成目录。"
    return "还缺一点信息，我先问你。"


def capability_observation(tool: str, missing: list[str]) -> str:
    miss = ",".join(missing) if missing else "—"
    return f"{tool} 能力检查未过：missing={miss}。只补这一项，不要重走开场流水线。"


def filter_plan_missing(
    session: ChatSession,
    missing: list[str],
    *,
    tools: list[dict[str, Any]] | None = None,
) -> list[str]:
    """计划 JSON 里的 missing 只保留当前 Tool 真正需要的槽。"""
    names = {str(s.get("tool") or "").strip() for s in (tools or [])}
    keep: list[str] = []
    seen: set[str] = set()
    pathish = {"writing_path", "freeform_mode", "entry_path", "entry_branch"}
    for key in missing or []:
        k = str(key or "").strip()
        if not k or k in seen:
            continue
        if k in pathish:
            continue
        if k == "doc_name":
            if not names.intersection(FORMAL_NAME_TOOLS):
                continue
            from report_writing.chat.session_documents import focus_needs_doc_name

            if not focus_needs_doc_name(session):
                continue
        seen.add(k)
        keep.append(k)
    return keep
