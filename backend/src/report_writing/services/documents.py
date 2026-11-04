"""从样板和上传资料中抽出正文。资料支持 Word、PDF、Excel、图片和文本。"""

from __future__ import annotations

import base64
import json
import re
import urllib.error
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

_HEADING = re.compile(
    r"^(?:(?P<marks>#{1,6})\s+)?"
    r"(?P<sequence>第[0-9一二三四五六七八九十百零]+[章节篇]|[一二三四五六七八九十]+|\d+(?:\.\d+)*)"
    r"[章节篇、.．\s　]+"
    r"(?P<title>\S.{0,80})$"
)
_STYLE_LEVEL = re.compile(r"(?:Heading|标题)\s*(\d+)", re.IGNORECASE)


MATERIAL_SUFFIXES = {
    ".md",
    ".txt",
    ".docx",
    ".pdf",
    ".xlsx",
    ".xls",
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".bmp",
    ".gif",
    ".tif",
    ".tiff",
}

_IMAGE_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".gif": "image/gif",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
}


def read_material(path: Path) -> list[str]:
    """读出资料中的段落。默认走安源 RAG 全文解析，离线时再本地抽取。"""
    cache = path.with_name(path.name + ".paragraphs.json")
    if cache.is_file():
        cached = json.loads(cache.read_text(encoding="utf-8"))
        if isinstance(cached, list) and cached:
            return [str(item) for item in cached if str(item).strip()]
    from report_writing.infra.rag_parse import paragraphs_from_markdown, parse_full_text, use_remote_parse

    if use_remote_parse():
        lines = paragraphs_from_markdown(parse_full_text(path))
        if not lines:
            raise ValueError(f"{path.name} 没有可读文字")
        cache.write_text(json.dumps(lines, ensure_ascii=False), encoding="utf-8")
        return lines
    suffix = path.suffix.lower()
    if suffix in {".md", ".txt"}:
        text = path.read_text(encoding="utf-8")
        lines = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    elif suffix == ".docx":
        lines = _read_docx(path)
    elif suffix == ".doc":
        raise ValueError("旧版 .doc 请另存为 .docx 后再上传")
    elif suffix == ".pdf":
        lines = _read_pdf(path)
    elif suffix in {".xlsx", ".xls"}:
        lines = _read_excel(path)
    elif suffix in _IMAGE_MIME:
        lines = _read_image(path)
    else:
        raise ValueError("资料请上传 Word、PDF、Excel、图片或文本")
    if not lines:
        raise ValueError(f"{path.name} 没有可读文字")
    cache.write_text(json.dumps(lines, ensure_ascii=False), encoding="utf-8")
    return lines


def read_document(path: Path) -> list[str]:
    return [block["text"] for block in read_blocks(path)]


def read_blocks_local(path: Path) -> list[dict]:
    """仅本地抽取模版块（不调用 RAG）。"""
    suffix = path.suffix.lower()
    if suffix == ".docx":
        return _read_docx_blocks(path)
    if suffix == ".pdf":
        return [{"text": line, "level": 0} for line in _read_pdf(path)]
    if suffix in {".md", ".txt"}:
        text = path.read_text(encoding="utf-8")
        return [{"text": line, "level": 0} for line in text.splitlines() if line.strip()]
    if suffix == ".doc":
        raise ValueError("旧版 .doc 请另存为 .docx 后再上传")
    raise ValueError("模版请上传 PDF 或 Word（.docx）")


def read_blocks(path: Path) -> list[dict]:
    """读出模版块。默认向 RAG 要 HTML，离线时再读本地大纲。"""
    from report_writing.infra.rag_parse import parse_full_text, use_remote_parse

    if use_remote_parse():
        return blocks_from_html(parse_full_text(path, result_type="html"))
    return read_blocks_local(path)


class _HtmlOutline(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[dict] = []
        self._kind = ""
        self._level = 0
        self._buf: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self._flush()
            self._kind = "heading"
            self._level = int(tag[1])
        elif tag in {"p", "li"}:
            self._flush()
            self._kind = "text"
            self._level = 0
        elif tag == "tr":
            self._flush()
            self._kind = "text"
            self._level = 0

    def handle_endtag(self, tag: str) -> None:
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "tr"}:
            self._flush()

    def handle_data(self, data: str) -> None:
        if self._kind:
            self._buf.append(data)
        elif data.strip():
            self.blocks.append({"text": " ".join(data.split()), "level": 0})

    def close(self) -> None:
        self._flush()
        super().close()

    def _flush(self) -> None:
        text = " ".join("".join(self._buf).split())
        if text:
            self.blocks.append({"text": text, "level": self._level if self._kind == "heading" else 0})
        self._kind = ""
        self._level = 0
        self._buf = []


def blocks_from_html(html: str) -> list[dict]:
    """按 HTML 标题层级切块。没有标题标签时，退回按行识别「第一章」「1.2」。"""
    parser = _HtmlOutline()
    parser.feed(html or "")
    parser.close()
    if any(block["level"] for block in parser.blocks):
        return parser.blocks
    marked = re.sub(r"<b>([^<]*)</b>", r"**\1**", html or "", flags=re.IGNORECASE)
    plain = re.sub(r"<[^>]+>", "\n", marked)
    return [{"text": line.strip(), "level": 0} for line in plain.splitlines() if line.strip()]


