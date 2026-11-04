"""Task controller helpers."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path

from report_writing.agents.supervisor import run_supervisor
from report_writing.services.corpus import load_template, normalize_biz_type
from report_writing.infra.llm import live_model
from report_writing.domain.models import (
    ChapterRequirement,
    ChapterState,
    OutlineNode,
    OutlineTree,
    WritingTask,
)
from report_writing.domain.outline import (
    finalize_outline,
    load_outline_json,
    outline_as_text,
    outline_from_brief_chapters,
    outline_from_payload,
    outline_from_text,
    read_template_text,
    slice_toc_region,
    template_from_outline,
)
from report_writing.infra.postgres import open_library
from report_writing.services.store import BriefCache, TaskStore

_log = logging.getLogger("uvicorn.error")

_INLINE_HEADING_BREAK = re.compile(
    r"(?<!\n)(?=(?<![\d.])\d+(?:\.\d+){1,}[.\s　]*[\u4e00-\u9fff])"
)
_HEADING_AT = re.compile(
    r"(?<![\d.])(\d+(?:\.\d+){1,})(?:\*\*)?[.\s　]*\*{0,2}[\u4e00-\u9fffA-Za-z]"
)


def _break_inline_headings(text: str) -> str:
    raw = text or ""
    if raw.count("\n") >= max(3, raw.count(".") // 2):
        return raw
    return _INLINE_HEADING_BREAK.sub("\n", raw)


def _excerpt_by_heading(text: str, sequence: str, title: str = "") -> str:
    """在全文中按序号定位章节片段（支持无换行的粘贴）。"""
    seq = (sequence or "").strip()
    if not seq or not (text or "").strip():
        return ""
    body = text or ""
    title = (title or "").strip()
    starters = []
    if title:
        starters.append(re.escape(seq) + r"[.\s　]*" + re.escape(title))
        starters.append(re.escape(seq) + re.escape(title))
    starters.append(re.escape(seq) + r"(?=[.\s　]|[^\d.])")
    start = -1
    for pattern in starters:
        match = re.search(pattern, body)
        if match:
            start = match.start()
            break
    if start < 0:
        return ""
    # 切到下一个非本村子孙的标题（2.3 会包含 2.3.1…，到 2.4/3 为止）
    tail = body[start + len(seq) :]
    end_rel = len(tail)
    for match in _HEADING_AT.finditer(tail):
        other = match.group(1)
        if other == seq or other.startswith(seq + "."):
            continue
        end_rel = match.start()
        break
    chunk = body[start : start + len(seq) + end_rel].strip()
    from report_writing.domain.chapter_locator import CHAPTER_EXCERPT_MAX_CHARS

    return chunk[:CHAPTER_EXCERPT_MAX_CHARS]


def _excerpt_body_score(excerpt: str, title: str = "", sequence: str = "") -> int:
    """评估切段「可学正文」含量：去掉标题行后的有效字符数。"""
    body_lines = []
    title = (title or "").strip()
    sequence = (sequence or "").strip()
    for line in (excerpt or "").splitlines():
        item = line.strip()
        if not item:
            continue
        if sequence and (item == sequence or item.startswith(sequence + " ") or item.startswith(sequence + "　")):
            rest = item[len(sequence) :].strip(" .．、　")
            if not rest or rest == title:
                continue
        if title and (item == title or item.endswith(title)):
            continue
        body_lines.append(item)
    return sum(len(line) for line in body_lines)


def _best_chapter_excerpt(
    full_text: str,
    *,
    sequence: str,
    title: str,
    span_paragraphs: list[str] | None = None,
    allow_full_fallback: bool = False,
    heading_index=None,
) -> tuple[str, bool]:
    """择优切章：标题索引边界切段优先，其次目录行/序号切段，并滑动窗修边界。"""
    from report_writing.domain.chapter_locator import locate_chapter

    from report_writing.domain.chapter_locator import CHAPTER_EXCERPT_MAX_CHARS

    cap = CHAPTER_EXCERPT_MAX_CHARS
    locator_excerpt = ""
    try:
        locator_excerpt, locator_ok = locate_chapter(
            full_text or "",
            sequence=sequence or "",
            title=title or "",
            index=heading_index,
            max_chars=cap,
            refine=True,
        )
        if not locator_ok:
            locator_excerpt = ""
    except Exception as error:
        _log.warning("标题索引切章失败 %s %s: %s", sequence, title, error)
        locator_excerpt = ""

    span_excerpt = "\n".join(span_paragraphs or []).strip()
    heading_excerpt = _excerpt_by_heading(full_text, sequence or "", title or "").strip()
    candidates = []
    if locator_excerpt:
        candidates.append(locator_excerpt)
    if span_excerpt:
        candidates.append(span_excerpt)
    if heading_excerpt and heading_excerpt not in candidates:
        candidates.append(heading_excerpt)
    if not candidates and allow_full_fallback and (full_text or "").strip():
        return (full_text or "").strip()[:cap], True
    if not candidates:
        return "", False

    from report_writing.domain.chapter_locator import _has_table_body, _promises_table

    def _missing_promised_table(item: str) -> bool:
        return bool(item) and _promises_table(item) and not _has_table_body(item)

    # 「见下表」却无表体的候选视为残缺，优先换更长/含表候选
    candidates = [c for c in candidates if not _missing_promised_table(c)] or list(
        filter(None, [locator_excerpt, span_excerpt, heading_excerpt])
    )

    # 有序号且索引切段已对齐时，优先用定位结果（即使较短），避免序号正则把后文其它章拼进来
    if sequence and locator_excerpt and _excerpt_sequence_aligned(locator_excerpt, sequence):
        if not _missing_promised_table(locator_excerpt):
            foreign_heading = heading_excerpt and _excerpt_contains_foreign_heading(heading_excerpt, sequence)
            foreign_span = span_excerpt and _excerpt_contains_foreign_heading(span_excerpt, sequence)
            if (not foreign_heading and not foreign_span) or _excerpt_body_score(locator_excerpt, title, sequence) >= 8:
                if not _excerpt_contains_foreign_heading(locator_excerpt, sequence):
                    return locator_excerpt[:cap], True

    def _rank_excerpt(item: str) -> tuple:
        aligned = _excerpt_sequence_aligned(item, sequence)
        # 有目标序号时：绝不选用明显属于其它数字标题开头的片段
        if sequence and not aligned and _excerpt_foreign_sequence(item, sequence):
            return (-1, 0)
        table_ok = 1 if (_has_table_body(item) or not _promises_table(item)) else 0
        return (table_ok, 1 if aligned else 0, _excerpt_body_score(item, title, sequence))

    best = max(candidates, key=_rank_excerpt)
    if sequence and _excerpt_foreign_sequence(best, sequence) and not _excerpt_sequence_aligned(best, sequence):
        aligned_only = [item for item in candidates if _excerpt_sequence_aligned(item, sequence)]
        if aligned_only:
            best = max(aligned_only, key=lambda item: _excerpt_body_score(item, title, sequence))
        else:
            return "", False
    # 行切与序号切都几乎没正文时，仍返回较长原文（可能含表格粘在标题后）
    if _excerpt_body_score(best, title, sequence) < 24:
        longer_pool = [
            item
            for item in (locator_excerpt, heading_excerpt, span_excerpt)
            if item and (not sequence or _excerpt_sequence_aligned(item, sequence) or not _excerpt_foreign_sequence(item, sequence))
        ]
        longer = max(longer_pool, key=len, default=best)
        if len(longer) > len(best):
            best = longer
    return best[:cap], bool(best.strip())


def _excerpt_sequence_aligned(excerpt: str, sequence: str) -> bool:
    seq = (sequence or "").strip()
    if not seq:
        return True
    head = (excerpt or "").lstrip()
    head = re.sub(r"^#{1,6}\s+", "", head)
    head = re.sub(r"^\*\*", "", head)
    return bool(re.match(re.escape(seq) + r"(?=$|[.\s　]|[^\d.])", head))


def _excerpt_foreign_sequence(excerpt: str, sequence: str) -> bool:
    """片段开头是另一个数字序号（且不是目标序号）。"""
    seq = (sequence or "").strip()
    head = (excerpt or "").lstrip()
    head = re.sub(r"^#{1,6}\s+", "", head)
    head = re.sub(r"\*+", "", head).strip()
    match = re.match(r"(\d+(?:\.\d+)*)", head)
    if not match:
        return False
    other = match.group(1)
    return bool(other) and other != seq


def _excerpt_contains_foreign_heading(excerpt: str, sequence: str) -> bool:
    """片段中是否出现其它章的数字标题行（如目标 1.5.1 却含 ## 7.4）。"""
    seq = (sequence or "").strip()
    if not seq or not (excerpt or "").strip():
        return False
    for raw in (excerpt or "").splitlines():
        plain = re.sub(r"^#{1,6}\s+", "", raw.strip())
        plain = re.sub(r"\*+", "", plain).strip()
        match = re.match(r"(\d+(?:\.\d+)*)(?=$|[.\s　]|[^\d.])", plain)
        if not match:
            continue
        other = match.group(1)
        if other == seq or other.startswith(seq + "."):
            continue
        # 行内像标题：序号后跟中文/短标题，而非表格单元格长句
        rest = plain[len(other) :].strip(" .．、　")
        if rest and len(plain) <= 60 and re.search(r"[\u4e00-\u9fffA-Za-z]", rest):
            return True
    return False


