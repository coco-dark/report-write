"""把章节 HTML 片段写入 Word（python-docx）。"""

from __future__ import annotations

import io
import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlparse
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Cm, Inches, Pt, RGBColor
from docx.text.paragraph import Paragraph

from report_writing.domain.word_style import WordStyleSettings

_BLACK = RGBColor(0, 0, 0)


_BLOCK = {"p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li", "tr", "blockquote", "pre"}
_SKIP = {"script", "style", "head", "meta", "link"}
_TABLE_CAPTION_RE = re.compile(r"^表\s*[\d\.\-\w（）()]+")
_HTML_FILLER = (
    r"(?:\s|&nbsp;|&#160;|<br\s*/?>|<p[^>]*>(?:\s|&nbsp;|&#160;|<br\s*/?>)*</p>"
    r"|<div[^>]*>(?:\s|&nbsp;|&#160;|<br\s*/?>)*</div>)*"
)


def _attrs_map(attrs: list[tuple[str, str | None]]) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in attrs:
        if key and value is not None:
            out[key.lower()] = str(value)
    return out


def _classes(attrs: dict[str, str]) -> set[str]:
    return {part.strip() for part in (attrs.get("class") or "").split() if part.strip()}


def _int_attr(attrs: dict[str, str], name: str, default: int) -> int:
    try:
        return int(attrs.get(name) or default)
    except (TypeError, ValueError):
        return default


def _level_size(mapping: dict[int, float], level: int, default: float) -> float:
    return mapping.get(min(max(int(level or 1), 1), 6), default)


def _style_run(
    run,
    *,
    bold: bool | None = None,
    size_pt: float | None = None,
    font_name: str = "宋体",
) -> None:
    run.font.color.rgb = _BLACK
    if bold is not None:
        run.bold = bold
    if size_pt is not None:
        run.font.size = Pt(size_pt)
    run.font.name = font_name
    if run._element.rPr is not None and run._element.rPr.rFonts is not None:
        run._element.rPr.rFonts.set(qn("w:eastAsia"), font_name)


def _body_paragraph_format(paragraph, style: WordStyleSettings) -> None:
    fmt = paragraph.paragraph_format
    fmt.left_indent = Cm(0)
    fmt.first_line_indent = Cm(style.first_line_indent_cm)
    fmt.line_spacing = style.line_spacing
    fmt.space_after = Pt(6)


def _left_indent_by_level(paragraph, level: int, style: WordStyleSettings) -> None:
    depth = max(0, level - 1)
    paragraph.paragraph_format.left_indent = Cm(style.toc_indent_cm * depth)


def _paragraph_is_empty(paragraph) -> bool:
    if paragraph is None:
        return True
    if (paragraph.text or "").strip():
        return False
    for run in paragraph.runs:
        if (run.text or "").strip():
            return False
    return True


def _remove_paragraph(paragraph) -> None:
    if paragraph is None:
        return
    element = paragraph._element
    parent = element.getparent()
    if parent is not None:
        parent.remove(element)


def _strip_trailing_empty_paragraphs(document: Document) -> None:
    while document.paragraphs and _paragraph_is_empty(document.paragraphs[-1]):
        _remove_paragraph(document.paragraphs[-1])


def _take_pending_table_caption(document: Document) -> str:
    """表题单独成段时 Word 无法与表格 keep 在一起，并入表格首行。"""
    _strip_trailing_empty_paragraphs(document)
    if not document.paragraphs:
        return ""
    last = document.paragraphs[-1]
    text = (last.text or "").strip()
    if not text or not _TABLE_CAPTION_RE.match(text):
        return ""
    _remove_paragraph(last)
    return text


def _write_table_caption_row(table, caption: str, cols: int, style: WordStyleSettings) -> None:
    row = table.rows[0]
    cell = row.cells[0]
    if cols > 1:
        cell.merge(row.cells[cols - 1])
    cell.text = caption
    for paragraph in cell.paragraphs:
        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
        fmt = paragraph.paragraph_format
        fmt.space_before = Pt(0)
        fmt.space_after = Pt(3)
        fmt.first_line_indent = Cm(0)
        fmt.left_indent = Cm(0)
        for run in paragraph.runs:
            _style_run(run, bold=True, size_pt=style.body_pt, font_name=style.title_font)