def chapters_from_lines(lines: list[str]) -> list[dict]:
    return chapters_from_blocks([{"text": line, "level": 0} for line in lines])


def chapters_from_blocks(blocks: list[dict]) -> list[dict]:
    sections: list[dict] = []
    current: dict | None = None
    for block in blocks:
        text = " ".join(str(block.get("text") or "").split())
        if not text:
            continue
        explicit = int(block.get("level") or 0)
        heading = _heading_parts(text, explicit, bool(block.get("trust")))
        if heading:
            sequence, title, level = heading
            current = {
                "sequence": sequence,
                "title": title,
                "level": level,
                "paragraphs": [],
            }
            sections.append(current)
            continue
        if current is None:
            current = {"sequence": "", "title": "正文", "level": 1, "paragraphs": []}
            sections.append(current)
        current["paragraphs"].append(text)
    if not sections:
        return []
    chapters = []
    stack: list[tuple[int, str]] = []
    for index, section in enumerate(sections, start=1):
        level = int(section["level"] or 1)
        while stack and stack[-1][0] >= level:
            stack.pop()
        key = f"ch-{index}"
        parent = stack[-1][1] if stack else ""
        chapters.append(
            {
                "key": key,
                "sequence": section.get("sequence") or "",
                "title": section["title"],
                "level": level,
                "parent": parent,
                "points": [],
                "sample_paragraphs": section["paragraphs"],
                "boundary": "",
                "depends_on": [f"ch-{index - 1}"] if index > 1 else [],
            }
        )
        stack.append((level, key))
    return chapters


_STARS = re.compile(r"\*+")
_YEAR = re.compile(r"^20\d{2}(?:\.\d{1,2}){0,2}$")
_ONLY_NUMBER = re.compile(r"^\d+(?:\.\d+)*$")
_BOLD_LINE = re.compile(r"^\*\*(.+)\*\*$")
_GLUED_NUMBER = re.compile(r"^(?P<sequence>\d+(?:\.\d+)+)(?P<title>[\u4e00-\u9fff].{0,40})$")
_SKIP_TITLES = {"序号", "文号", "编号", "单位", "项目", "内容", "文件名称", "实施日期", "完成时间", "附图", "附件"}


def _clean_heading_text(text: str) -> str:
    return _STARS.sub("", text).strip()


def _accept_heading(sequence: str, title: str, trust: bool) -> bool:
    if not title or _ONLY_NUMBER.fullmatch(title) or re.fullmatch(r"[\d\s./%:+-]+", title):
        return False
    if _YEAR.fullmatch(sequence or ""):
        return False
    if re.fullmatch(r"\d+", sequence or "") and int(sequence) > 30:
        return False
    if trust:
        return True
    if title.endswith(("。", "；")) or len(title) > 40:
        return False
    if re.fullmatch(r"\d+", sequence or "") and len(title) > 22:
        return False
    return True


def _heading_parts(text: str, explicit: int, trust: bool) -> tuple[str, str, int] | None:
    raw = text.strip()
    bold = _BOLD_LINE.fullmatch(raw)
    cleaned = _clean_heading_text(bold.group(1) if bold else raw)
    glued = _GLUED_NUMBER.match(cleaned)
    if glued and _accept_heading(glued.group("sequence"), glued.group("title"), trust):
        sequence = glued.group("sequence")
        title = glued.group("title")
        return sequence, title, _heading_level(sequence, "", explicit)
    match = _HEADING.match(cleaned)
    if match:
        sequence = match.group("sequence").strip()
        title = _clean_heading_text(match.group("title"))
        separator = cleaned[match.end("sequence"):match.start("title")]
        if not _restarted_list(sequence, separator) and _accept_heading(sequence, title, trust):
            return sequence, title, _heading_level(sequence, match.group("marks") or "", explicit)
    if bold and _plain_title(cleaned):
        return "", cleaned[:40], explicit or 1
    if trust and explicit and _accept_heading("", cleaned, True):
        return "", cleaned[:80], explicit
    return None


def _restarted_list(sequence: str, separator: str) -> bool:
    """「1.道路布置」「2、管沟」是节内条目，不是新的章节层级。"""
    return bool(re.fullmatch(r"\d+", sequence or "") and re.fullmatch(r"[\.、．]+", separator.strip()))


def _plain_title(text: str) -> bool:
    if not text or text in _SKIP_TITLES or len(text) > 20:
        return False
    if text.endswith(("。", "；", "：", ":")) or re.search(r"\d", text):
        return False
    return len(text) >= 2


def _heading_level(sequence: str, marks: str, explicit: int) -> int:
    if explicit > 0:
        return explicit
    if marks:
        return len(marks)
    if re.fullmatch(r"\d+(?:\.\d+)*", sequence):
        return sequence.count(".") + 1
    return 1


