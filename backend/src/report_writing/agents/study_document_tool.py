"""Agent tools：先 extract_outline_from_document，确认目录后再 study_from_document。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from report_writing.controller import (
    auto_study_selected_chapters,
    confirm_auto_study,
    confirm_outline_extract,
    extract_outline_from_document,
)
from report_writing.domain.models import (
    AutoStudyDraft,
    ExtractOutlineToolResult,
    OutlineDiffItem,
    OutlineExtractDraft,
    StudyDocumentToolResult,
    WritingTask,
)

EXTRACT_TOOL_NAME = "extract_outline_from_document"
EXTRACT_TOOL_DESCRIPTION = (
    "从粘贴文本或上传报告中，相对勾选的蓝本父章节发现尚未收录的子孙目录（如 2.3 → 2.3.1）。"
    "只返回 outline_diff，不提炼写作要点；须用户确认后再写入目录并调用 study_from_document。"
)

STUDY_TOOL_NAME = "study_from_document"
STUDY_TOOL_DESCRIPTION = (
    "依据已确认目录与用户资料，对给定章节提炼写作要点。"
    "目录新增须先经 extract_outline_from_document 确认；本工具不再改目录结构。"
)


def extract_tool_schema() -> dict[str, Any]:
    return {
        "name": EXTRACT_TOOL_NAME,
        "description": EXTRACT_TOOL_DESCRIPTION,
        "parameters": {
            "type": "object",
            "properties": {
                "chapter_keys": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "勾选的蓝本父章节 key 列表",
                },
                "source_text": {
                    "type": "string",
                    "description": "粘贴的富文本/纯文本资料（与 template_path 二选一）",
                },
                "template_path": {
                    "type": "string",
                    "description": "上传报告文件路径（与 source_text 二选一）",
                },
            },
            "required": ["chapter_keys"],
        },
    }


def study_tool_schema() -> dict[str, Any]:
    return {
        "name": STUDY_TOOL_NAME,
        "description": STUDY_TOOL_DESCRIPTION,
        "parameters": {
            "type": "object",
            "properties": {
                "chapter_keys": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "要学习要点的章节 key（含已确认写入的新章）",
                },
                "source_text": {
                    "type": "string",
                    "description": "粘贴资料；可省略以复用 extract 阶段已保存的正文",
                },
                "template_path": {
                    "type": "string",
                    "description": "上传报告路径；可省略以复用 extract 阶段资料",
                },
            },
            "required": ["chapter_keys"],
        },
    }


def tool_schema() -> dict[str, Any]:
    """兼容旧调用：默认返回 study tool schema。"""
    return study_tool_schema()


def all_tool_schemas() -> list[dict[str, Any]]:
    return [extract_tool_schema(), study_tool_schema()]


def build_extract_outline_result(
    drafts: list[OutlineExtractDraft],
    *,
    selected_chapter_keys: list[str],
) -> ExtractOutlineToolResult:
    outline_diff = [
        OutlineDiffItem(
            action="add",
            sequence=item.sequence,
            title=item.title,
            parent_sequence=item.parent_sequence or "",
            chapter_key=item.chapter_key,
        )
        for item in drafts
    ]
    mismatch = bool(drafts)
    if mismatch:
        preview = "、".join(f"{item.sequence} {item.title}".strip() for item in drafts[:6])
        more = f" 等 {len(drafts)} 项" if len(drafts) > 6 else ""
        message = (
            f"检测到资料目录与当前蓝本不一致，发现 {len(drafts)} 个新章节"
            f"（{preview}{more}）。请确认是否写入目录，并可先修改序号/标题。"
        )
    else:
        message = "未发现新的目录章节，将直接进入写作要点分析。"
    return ExtractOutlineToolResult(
        tool=EXTRACT_TOOL_NAME,
        needs_confirmation=mismatch,
        outline_mismatch=mismatch,
        message=message,
        new_chapter_count=len(drafts),
        outline_diff=outline_diff,
        selected_chapter_keys=list(selected_chapter_keys),
        drafts=list(drafts),
    )


def build_study_points_result(drafts: list[AutoStudyDraft]) -> StudyDocumentToolResult:
    message = f"已提炼 {len(drafts)} 个章节的写作要点，请确认后写入蓝本。"
    return StudyDocumentToolResult(
        tool=STUDY_TOOL_NAME,
        needs_confirmation=True,
        outline_mismatch=False,
        message=message,
        new_chapter_count=0,
        outline_diff=[],
        drafts=list(drafts),
    )


def build_tool_result(drafts: list[AutoStudyDraft]) -> StudyDocumentToolResult:
    """兼容旧名。"""
    return build_study_points_result(drafts)


def run_extract_outline_from_document_tool(
    task_dir: Path,
    chapter_keys: list[str],
    *,
    source_text: str = "",
    template_path: str | Path | None = None,
    on_event=None,
) -> WritingTask:
    path = Path(template_path) if template_path else None
    return extract_outline_from_document(
        task_dir,
        chapter_keys,
        template_path=path,
        source_text=source_text,
        on_event=on_event,
    )


def run_study_from_document_tool(
    task_dir: Path,
    chapter_keys: list[str],
    *,
    source_text: str = "",
    template_path: str | Path | None = None,
    on_event=None,
) -> WritingTask:
    path = Path(template_path) if template_path else None
    return auto_study_selected_chapters(
        task_dir,
        chapter_keys,
        template_path=path,
        source_text=source_text,
        on_event=on_event,
    )


def confirm_extract_outline_tool(
    task_dir: Path,
    drafts: list | None = None,
    *,
    apply_outline: bool = True,
) -> WritingTask:
    return confirm_outline_extract(task_dir, drafts, apply_outline=apply_outline)


def confirm_study_from_document_tool(task_dir: Path, drafts: list | None = None) -> WritingTask:
    return confirm_auto_study(task_dir, drafts)