def _tighten_table_spacing(document: Document) -> None:
    """去掉表格后的空段，并收紧紧随其后的正文段前距。"""
    body = document.element.body
    children = list(body)
    index = 0
    while index < len(children):
        if children[index].tag != qn("w:tbl"):
            index += 1
            continue
        while index > 0 and children[index - 1].tag == qn("w:p"):
            prev = Paragraph(children[index - 1], document)
            if not _paragraph_is_empty(prev):
                break
            body.remove(children[index - 1])
            children.pop(index - 1)
            index -= 1
        next_index = index + 1
        while next_index < len(children):
            element = children[next_index]
            if element.tag != qn("w:p"):
                break
            paragraph = Paragraph(element, document)
            if _paragraph_is_empty(paragraph):
                body.remove(element)
                children.pop(next_index)
                continue
            fmt = paragraph.paragraph_format
            fmt.space_before = Pt(0)
            fmt.space_after = Pt(6)
            break
        index += 1


class _HtmlToDocx(HTMLParser):
    def __init__(self, document: Document, *, style: WordStyleSettings) -> None:
        super().__init__(convert_charrefs=True)
        self.doc = document
        self.style = style
        self.paragraph = None
        self.para_kind = "body"
        self.bold = 0
        self.italic = 0
        self.underline = 0
        self.list_stack: list[str] = []
        self.skip = 0
        self.table = None
        self.table_rows: list[list[str]] = []
        self.row_cells: list[str] = []
        self.cell_buf: list[str] = []
        self.in_td = False
        self.para_level = 1
        self.block_depth = 0
        self.after_table = False

    def _discard_empty_open_paragraph(self) -> None:
        if _paragraph_is_empty(self.paragraph):
            _remove_paragraph(self.paragraph)
        self.paragraph = None

    def _start_paragraph(self, kind: str = "body", *, level: int = 1) -> None:
        self._discard_empty_open_paragraph()
        self.para_kind = kind
        self.para_level = level
        if kind == "chapter-title":
            word_level = min(max(level, 1), 9)
            self.paragraph = self.doc.add_heading("", level=word_level)
            fmt = self.paragraph.paragraph_format
            fmt.left_indent = Cm(0)
            fmt.first_line_indent = Cm(0)
            fmt.space_before = Pt(10 if level <= 2 else 6)
            fmt.space_after = Pt(6)
            return
        if kind == "toc-title":
            self.paragraph = self.doc.add_paragraph()
            self.paragraph.paragraph_format.left_indent = Cm(0)
            self.paragraph.paragraph_format.space_after = Pt(8)
            return
        if kind == "toc-entry":
            self.paragraph = self.doc.add_paragraph()
            _left_indent_by_level(self.paragraph, level, self.style)
            self.paragraph.paragraph_format.first_line_indent = Cm(0)
            self.paragraph.paragraph_format.space_after = Pt(2)
            return
        if kind == "subheading":
            self.paragraph = self.doc.add_paragraph()
            fmt = self.paragraph.paragraph_format
            fmt.left_indent = Cm(0)
            fmt.first_line_indent = Cm(0)
            fmt.space_before = Pt(6)
            fmt.space_after = Pt(4)
            return
        self.paragraph = self.doc.add_paragraph()
        _body_paragraph_format(self.paragraph, self.style)
        if self.after_table:
            self.paragraph.paragraph_format.space_before = Pt(0)
            self.after_table = False

    def _close_block_paragraph(self) -> None:
        if _paragraph_is_empty(self.paragraph):
            _remove_paragraph(self.paragraph)
        self.paragraph = None
        self.para_kind = "body"

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in _SKIP:
            self.skip += 1
            return
        if self.skip:
            return
        attr = _attrs_map(attrs)
        classes = _classes(attr)
        if tag in {"b", "strong"}:
            self.bold += 1
        elif tag in {"i", "em"}:
            self.italic += 1
        elif tag == "u":
            self.underline += 1
        elif tag == "br":
            if self.in_td:
                self.cell_buf.append("\n")
            elif self.after_table:
                return
            elif self.paragraph is not None:
                self.paragraph.add_run().add_break()
            else:
                self._start_paragraph("body")
                if self.paragraph is not None:
                    self.paragraph.add_run().add_break()
        elif tag in {"ul", "ol"}:
            self.list_stack.append(tag)
        elif tag == "li":
            style = "List Number" if (self.list_stack and self.list_stack[-1] == "ol") else "List Bullet"
            try:
                self.paragraph = self.doc.add_paragraph(style=style)
            except KeyError:
                self.paragraph = self.doc.add_paragraph()
            self.para_kind = "list"
            self.para_level = len(self.list_stack)
            self.paragraph.paragraph_format.left_indent = Cm(self.style.first_line_indent_cm)
            self.paragraph.paragraph_format.first_line_indent = Cm(0)
        elif tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            level = int(tag[1])
            self._start_paragraph("subheading", level=level)
        elif tag in {"p", "div", "blockquote", "pre", "article", "section", "main"}:
            if "chapter-title" in classes:
                self._start_paragraph("chapter-title", level=_int_attr(attr, "data-level", 2))
            elif "toc-title" in classes:
                self._start_paragraph("toc-title")
            elif "toc-entry" in classes:
                self._start_paragraph("toc-entry", level=_int_attr(attr, "data-level", 1))
            elif "subheading" in classes:
                self._start_paragraph("subheading", level=_int_attr(attr, "data-level", 3))
            elif tag == "div" and "report-toc" in classes:
                self.paragraph = None
            else:
                self.block_depth += 1
        elif tag == "table":
            self.table_rows = []
        elif tag == "img":
            src = str(attr.get("src") or attr.get("data-rw-file") or "").strip()
            path = None
            if src.startswith("file:"):
                path = Path(unquote(urlparse(src).path))
            elif src:
                path = Path(src)
            if path is not None and path.is_file():
                self.paragraph = self.doc.add_paragraph()
                self.paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
                run = self.paragraph.add_run()
                try:
                    run.add_picture(str(path), width=Inches(5.5))
                except Exception:
                    self.paragraph.add_run(f"[图片：{path.name}]")
                self.after_table = False
                self.paragraph = None
        elif tag == "tr":
            self.row_cells = []
        elif tag in {"td", "th"}:
            self.in_td = True
            self.cell_buf = []
        elif tag == "hr":
            self.doc.add_paragraph("────────")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in _SKIP:
            self.skip = max(0, self.skip - 1)
            return
        if self.skip:
            return
        if tag in {"b", "strong"}:
            self.bold = max(0, self.bold - 1)
        elif tag in {"i", "em"}:
            self.italic = max(0, self.italic - 1)
        elif tag == "u":
            self.underline = max(0, self.underline - 1)
        elif tag in {"ul", "ol"}:
            if self.list_stack:
                self.list_stack.pop()
        elif tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self._close_block_paragraph()
        elif tag in {"p", "div", "blockquote", "pre", "article", "section", "main"}:
            if self.block_depth > 0:
                self.block_depth = max(0, self.block_depth - 1)
            self._close_block_paragraph()
        elif tag == "li":
            self._close_block_paragraph()
        elif tag in {"td", "th"}:
            self.in_td = False
            self.row_cells.append("".join(self.cell_buf).strip())
            self.cell_buf = []
        elif tag == "tr":
            if self.row_cells:
                self.table_rows.append(self.row_cells)
            self.row_cells = []
        elif tag == "table":
            self._flush_table()

    def handle_data(self, data: str) -> None:
        if self.skip:
            return
        text = data
        if not text:
            return
        if self.in_td:
            self.cell_buf.append(text)
            return
        if self.paragraph is None:
            if not text.strip():
                return
            self._start_paragraph("body")
        run = self.paragraph.add_run(text)
        run.italic = self.italic > 0
        run.underline = self.underline > 0
        if self.para_kind == "chapter-title":
            _style_run(
                run,
                bold=True,
                size_pt=_level_size(self.style.chapter_title_pt, self.para_level, 14),
                font_name=self.style.title_font,
            )
        elif self.para_kind == "toc-title":
            _style_run(run, bold=True, size_pt=self.style.toc_title_pt, font_name=self.style.title_font)
        elif self.para_kind == "toc-entry":
            _style_run(run, bold=False, size_pt=self.style.toc_entry_pt, font_name=self.style.body_font)
        elif self.para_kind == "subheading":
            _style_run(
                run,
                bold=True,
                size_pt=self.style.subheading_pt,
                font_name=self.style.title_font,
            )
        elif self.para_kind == "list":
            _style_run(run, bold=self.bold > 0, size_pt=self.style.body_pt, font_name=self.style.body_font)
        else:
            _style_run(run, bold=self.bold > 0, size_pt=self.style.body_pt, font_name=self.style.body_font)

    def _flush_table(self) -> None:
        rows = [row for row in self.table_rows if any(cell.strip() for cell in row)]
        self.table_rows = []
        if not rows:
            return
        self.paragraph = None
        caption = _take_pending_table_caption(self.doc)
        cols = max(len(row) for row in rows)
        total_rows = len(rows) + (1 if caption else 0)
        table = self.doc.add_table(rows=total_rows, cols=cols)
        table.style = "Table Grid"
        offset = 0
        if caption:
            _write_table_caption_row(table, caption, cols, self.style)
            offset = 1
        for r_index, row in enumerate(rows):
            for c_index in range(cols):
                table.rows[r_index + offset].cells[c_index].text = row[c_index] if c_index < len(row) else ""
        self.after_table = True
        self.paragraph = None