def _plain_from_rich(raw: str) -> str:
    """把粘贴的富文本/HTML 收成纯文本，便于按目录切章。

    Word 复制片段时 text/plain 常从 1 重排；HTML 里 <!--[if !supportLists]-->2.
    或 mso-list:Ignore 仍保留屏幕上的真实序号，优先从这些标记还原。
    """
    text = raw or ""
    if "<" in text and ">" in text:
        text = _word_list_markers_to_text(text)
        text = _html_lists_to_text(text)
        text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", text)
        text = re.sub(r"(?i)<br\s*/?>", "\n", text)
        text = re.sub(r"(?i)</p\s*>", "\n", text)
        text = re.sub(r"(?i)</div\s*>", "\n", text)
        text = re.sub(r"(?i)</h[1-6]\s*>", "\n", text)
        text = re.sub(r"(?i)</li\s*>", "\n", text)
        text = re.sub(r"(?s)<[^>]+>", "", text)
        text = (
            text.replace("&nbsp;", " ")
            .replace("\xa0", " ")
            .replace("&amp;", "&")
            .replace("&lt;", "<")
            .replace("&gt;", ">")
            .replace("&quot;", '"')
        )
    text = _normalize_sequence_chars(text)
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def _word_list_markers_to_text(html: str) -> str:
    """把 Word HTML 里隐藏的列表序号变成可见文本前缀。"""

    def marker_text(inner: str) -> str:
        plain = re.sub(r"(?s)<[^>]+>", "", inner or "")
        plain = (
            plain.replace("&nbsp;", " ")
            .replace("\xa0", " ")
            .replace("&amp;", "&")
            .replace("&lt;", "<")
            .replace("&gt;", ">")
        )
        plain = re.sub(r"\s+", " ", plain).strip()
        if not plain:
            return ""
        # 「2.」「2.3.1」「2、」等
        match = re.match(r"^(\d+(?:\.\d+)*\.?|[一二三四五六七八九十百]+[、.]?)\s*", plain)
        if match:
            return match.group(1).rstrip(".") + " "
        return plain + " "

    text = re.sub(
        r"(?is)<!--\[if\s*!supportLists\]-->(.*?)<!--\[endif\]-->",
        lambda m: marker_text(m.group(1)),
        html or "",
    )
    text = re.sub(
        r'(?is)<span\b[^>]*mso-list\s*:\s*Ignore[^>]*>(.*?)</span>',
        lambda m: marker_text(m.group(1)),
        text,
    )
    return text


