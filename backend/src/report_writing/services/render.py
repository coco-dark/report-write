"""报告渲染：模版占位填充 / 无模版 LLM 生成整篇 HTML，再转 Word。"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from pydantic import Field

from report_writing.domain.content_blocks import ensure_html_fragment
from report_writing.domain.word_style import WordStyleSettings
from report_writing.services.html_docx import html_to_docx_bytes
from report_writing.domain.models import ChapterStatus, Entity, OutlineTree, RenderTemplateSummary, WritingTask
from report_writing.services.store import TaskStore

_log = logging.getLogger(__name__)
_ID_SAFE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_PLACEHOLDER = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")
_TAG = re.compile(r"<[^>]+>")
_schema_ready = False

InvokeFn = Callable[[str, str], str]


class RenderChapter(Entity):
    key: str = ""
    sequence: str = ""
    title: str = ""
    html: str = ""
    status: str = ""


class RenderResult(Entity):
    mode: str = "template"  # template | llm | plain
    template_id: str = ""
    html: str = ""
    chapter_count: int = 0
    filled: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)


_RENDER_PARENT = "writing_render_template"
_RENDER_CHILD = "writing_render_template_document"
_RENDER_META_DDL = """
CREATE TABLE IF NOT EXISTS writing_render_template (
    id varchar(64) NOT NULL PRIMARY KEY,
    name varchar(255) NOT NULL DEFAULT '',
    format varchar(32) NOT NULL DEFAULT 'DOCX',
    label_updated varchar(64) NOT NULL DEFAULT '',
    updated_at varchar(32) NOT NULL,
    KEY writing_render_template_updated_idx (updated_at)
)
"""
_RENDER_CHILD_DDL = """
CREATE TABLE IF NOT EXISTS writing_render_template_document (
    template_id varchar(64) NOT NULL PRIMARY KEY,
    document json NOT NULL
)
"""

_DEFAULT_TEMPLATE_SEEDS: list[dict[str, Any]] = [
    {
        "id": "rt_anping_a4",
        "name": "安评正文 A4",
        "desc": "按章节键占位填充；请用 {{章节key}} 或 {{title:key}} / {{seq:key}}。",
        "format": "DOCX",
        "tone": "linear-gradient(145deg,#dbeafe,#eff6ff)",
        "margin": "上下 2.54cm / 左右 3.17cm",
        "font": "宋体 12pt",
        "heading": "黑体分级标题",
        "header": "报告名称 + 页码",
        "html": (
            "<h1>安全预评价报告</h1>\n"
            "<h2>{{title:ch-1}}（{{seq:ch-1}}）</h2>\n{{ch-1}}\n"
            "<h2>{{title:ch-2}}（{{seq:ch-2}}）</h2>\n{{ch-2}}\n"
            "<p>（可按章节 key 继续追加占位符）</p>\n"
        ),
    },
    {
        "id": "rt_plain_shell",
        "name": "通用章节壳",
        "desc": "遍历时可用章节标题占位；未匹配占位符的章节不会自动追加。",
        "format": "DOCX",
        "tone": "linear-gradient(145deg,#dcfce7,#f0fdf4)",
        "margin": "上下 2.0cm / 左右 2.5cm",
        "font": "微软雅黑 11pt",
        "heading": "加粗分级",
        "header": "仅页码",
        "html": "<article>\n  <!-- 示例：{{overview}} 或 {{title:overview}} -->\n</article>\n",
    },
]


def templates_root(home: Path) -> Path:
    root = Path(home) / "render_templates"
    root.mkdir(parents=True, exist_ok=True)
    return root


def ensure_default_templates(home: Path, *, owner_id: str | None = None) -> None:
    if _use_mysql():
        if _db_template_count_for_user(owner_id) == 0:
            for item in _DEFAULT_TEMPLATE_SEEDS:
                data = dict(item)
                data["updated"] = data.get("updated") or _now_label()
                _db_save_template(data, owner_id=owner_id)
        return
    root = templates_root(home)
    if any(root.glob("*/meta.json")):
        return
    for item in _DEFAULT_TEMPLATE_SEEDS:
        _write_template(home, item)


def list_templates(home: Path) -> list[RenderTemplateSummary]:
    ensure_default_templates(home)
    if _use_mysql():
        return _db_list_templates()
    uid = _template_owner_id()
    items: list[RenderTemplateSummary] = []
    for meta_path in sorted(templates_root(home).glob("*/meta.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(meta, dict):
            continue
        if not _owns_template(meta, uid):
            continue
        items.append(_summary_from_meta(meta))
    return items


def get_template(home: Path, template_id: str) -> dict[str, Any] | None:
    ensure_default_templates(home)
    tid = _require_id(template_id)
    if _use_mysql():
        found = _db_get_template(tid)
        if found is not None:
            return found
        local = _file_get_template(home, tid)
        if local is not None:
            _db_save_template(local)
            return _db_get_template(tid)
        return None
    return _file_get_template(home, tid)


def save_template(home: Path, payload: dict[str, Any]) -> dict[str, Any]:
    data = dict(payload or {})
    tid = str(data.get("id") or "").strip() or f"rt{uuid.uuid4().hex[:10]}"
    tid = _require_id(tid)
    data["id"] = tid
    data["updated"] = data.get("updated") or _now_label()
    data.setdefault("format", "DOCX")
    data.setdefault("name", "未命名模版")
    data.setdefault("desc", "")
    _persist_template(home, data)
    return get_template(home, tid) or data


def delete_template(home: Path, template_id: str) -> None:
    import shutil

    tid = _require_id(template_id)
    if _use_mysql():
        if not _db_delete_template(tid):
            raise KeyError(template_id)
        folder = templates_root(home) / tid
        if folder.exists():
            shutil.rmtree(folder)
        return
    folder = templates_root(home) / tid
    if not folder.exists():
        raise KeyError(template_id)
    shutil.rmtree(folder)


def _visible_text_len(html: str) -> int:
    text = re.sub(r"(?is)<!--.*?-->", "", html or "")
    text = _TAG.sub("", text)
    text = (
        text.replace("&nbsp;", " ")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&amp;", "&")
        .replace("&quot;", '"')
    )
    return len(text.strip())


def _draft_paragraphs_html(draft) -> str:
    if draft is None:
        return ""
    paragraphs = getattr(draft, "paragraphs", None)
    if paragraphs is None and isinstance(draft, dict):
        paragraphs = draft.get("paragraphs")
    parts: list[str] = []
    for item in paragraphs or []:
        text = ""
        if isinstance(item, dict):
            text = str(item.get("text") or "").strip()
        else:
            text = str(getattr(item, "text", "") or "").strip()
        if text:
            parts.append(ensure_html_fragment(text))
    return "\n".join(parts).strip()


def _chapter_body_html(state) -> str:
    from report_writing.domain.content_blocks import promote_tables_in_html

    if state is None:
        return ""
    official = (getattr(state, "official_text", None) or "").strip()
    if not official and isinstance(state, dict):
        official = str(state.get("official_text") or "").strip()
    draft_html = _draft_paragraphs_html(getattr(state, "draft", None) if not isinstance(state, dict) else state.get("draft"))
    # 本轮草稿优先：重写后 disk/库里的旧 official_text 不得盖住新稿
    html = draft_html or official
    if _visible_text_len(html) < 1 and official:
        html = official
    # 历史正文可能是空格对齐的「工程特性表」纯文本：预览时提升为 <table>
    return promote_tables_in_html(html.strip()) if html.strip() else ""


def _pick_chapter_state(
    by_key: dict,
    by_seq: dict,
    by_title: dict,
    *,
    key: str,
    sequence: str,
    title: str,
):
    state = by_key.get(key)
    if state is not None:
        return state
    seq = str(sequence or "").strip()
    if seq and seq in by_seq:
        return by_seq[seq]
    name = str(title or "").strip()
    if name and name in by_title:
        return by_title[name]
    return None


def collect_chapters(task: WritingTask, *, confirmed_only: bool = True) -> list[RenderChapter]:
    """按章节序号（大纲顺序）收集正文，不按写作先后。"""
    from report_writing.domain.outline import _sequence_sort_key

    states = list(task.chapters or [])
    by_key = {c.key: c for c in states if c.key}
    by_seq: dict = {}
    by_title: dict = {}
    for chapter in states:
        title = str(getattr(chapter, "title", "") or "").strip()
        if title and title not in by_title:
            by_title[title] = chapter
    order: list[tuple[str, str, str]] = []
    if task.outline and task.outline.chapters:
        for node in task.outline.chapters:
            order.append((node.key, node.sequence or "", node.title or node.key))
            seq = str(node.sequence or "").strip()
            if seq and seq not in by_seq:
                hit = by_key.get(node.key)
                if hit is not None:
                    by_seq[seq] = hit
    else:
        for chapter in states:
            order.append((chapter.key, "", chapter.title or chapter.key))

    # 写作 clone 的 outline.chapters 可能被写成「写作先后」；预览/导出一律按序号
    order.sort(key=lambda item: (_sequence_sort_key(item[1]), item[0]))

    rows: list[RenderChapter] = []
    seen: set[str] = set()
    used_states: set[str] = set()
    for key, sequence, title in order:
        if not key or key in seen:
            continue
        seen.add(key)
        state = _pick_chapter_state(by_key, by_seq, by_title, key=key, sequence=sequence, title=title)
        status = ""
        html = ""
        if state is not None:
            used_states.add(str(getattr(state, "key", "") or ""))
            status = str(state.status.value if hasattr(state.status, "value") else state.status)
            if status == ChapterStatus.SKIPPED.value:
                continue
            if confirmed_only and state.status != ChapterStatus.CONFIRMED:
                html = ""
            else:
                html = _chapter_body_html(state)
        rows.append(
            RenderChapter(
                key=key,
                sequence=sequence,
                title=title or (state.title if state else key),
                html=html,
                status=status,
            )
        )
    # 大纲缺 key 但已写正文：仍拼进预览，避免全文空白
    for chapter in states:
        ck = str(getattr(chapter, "key", "") or "").strip()
        if not ck or ck in seen or ck in used_states:
            continue
        status = str(chapter.status.value if hasattr(chapter.status, "value") else chapter.status)
        if status == ChapterStatus.SKIPPED.value:
            continue
        html = "" if (confirmed_only and chapter.status != ChapterStatus.CONFIRMED) else _chapter_body_html(chapter)
        if _visible_text_len(html) < 1:
            continue
        seen.add(ck)
        rows.append(
            RenderChapter(
                key=ck,
                sequence="",
                title=chapter.title or ck,
                html=html,
                status=status,
            )
        )
    return rows


def _escape_preview_text(text: str) -> str:
    return (
        (text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def build_written_chapters_preview(
    task: WritingTask,
    *,
    written_only: bool = True,
    confirmed_only: bool = False,
    home: Path | None = None,
) -> dict[str, Any]:
    """组装已书写章节的 HTML 预览（按大纲顺序；任务隔离由调用方传入 task）。"""
    rows = collect_chapters(task, confirmed_only=confirmed_only)
    structure_keys: set[str] = set()
    if task.outline and task.outline.chapters:
        for node in task.outline.chapters:
            if bool(getattr(node, "structure_only", False)):
                structure_keys.add(str(node.key or "").strip())

    parts: list[str] = []
    toc: list[dict[str, Any]] = []
    written = 0
    doc_title = _escape_preview_text(str(getattr(task, "title", "") or task.id or "文档预览"))
    parts.append(f'<article class="rw-draft-preview" data-task-id="{_escape_preview_text(task.id)}">')
    parts.append(f"<header><h1>{doc_title}</h1></header>")

    for item in rows:
        key = str(item.key or "").strip()
        if not key:
            continue
        body = (item.html or "").strip()
        has_body = _visible_text_len(body) > 0
        # 仅目录且无正文才跳过；写过正文的章必须进预览
        if key in structure_keys and not has_body:
            continue
        if written_only and not has_body:
            continue
        written += 1 if has_body else 0
        seq = _escape_preview_text(item.sequence or "")
        title = _escape_preview_text(item.title or key)
        heading = f"{seq} {title}".strip() if seq else title
        level = 2
        if item.sequence and item.sequence.count(".") >= 2:
            level = 4
        elif item.sequence and "." in item.sequence:
            level = 3
        toc.append(
            {
                "key": key,
                "sequence": item.sequence or "",
                "title": item.title or key,
                "status": item.status or "",
                "has_body": has_body,
            }
        )
        parts.append(f'<section class="rw-preview-chapter" data-chapter-key="{_escape_preview_text(key)}">')
        parts.append(f"<h{level}>{heading}</h{level}>")
        if has_body:
            if home is not None:
                from report_writing.services.element_images import expand_image_placeholders_html

                body = expand_image_placeholders_html(body, home, task, for_export=False)
            parts.append(f'<div class="rw-preview-body">{body}</div>')
        else:
            parts.append('<p class="rw-preview-empty">（本章尚无正文）</p>')
        parts.append("</section>")

    parts.append("</article>")
    return {
        "task_id": task.id,
        "title": str(getattr(task, "title", "") or task.id or ""),
        "chapter_count": len(rows),
        "written_count": written,
        "html": "\n".join(parts),
        "chapters": toc,
    }


def fill_template(template_html: str, chapters: list[RenderChapter]) -> tuple[str, list[str], list[str]]:
    """用 {{key}} / {{title:key}} / {{seq:key}} / {{body:key}} 填充模版。"""
    index = {item.key: item for item in chapters if item.key}
    # 标题别名：同名章节可用 {{概述}} 填充正文
    by_title = {}
    for item in chapters:
        name = (item.title or "").strip()
        if name and name not in by_title:
            by_title[name] = item

    filled: list[str] = []
    missing: list[str] = []

    def repl(match: re.Match[str]) -> str:
        token = (match.group(1) or "").strip()
        if not token:
            return ""
        lower = token.lower()
        kind = "body"
        key = token
        if ":" in token:
            prefix, rest = token.split(":", 1)
            prefix = prefix.strip().lower()
            key = rest.strip()
            if prefix in {"title", "name", "标题"}:
                kind = "title"
            elif prefix in {"seq", "sequence", "序号"}:
                kind = "seq"
            elif prefix in {"body", "html", "content", "正文"}:
                kind = "body"
            else:
                key = token
        chapter = index.get(key) or by_title.get(key)
        if chapter is None:
            missing.append(token)
            return ""
        filled.append(token)
        if kind == "title":
            return chapter.title or chapter.key
        if kind == "seq":
            return chapter.sequence or ""
        return chapter.html or ""

    html = _PLACEHOLDER.sub(repl, template_html or "")
    return html, sorted(set(filled)), sorted(set(missing))


def _heading_level(level: int, *, default: int = 2) -> int:
    try:
        return min(max(int(level or default), 1), 6)
    except (TypeError, ValueError):
        return default


def _outline_level(node, *, default: int = 2) -> int:
    """优先按章节序号段数定层级（2.3.2.1 → 4 级），再回落节点 level。"""
    seq = (getattr(node, "sequence", "") or "").strip()
    parts = [part for part in seq.split(".") if part.strip()]
    if parts and re.match(r"^\d", parts[0]):
        return min(max(len(parts), 1), 6)
    return _heading_level(getattr(node, "level", default), default=default)


def _is_outline_chapter_node(node) -> bool:
    """仅大纲章节（带序号或目录节点），排除要点/句子误挂的节点。"""
    seq = (getattr(node, "sequence", "") or "").strip()
    if seq and re.search(r"\d", seq):
        return True
    return bool(getattr(node, "structure_only", False))


def _export_outline_nodes(outline: OutlineTree | None) -> list:
    if outline is None or not outline.chapters:
        return []
    return [node for node in outline.chapters if _is_outline_chapter_node(node)]


def _normalize_heading_text(value: str) -> str:
    return re.sub(r"\s+", "", (value or "").strip())


def _strip_leading_duplicate_heading(body: str, heading: str) -> str:
    target = _normalize_heading_text(heading)
    if not target or not (body or "").strip():
        return body or ""
    match = re.match(r"(?is)^\s*<h[1-6][^>]*>(.*?)</h[1-6]>\s*", body or "")
    if not match:
        return body
    inner = re.sub(r"<[^>]+>", "", match.group(1))
    if _normalize_heading_text(inner) == target or target in _normalize_heading_text(inner):
        return body[match.end() :]
    return body


def _demote_content_headings(html: str) -> str:
    """正文内 h1-h6 改为 subheading 段落，避免进入 Word 导航目录。"""

    def repl(match: re.Match[str]) -> str:
        level = match.group(1)[1]
        inner = match.group(3) or ""
        return f'<p class="subheading" data-level="{level}">{inner}</p>'

    return re.sub(r"(?is)<(h[1-6])(\s[^>]*)?>(.*?)</\1>", repl, html or "")


def _outline_toc_html(outline: OutlineTree) -> str:
    lines = ['<div class="report-toc">', '<p class="toc-title">目录</p>']
    for node in _export_outline_nodes(outline):
        seq = (getattr(node, "sequence", "") or "").strip()
        title = (getattr(node, "title", "") or getattr(node, "key", "") or "").strip()
        label = " ".join(x for x in [seq, title] if x)
        level = _outline_level(node)
        lines.append(f'<p class="toc-entry" data-level="{level}">{_escape(label)}</p>')
    lines.append("</div>")
    return "\n".join(lines)


def plain_document_html(
    chapters: list[RenderChapter],
    *,
    report_title: str = "",
    outline: OutlineTree | None = None,
    include_toc: bool = True,
) -> str:
    parts = ['<!DOCTYPE html><html><head><meta charset="utf-8" /></head><body>']
    if report_title.strip():
        parts.append(f"<h1>{_escape(report_title.strip())}</h1>")
    if outline is not None and outline.chapters and include_toc:
        parts.append(_outline_toc_html(outline))
    index = {item.key: item for item in chapters if item.key}
    nodes = _export_outline_nodes(outline) if outline is not None else []
    if nodes:
        for node in nodes:
            key = (getattr(node, "key", "") or "").strip()
            if not key:
                continue
            item = index.get(key)
            body = (item.html if item else "") or ""
            heading = " ".join(
                x
                for x in [
                    (getattr(node, "sequence", "") or (item.sequence if item else "")),
                    getattr(node, "title", "") or (item.title if item else key),
                ]
                if x
            ).strip()
            level = _outline_level(node)
            structure_only = bool(getattr(node, "structure_only", False))
            if _visible_text_len(body) > 0:
                body = _strip_leading_duplicate_heading(body, heading)
                body = _demote_content_headings(body)
                if heading:
                    parts.append(
                        f'<p class="chapter-title" data-level="{level}">{_escape(heading)}</p>'
                    )
                parts.append(ensure_html_fragment(body))
            elif structure_only and heading:
                parts.append(
                    f'<p class="chapter-title" data-level="{level}">{_escape(heading)}</p>'
                )
    else:
        for item in chapters:
            if not (item.html or "").strip():
                continue
            heading = " ".join(x for x in [item.sequence, item.title or item.key] if x).strip()
            body = _demote_content_headings(item.html)
            body = _strip_leading_duplicate_heading(body, heading)
            if heading:
                parts.append(f'<p class="chapter-title" data-level="2">{_escape(heading)}</p>')
            parts.append(ensure_html_fragment(body))
    parts.append("</body></html>")
    return "\n".join(parts)


def llm_document_html(
    chapters: list[RenderChapter],
    *,
    report_title: str = "",
    invoke: InvokeFn,
) -> str:
    """把目录与章节 HTML 交给大模型，统一输出完整 HTML 文档。"""
    if invoke is None:
        raise ValueError("未配置大模型，无法无模版渲染")
    catalog = []
    for item in chapters:
        catalog.append(
            {
                "key": item.key,
                "sequence": item.sequence,
                "title": item.title,
                "html": item.html or "",
                "status": item.status,
            }
        )
    system = (
        "# Role\n你是文档排版编辑。\n"
        "# Task\n根据给定的章节目录与各章 HTML 正文，输出一份完整、可直接用于转 Word 的 HTML 文档。"
        "版式须符合该文档类型（函件、方案、说明书、纪要、报告等），不要一律套研究报告封面体例。\n"
        "# Rules\n"
        "1. 只输出 HTML（可含 <!DOCTYPE html>），不要 Markdown 围栏，不要解释。\n"
        "2. 保留章节顺序与序号/标题层级（h1/h2/h3）。\n"
        "3. 尽量保留各章原有段落、列表、表格结构；不要编造原文没有的事实。\n"
        "4. 空正文的章节可省略或仅保留标题。\n"
        "5. 使用简洁语义化标签：h1-h3、p、ul/ol/li、table/tr/td、strong/em。\n"
        "# Output\n完整 HTML 文档。"
    )
    user = (
        f"报告标题：{report_title or '未命名报告'}\n"
        f"章节数：{len(catalog)}\n"
        f"章节 JSON：\n{json.dumps(catalog, ensure_ascii=False)}"
    )
    raw = str(invoke(system, user) or "").strip()
    html = _strip_fence(raw)
    if not html.lower().lstrip().startswith("<"):
        # 模型偶发包了说明文字：尽量截取首个标签起
        match = re.search(r"(?is)<!DOCTYPE html>|<html\b|<body\b|<h1\b|<article\b", html)
        if match:
            html = html[match.start() :]
    if not html.strip():
        raise ValueError("大模型未返回可用 HTML")
    return html


def render_task(
    home: Path,
    task: WritingTask,
    *,
    template_id: str = "",
    mode: str = "",
    confirmed_only: bool = True,
    invoke: InvokeFn | None = None,
    word_style=None,
) -> RenderResult:
    from report_writing.domain.word_style import WordStyleSettings

    style = word_style if isinstance(word_style, WordStyleSettings) else WordStyleSettings()
    include_toc = style.include_toc
    """
    mode:
      - template: 必须提供 template_id，占位符填充
      - llm: 无模版，大模型整篇 HTML
      - auto: 有 template_id 走 template，否则 llm（llm 不可用则 plain）
      - plain: 本地按章节顺序拼接
    """
    chapters = collect_chapters(task, confirmed_only=confirmed_only)
    report_title = (task.title or task.id or "").strip()
    chosen = (mode or "auto").strip().lower()
    tid = (template_id or "").strip()

    if chosen == "auto":
        chosen = "template" if tid else "plain"

    if chosen == "template":
        if not tid:
            raise ValueError("模版渲染需要 template_id")
        meta = get_template(home, tid)
        if meta is None:
            raise KeyError(tid)
        html, filled, missing = fill_template(str(meta.get("html") or ""), chapters)
        if _visible_text_len(html) < 2 and any(_visible_text_len(c.html) > 0 for c in chapters):
            _log.warning("模版 %s 占位未匹配正文，回落按章节顺序拼接", tid)
            html = plain_document_html(
                chapters, report_title=report_title, outline=task.outline, include_toc=include_toc
            )
            return RenderResult(
                mode="plain",
                template_id=tid,
                html=html,
                chapter_count=len(chapters),
                filled=filled,
                missing=missing,
            )
        return RenderResult(
            mode="template",
            template_id=tid,
            html=html,
            chapter_count=len(chapters),
            filled=filled,
            missing=missing,
        )

    if chosen == "llm":
        if invoke is None:
            _log.warning("无模版渲染未提供 invoke，回落 plain")
            html = plain_document_html(
                chapters, report_title=report_title, outline=task.outline, include_toc=include_toc
            )
            return RenderResult(mode="plain", html=html, chapter_count=len(chapters))
        try:
            html = llm_document_html(chapters, report_title=report_title, invoke=invoke)
        except Exception as error:
            _log.warning("LLM 渲染失败，回落 plain: %s", error)
            html = plain_document_html(
                chapters, report_title=report_title, outline=task.outline, include_toc=include_toc
            )
            return RenderResult(mode="plain", html=html, chapter_count=len(chapters))
        if _visible_text_len(html) < 2:
            _log.warning("LLM 渲染结果为空，回落 plain")
            html = plain_document_html(
                chapters, report_title=report_title, outline=task.outline, include_toc=include_toc
            )
            return RenderResult(mode="plain", html=html, chapter_count=len(chapters))
        return RenderResult(mode="llm", html=html, chapter_count=len(chapters))

    if chosen == "plain":
        html = plain_document_html(
            chapters, report_title=report_title, outline=task.outline, include_toc=include_toc
        )
        return RenderResult(mode="plain", html=html, chapter_count=len(chapters))

    raise ValueError("mode 只能是 template / llm / plain / auto")


def _validate_export_chapters(
    task: WritingTask,
    chapters: list[RenderChapter],
    *,
    confirmed_only: bool,
) -> None:
    has_body = any(_visible_text_len(item.html) > 0 for item in chapters)
    has_outline = bool(task.outline and task.outline.chapters)
    if not has_body and not has_outline:
        confirmed_n = sum(
            1 for c in (task.chapters or []) if getattr(c, "status", None) == ChapterStatus.CONFIRMED
        )
        draft_n = sum(1 for c in (task.chapters or []) if _visible_text_len(_chapter_body_html(c)) > 0)
        if confirmed_only and draft_n > confirmed_n:
            raise ValueError(
                "没有可导出的正文：当前仅导出「已确认」章节，但还有未确认草稿。"
                "请先在对话里确认章节，或关闭「仅已确认章节」以包含草稿。"
            )
        raise ValueError(
            "没有可导出的正文。请先完成写作并确认章节，或检查渲染模版占位符是否与章节 key 一致。"
        )


def _render_task_export_html(
    home: Path,
    task: WritingTask,
    *,
    template_id: str = "",
    style_id: str = "",
    mode: str = "",
    confirmed_only: bool = True,
    invoke: InvokeFn | None = None,
) -> tuple[str, RenderResult, WordStyleSettings]:
    from report_writing.services.word_styles import resolve_word_style

    word_style = resolve_word_style(home, style_id)
    task = _hydrate_chapters_from_db(task)
    chapters = collect_chapters(task, confirmed_only=confirmed_only)
    _validate_export_chapters(task, chapters, confirmed_only=confirmed_only)
    result = render_task(
        home,
        task,
        template_id=template_id,
        mode=mode,
        confirmed_only=confirmed_only,
        invoke=invoke,
        word_style=word_style,
    )
    if _visible_text_len(result.html) < 2:
        raise ValueError(
            "渲染结果为空，请检查渲染模版占位符（如 {{章节key}}）是否与大纲章节 key 一致。"
        )
    from report_writing.services.element_images import expand_image_placeholders_html

    export_html = expand_image_placeholders_html(result.html, home, task, for_export=True)
    return export_html, result, word_style


def render_task_docx(
    home: Path,
    task: WritingTask,
    *,
    template_id: str = "",
    style_id: str = "",
    mode: str = "",
    confirmed_only: bool = True,
    invoke: InvokeFn | None = None,
) -> tuple[bytes, RenderResult]:
    export_html, result, word_style = _render_task_export_html(
        home,
        task,
        template_id=template_id,
        style_id=style_id,
        mode=mode,
        confirmed_only=confirmed_only,
        invoke=invoke,
    )
    title = (task.title or task.id or "报告").strip()
    data = html_to_docx_bytes(
        export_html,
        title="" if "<h1" in result.html.lower() else title,
        style=word_style,
    )
    return data, result


def render_task_pdf(
    home: Path,
    task: WritingTask,
    *,
    template_id: str = "",
    style_id: str = "",
    mode: str = "",
    confirmed_only: bool = True,
    invoke: InvokeFn | None = None,
) -> tuple[bytes, RenderResult]:
    from report_writing.services.html_pdf import html_to_pdf_bytes

    export_html, result, word_style = _render_task_export_html(
        home,
        task,
        template_id=template_id,
        style_id=style_id,
        mode=mode,
        confirmed_only=confirmed_only,
        invoke=invoke,
    )
    title = (task.title or task.id or "报告").strip()
    base_url = (Path(home) / "tasks" / str(task.id or "").strip()).resolve().as_uri()
    data = html_to_pdf_bytes(
        export_html,
        title="" if "<h1" in result.html.lower() else title,
        style=word_style,
        base_url=base_url,
    )
    return data, result


def _hydrate_chapters_from_db(task: WritingTask) -> WritingTask:
    """task.json 缺 official_text 时，从 MySQL 已确认章节表补全。"""
    from report_writing.infra.postgres import load_database_settings

    settings = load_database_settings()
    if settings is None or not (task.id or "").strip():
        return task
    try:
        from report_writing.infra.postgres import _connect

        with _connect(settings) as connection:
            cursor = connection.execute(
                "SELECT chapter_key, official_text FROM writing_task_chapter WHERE task_id = %s",
                (task.id,),
            )
            rows = cursor.fetchall() or []
            by_key = {str(row[0]): str(row[1] or "") for row in rows if row and row[0]}
    except Exception as error:
        _log.debug("hydrate chapters skipped: %s", error)
        return task
    if not by_key:
        return task
    for chapter in task.chapters or []:
        if (chapter.official_text or "").strip():
            continue
        if getattr(chapter, "draft", None) and getattr(chapter.draft, "paragraphs", None):
            continue
        backup = (by_key.get(chapter.key) or "").strip()
        if backup:
            chapter.official_text = backup
    return task


def load_task(home: Path, task_id: str) -> WritingTask:
    path = Path(home) / "tasks" / task_id
    if not (path / "task.json").is_file():
        raise KeyError(task_id)
    task = TaskStore(path).load()
    return _hydrate_chapters_from_db(task)


def _persist_template(home: Path, data: dict[str, Any]) -> None:
    if _use_mysql():
        _db_save_template(data)
        return
    _write_template(home, data)


def _file_get_template(home: Path, template_id: str) -> dict[str, Any] | None:
    folder = templates_root(home) / template_id
    meta_path = folder / "meta.json"
    html_path = folder / "template.html"
    if not meta_path.is_file():
        return None
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if not isinstance(meta, dict):
        return None
    html = html_path.read_text(encoding="utf-8") if html_path.is_file() else str(meta.get("html") or "")
    meta["html"] = html
    return meta


def _ensure_render_schema(connection) -> None:
    global _schema_ready
    if _schema_ready:
        return
    from report_writing.infra.json_tables import migrate_legacy_document_column, table_columns

    connection.execute(_RENDER_CHILD_DDL)
    cols = table_columns(connection, _RENDER_PARENT)
    if not cols:
        connection.execute(_RENDER_META_DDL)
        _schema_ready = True
        return
    if "name" not in cols:
        for stmt in (
            "ADD COLUMN name varchar(255) NOT NULL DEFAULT ''",
            "ADD COLUMN format varchar(32) NOT NULL DEFAULT 'DOCX'",
            "ADD COLUMN label_updated varchar(64) NOT NULL DEFAULT ''",
        ):
            try:
                connection.execute(f"ALTER TABLE {_RENDER_PARENT} {stmt}")
            except Exception:
                pass

    def _on_migrate(parent_id: str, doc: dict, updated_at: str) -> None:
        tid = str(doc.get("id") or parent_id)
        connection.execute(
            f"""
            INSERT INTO {_RENDER_PARENT} (id, name, format, label_updated, updated_at)
            VALUES (%s, %s, %s, %s, %s) AS incoming
            ON DUPLICATE KEY UPDATE
                name = incoming.name,
                format = incoming.format,
                label_updated = incoming.label_updated,
                updated_at = incoming.updated_at
            """,
            (
                tid,
                str(doc.get("name") or tid),
                str(doc.get("format") or "DOCX"),
                str(doc.get("updated") or ""),
                updated_at or time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            ),
        )

    migrate_legacy_document_column(connection, _RENDER_PARENT, _RENDER_CHILD, "template_id", on_row=_on_migrate)
    from report_writing.infra.json_tables import drop_legacy_document_column

    drop_legacy_document_column(connection, _RENDER_PARENT)
    _schema_ready = True


def _template_owner_id(explicit: str | None = None) -> str:
    from report_writing.services.users import current_user_id, normalize_owner_id

    return normalize_owner_id(explicit or current_user_id())


def _owns_template(doc: dict[str, Any] | None, owner_id: str) -> bool:
    from report_writing.services.users import owns_user_resource

    if not doc:
        return False
    return owns_user_resource(owner_id, str(doc.get("user_id") or ""))


def _use_mysql() -> bool:
    from report_writing.infra.postgres import _connect, load_database_settings, mysql_available

    if not mysql_available():
        return False
    if _schema_ready:
        return True
    settings = load_database_settings()
    if settings is None:
        return False
    try:
        with _connect(settings) as connection:
            _ensure_render_schema(connection)
        return True
    except Exception as error:
        _log.warning("渲染模版改用本地目录存储：%s", error)
        return False


def _db_template_count_for_user(owner_id: str | None = None) -> int:
    return len(_db_list_templates(owner_id=owner_id))


def sync_render_templates_from_disk(home: Path) -> int:
    """将 data/render_templates  upsert 到 MySQL。"""
    if not _use_mysql():
        return 0
    saved = 0
    for meta_path in templates_root(home).glob("*/meta.json"):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(meta, dict):
            continue
        tid = str(meta.get("id") or meta_path.parent.name)
        html_path = meta_path.parent / "template.html"
        meta = dict(meta)
        meta["id"] = tid
        meta["html"] = html_path.read_text(encoding="utf-8") if html_path.is_file() else str(meta.get("html") or "")
        disk_ts = str(meta.get("updated") or "")
        try:
            existing = _db_get_template(tid)
            if existing is None or disk_ts >= str(existing.get("updated") or ""):
                _db_save_template(meta)
                saved += 1
        except Exception:
            continue
    return saved


def _db_list_templates(owner_id: str | None = None) -> list[RenderTemplateSummary]:
    from report_writing.infra.json_tables import fetch_document_child
    from report_writing.infra.postgres import _connect, load_database_settings

    uid = _template_owner_id(owner_id)
    settings = load_database_settings()
    if settings is None:
        return []
    items: list[RenderTemplateSummary] = []
    with _connect(settings) as connection:
        _ensure_render_schema(connection)
        rows = connection.execute(
            f"SELECT id FROM {_RENDER_PARENT} ORDER BY updated_at DESC LIMIT 500"
        ).fetchall()
        for row in rows or []:
            tid = str(row[0])
            doc = fetch_document_child(connection, _RENDER_CHILD, "template_id", tid)
            if doc is not None:
                if not _owns_template(doc, uid):
                    continue
                try:
                    items.append(_summary_from_meta(doc))
                    continue
                except Exception:
                    pass
            elif uid != "default":
                continue
            items.append(
                RenderTemplateSummary(
                    id=tid,
                    name=tid,
                    desc="",
                    format="DOCX",
                    updated="",
                    tone="",
                    margin="",
                    font="",
                    heading="",
                    header="",
                )
            )
    return items


def _db_get_template(template_id: str) -> dict[str, Any] | None:
    from report_writing.infra.json_tables import fetch_document_child
    from report_writing.infra.postgres import _connect, load_database_settings

    settings = load_database_settings()
    if settings is None:
        return None
    uid = _template_owner_id()
    with _connect(settings) as connection:
        _ensure_render_schema(connection)
        doc = fetch_document_child(connection, _RENDER_CHILD, "template_id", template_id)
        if doc is None:
            return None
        return doc if _owns_template(doc, uid) else None


def _db_save_template(data: dict[str, Any], *, owner_id: str | None = None) -> None:
    from report_writing.infra.json_tables import upsert_document_child
    from report_writing.infra.postgres import _connect, load_database_settings

    settings = load_database_settings()
    if settings is None:
        raise RuntimeError("未配置数据库")
    tid = _require_id(str(data.get("id") or ""))
    payload = dict(data)
    payload["id"] = tid
    payload["user_id"] = _template_owner_id(
        owner_id if owner_id is not None else str(payload.get("user_id") or "")
    )
    payload["updated"] = str(payload.get("updated") or _now_label())
    updated_at = time.strftime("%Y-%m-%dT%H:%M:%SZ")
    with _connect(settings) as connection:
        _ensure_render_schema(connection)
        connection.execute(
            f"""
            INSERT INTO {_RENDER_PARENT} (id, name, format, label_updated, updated_at)
            VALUES (%s, %s, %s, %s, %s) AS incoming
            ON DUPLICATE KEY UPDATE
                name = incoming.name,
                format = incoming.format,
                label_updated = incoming.label_updated,
                updated_at = incoming.updated_at
            """,
            (
                tid,
                str(payload.get("name") or tid),
                str(payload.get("format") or "DOCX"),
                str(payload.get("updated") or ""),
                updated_at,
            ),
        )
        upsert_document_child(connection, _RENDER_CHILD, "template_id", tid, payload)


def _db_delete_template(template_id: str) -> bool:
    from report_writing.infra.postgres import _connect, load_database_settings

    if _db_get_template(template_id) is None:
        return False
    settings = load_database_settings()
    if settings is None:
        return False
    with _connect(settings) as connection:
        _ensure_render_schema(connection)
        exists = connection.execute(
            f"SELECT 1 FROM {_RENDER_PARENT} WHERE id = %s",
            (template_id,),
        ).fetchone()
        if exists is None:
            return False
        connection.execute(f"DELETE FROM {_RENDER_CHILD} WHERE template_id = %s", (template_id,))
        connection.execute(f"DELETE FROM {_RENDER_PARENT} WHERE id = %s", (template_id,))
    return True


def _document_dict(payload) -> dict[str, Any]:
    if isinstance(payload, (bytes, bytearray)):
        payload = payload.decode("utf-8")
    if isinstance(payload, str):
        payload = json.loads(payload)
    if not isinstance(payload, dict):
        raise ValueError("document 格式无效")
    return payload


def _write_template(home: Path, data: dict[str, Any]) -> None:
    tid = _require_id(str(data.get("id") or ""))
    folder = templates_root(home) / tid
    folder.mkdir(parents=True, exist_ok=True)
    html = str(data.get("html") or "")
    (folder / "template.html").write_text(html, encoding="utf-8")
    meta = {
        "id": tid,
        "name": str(data.get("name") or tid),
        "desc": str(data.get("desc") or ""),
        "format": str(data.get("format") or "DOCX"),
        "updated": str(data.get("updated") or _now_label()),
        "tone": str(data.get("tone") or "linear-gradient(145deg,#e2e8f0,#f8fafc)"),
        "margin": str(data.get("margin") or ""),
        "font": str(data.get("font") or ""),
        "heading": str(data.get("heading") or ""),
        "header": str(data.get("header") or ""),
    }
    (folder / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def _summary_from_meta(meta: dict[str, Any]) -> RenderTemplateSummary:
    return RenderTemplateSummary(
        id=str(meta.get("id") or ""),
        name=str(meta.get("name") or ""),
        desc=str(meta.get("desc") or ""),
        format=str(meta.get("format") or "DOCX"),
        updated=str(meta.get("updated") or ""),
        tone=str(meta.get("tone") or ""),
        margin=str(meta.get("margin") or ""),
        font=str(meta.get("font") or ""),
        heading=str(meta.get("heading") or ""),
        header=str(meta.get("header") or ""),
    )


def _require_id(value: str) -> str:
    text = str(value or "").strip()
    if not _ID_SAFE.match(text):
        raise ValueError("模版编号不合法")
    return text


def _now_label() -> str:
    return time.strftime("%Y-%m-%d %H:%M")


def _escape(text: str) -> str:
    return (
        (text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _strip_fence(text: str) -> str:
    value = (text or "").strip()
    value = re.sub(r"^```(?:html|HTML)?\s*", "", value)
    value = re.sub(r"\s*```$", "", value)
    return value.strip()