def html_to_document(html: str, *, title: str = "", style: WordStyleSettings | None = None) -> Document:
    style = style or WordStyleSettings()
    doc = Document()
    _style_defaults(doc, style)
    if title.strip():
        heading = doc.add_heading(title.strip(), level=0)
        for run in heading.runs:
            _style_run(run, bold=True, size_pt=style.report_title_pt, font_name=style.title_font)
    parser = _HtmlToDocx(doc, style=style)
    parser.feed(_normalize_html(html or ""))
    parser.close()
    _tighten_table_spacing(doc)
    if not doc.paragraphs and not doc.tables:
        doc.add_paragraph("")
    return doc


def html_to_docx_bytes(html: str, *, title: str = "", style: WordStyleSettings | None = None) -> bytes:
    document = html_to_document(html, title=title, style=style)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _normalize_html(html: str) -> str:
    text = (html or "").strip()
    if not text:
        return "<p></p>"
    # 去掉 html/body 外壳，保留内部
    text = re.sub(r"(?is)</?(html|body|head)[^>]*>", "", text)
    text = re.sub(r"(?is)<!DOCTYPE[^>]*>", "", text)
    # 去掉无内容的块，避免 div/p 包裹产生大量空段
    text = re.sub(
        r"(?is)<(p|div|section|article|main)(\s[^>]*)?>(?:\s|&nbsp;|<br\s*/?>)*</\1>",
        "",
        text,
    )
    text = re.sub(r"(?:\s*<br\s*/?>\s*){3,}", "<br><br>", text, flags=re.IGNORECASE)
    text = re.sub(rf"(?is)</table>{_HTML_FILLER}", "</table>", text)
    text = re.sub(rf"(?is)</table>{_HTML_FILLER}(?=<p\b)", "</table>", text)
    text = re.sub(rf"(?is){_HTML_FILLER}(?=<table\b)", "", text)
    return text


def _style_defaults(document: Document, style: WordStyleSettings) -> None:
    normal = document.styles["Normal"]
    font = normal.font
    font.name = style.body_font
    font.size = Pt(style.body_pt)
    font.color.rgb = _BLACK
    if normal._element.rPr is not None and normal._element.rPr.rFonts is not None:
        normal._element.rPr.rFonts.set(qn("w:eastAsia"), style.body_font)
    for index in range(1, 10):
        try:
            heading = document.styles[f"Heading {index}"]
        except KeyError:
            continue
        heading.font.name = style.title_font
        heading.font.size = Pt(_level_size(style.chapter_title_pt, index, 14))
        heading.font.color.rgb = _BLACK
        if heading._element.rPr is not None and heading._element.rPr.rFonts is not None:
            heading._element.rPr.rFonts.set(qn("w:eastAsia"), style.title_font)
        try:
            heading.paragraph_format.left_indent = Cm(0)
            heading.paragraph_format.first_line_indent = Cm(0)
        except Exception:
            pass