def template_document(path: Path, biz_type: str, version: str = "1") -> dict:
    chapters = chapters_from_blocks(read_blocks(path))
    if not chapters:
        raise ValueError("模版里没有可读正文")
    return {
        "id": f"{biz_type}-{path.stem}",
        "version": version,
        "title": path.stem,
        "biz_type": biz_type,
        "chapters": chapters,
    }


def _read_docx(path: Path) -> list[str]:
    return [block["text"] for block in _read_docx_blocks(path)]


def _read_docx_blocks(path: Path) -> list[dict]:
    """按正文顺序抽出段落与表格。表格只落成纯文本行（| 分隔），结构交给大模型分析。"""
    from docx import Document
    from docx.oxml.ns import qn
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    document = Document(str(path))
    blocks: list[dict] = []
    for child in document.element.body.iterchildren():
        if child.tag == qn("w:p"):
            paragraph = Paragraph(child, document)
            text = paragraph.text.strip()
            if not text:
                if _paragraph_has_drawing(paragraph):
                    blocks.append({"text": "[图片] 图片占位", "level": 0, "kind": "image"})
                continue
            level = _docx_level(paragraph)
            blocks.append({"text": text, "level": level, "trust": level > 0, "kind": "text"})
            if _paragraph_has_drawing(paragraph):
                blocks.append({"text": "[图片] 图片占位", "level": 0, "kind": "image"})
        elif child.tag == qn("w:tbl"):
            table = Table(child, document)
            # 仅转成可读纯文本，不做行列/合并推断
            lines = ["[表格]"]
            for row in table.rows:
                cells = [" ".join((cell.text or "").split()) for cell in row.cells]
                # python-docx 合并格会重复同一 cell，去重保序便于阅读
                deduped = list(dict.fromkeys(cells)) if cells else cells
                if any(deduped):
                    lines.append(" | ".join(item or "（空）" for item in deduped))
            blocks.append({"text": "\n".join(lines), "level": 0, "kind": "text"})
    return blocks


def _paragraph_has_drawing(paragraph) -> bool:
    xml = paragraph._p.xml if paragraph is not None else ""
    return "w:drawing" in xml or "v:imagedata" in xml or "w:pict" in xml


def _docx_level(paragraph) -> int:
    style_name = paragraph.style.name if paragraph.style is not None else ""
    match = _STYLE_LEVEL.search(style_name or "")
    if match:
        return int(match.group(1))
    properties = paragraph._p.pPr
    outline = properties.outlineLvl if properties is not None else None
    if outline is not None and outline.val is not None:
        return int(outline.val) + 1
    return 0


def _read_excel(path: Path) -> list[str]:
    if path.suffix.lower() == ".xls":
        return _read_xls(path)
    from openpyxl import load_workbook

    book = load_workbook(path, data_only=True, read_only=True)
    try:
        lines: list[str] = []
        for sheet in book.worksheets:
            for row in sheet.iter_rows(values_only=True):
                cells = [_cell_text(cell) for cell in row]
                cells = [cell for cell in cells if cell]
                if cells:
                    lines.append(f"{sheet.title} " + " ".join(cells))
        return lines
    finally:
        book.close()


def _read_xls(path: Path) -> list[str]:
    import xlrd

    book = xlrd.open_workbook(str(path))
    lines: list[str] = []
    for sheet in book.sheets():
        for index in range(sheet.nrows):
            cells = [_cell_text(sheet.cell_value(index, column)) for column in range(sheet.ncols)]
            cells = [cell for cell in cells if cell]
            if cells:
                lines.append(f"{sheet.name} " + " ".join(cells))
    return lines


def _cell_text(value) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _read_image(path: Path) -> list[str]:
    from report_writing.infra.bailian import load_bailian_settings

    settings = load_bailian_settings()
    if not settings.api_key:
        raise ValueError("未读到百炼 api-key，无法识别图片文字")
    mime = _IMAGE_MIME[path.suffix.lower()]
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    payload = {
        "model": "qwen-vl-ocr-latest",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}},
                    {"type": "text", "text": "提取图片中的全部文字。只输出原文，保留换行，不要解释。"},
                ],
            }
        ],
    }
    request = urllib.request.Request(
        settings.base_url.rstrip("/") + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {settings.api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")[:300]
        raise ValueError(f"图片文字识别失败：{detail or error.reason}") from error
    except urllib.error.URLError as error:
        raise ValueError(f"图片文字识别失败：{error.reason}") from error
    message = ((body.get("choices") or [{}])[0].get("message") or {})
    content = message.get("content") or ""
    if isinstance(content, list):
        content = "\n".join(
            part.get("text", "") if isinstance(part, dict) else str(part) for part in content
        )
    return [line.strip() for line in str(content).splitlines() if line.strip()]


def _read_pdf(path: Path) -> list[str]:
    from pypdf import PdfReader

    lines: list[str] = []
    for page in PdfReader(str(path)).pages:
        text = page.extract_text() or ""
        lines.extend(part.strip() for part in text.splitlines() if part.strip())
    return lines