def _normalize_sequence_chars(text: str) -> str:
    """全角数字/点号转半角，避免 ２．３．１ 解析失败或错挂。"""
    trans = str.maketrans(
        {
            "．": ".",
            "。": ".",
            "、": " ",
            **{chr(ord("０") + i): str(i) for i in range(10)},
        }
    )
    return (text or "").translate(trans)


def _html_lists_to_text(html: str) -> str:
    """把有序清单尽量还原成「序号 + 标题」行，减少剥标签后丢号。"""

    def replace_ol(match: re.Match) -> str:
        attrs = match.group(1) or ""
        body = match.group(2) or ""
        start_match = re.search(r'(?i)\bstart\s*=\s*["\']?(\d+)', attrs)
        index = int(start_match.group(1)) if start_match else 1
        lines = []
        for li in re.finditer(r"(?is)<li\b([^>]*)>(.*?)</li>", body):
            li_attrs = li.group(1) or ""
            li_body = li.group(2) or ""
            value_match = re.search(r'(?i)\bvalue\s*=\s*["\']?(\d+)', li_attrs)
            number = int(value_match.group(1)) if value_match else index
            inner = re.sub(r"(?s)<[^>]+>", "", li_body)
            inner = (
                inner.replace("&nbsp;", " ")
                .replace("&amp;", "&")
                .replace("&lt;", "<")
                .replace("&gt;", ">")
            )
            inner = re.sub(r"\s+", " ", inner).strip()
            if inner:
                # 若正文已自带 2.3.1 这类序号，不再套 ol 序号
                if re.match(r"^\d+(?:\.\d+)*\s+\S", inner):
                    lines.append(inner)
                else:
                    lines.append(f"{number} {inner}")
            index = number + 1
        return "\n" + "\n".join(lines) + "\n"

    previous = None
    text = html
    # 由内向外处理嵌套 ol
    while previous != text:
        previous = text
        text = re.sub(r"(?is)<ol\b([^>]*)>(.*?)</ol>", replace_ol, text)
    return text


