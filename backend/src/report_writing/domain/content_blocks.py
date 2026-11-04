"""章节内容块规范化，以及 write 输出 HTML 完整性。

表格结构（行列、合并等）一律由大模型根据纯文本推断，代码不做表格结构解析。
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Any


_VOID = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}


def _row_from_scalar(value: Any) -> list[str] | None:
    text = str(value or "").strip()
    if not text:
        return None
    if "|" in text:
        return [part.strip() for part in text.split("|")]
    if "\t" in text:
        return [part.strip() for part in text.split("\t")]
    return [text]


def normalize_cells_sample(raw: Any) -> list[list[str]]:
    """将 cells_sample 规范为 list[list[str]]。

    模型常把整行写成字符串（而非单元格数组）。一维字符串列表按「每项一行」处理。
    """
    if raw is None:
        return []
    if isinstance(raw, str):
        row = _row_from_scalar(raw)
        return [row] if row else []
    if not isinstance(raw, list):
        return []
    out: list[list[str]] = []
    for row in raw:
        if isinstance(row, (list, tuple)):
            out.append([str(cell).strip() for cell in row])
            continue
        if isinstance(row, dict):
            nested = row.get("cells") or row.get("row") or row.get("values")
            if isinstance(nested, (list, tuple)):
                out.append([str(cell).strip() for cell in nested])
                continue
            parsed = _row_from_scalar(row.get("text") or row.get("value") or "")
            if parsed:
                out.append(parsed)
            continue
        parsed = _row_from_scalar(row)
        if parsed:
            out.append(parsed)
    return out


def normalize_content_blocks(raw: list | None) -> list[dict[str, Any]]:
    """规范化大模型返回的 content_blocks；不做结构推断。"""
    blocks: list[dict[str, Any]] = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "text").strip().lower()
        if kind in ("表格", "table"):
            kind = "table"
        elif kind in ("图片", "image", "img", "figure"):
            kind = "image"
        else:
            kind = "text"
        merges = []
        for merge in item.get("merges") or []:
            if not isinstance(merge, dict):
                continue
            merges.append(
                {
                    "row": int(merge.get("row") or 0),
                    "col": int(merge.get("col") or 0),
                    "rowspan": max(1, int(merge.get("rowspan") or 1)),
                    "colspan": max(1, int(merge.get("colspan") or 1)),
                }
            )
        headers = [str(h).strip() for h in (item.get("headers") or []) if str(h).strip()]
        cells = normalize_cells_sample(item.get("cells_sample"))
        rows = int(item.get("rows") or 0)
        cols = int(item.get("cols") or 0)
        note = str(item.get("note") or "").strip()
        column_roles: list[dict[str, Any]] = []
        for role in item.get("column_roles") or []:
            if not isinstance(role, dict):
                continue
            header = str(role.get("header") or role.get("name") or "").strip()
            r = str(role.get("role") or "").strip().lower()
            if r in {"固定", "fixed", "template", "label"}:
                r = "fixed"
            elif r in {"不固定", "可变", "variable", "project", "fill"}:
                r = "variable"
            elif r in {"混合", "mixed", "both"}:
                r = "mixed"
            else:
                r = r or "mixed"
            if not header and not str(role.get("note") or "").strip():
                continue
            column_roles.append(
                {
                    "header": header,
                    "role": r,
                    "note": str(role.get("note") or "").strip()[:200],
                }
            )
        fixed_items = [
            str(x).strip()
            for x in (item.get("fixed_items") or item.get("fixed") or [])
            if str(x).strip()
        ][:40]
        variable_items = [
            str(x).strip()
            for x in (item.get("variable_items") or item.get("variable") or [])
            if str(x).strip()
        ][:40]
        if kind == "table" and not note:
            bits = []
            if rows or cols:
                bits.append(f"约 {rows} 行 × {cols} 列")
            if headers:
                bits.append("列：" + "、".join(headers[:12]))
            if fixed_items:
                bits.append(f"固定项 {len(fixed_items)} 个")
            if variable_items:
                bits.append(f"可变项 {len(variable_items)} 个")
            note = "表格（" + "；".join(bits) + "）" if bits else "表格（结构见分析）"
        if kind == "image" and not note:
            note = "图片占位，写作时输出 figure 占位，不生成真实图片。"
        blocks.append(
            {
                "kind": kind,
                "note": note,
                "rows": rows,
                "cols": cols,
                "headers": headers,
                "merges": merges,
                "cells_sample": cells,
                "column_roles": column_roles,
                "fixed_items": fixed_items,
                "variable_items": variable_items,
            }
        )
    return blocks


def ensure_html_fragment(text: str) -> str:
    """保证片段是完整 HTML：无标签包 <p>；未闭合标签自动补上；纯文本表格提升为 <table>。"""
    raw = (text or "").strip()
    if not raw:
        return "<p></p>"
    if raw in ("【图片】", "[图片]", "图片占位"):
        return '<figure class="image-placeholder"><figcaption>图片占位</figcaption></figure>'
    if not re.search(r"<[a-zA-Z]", raw):
        as_table = _plain_text_to_table_html(raw)
        if as_table:
            return as_table
        return f"<p>{_escape_text(raw)}</p>"
    repaired = _close_open_tags(raw)
    stripped = repaired.lstrip().lower()
    if not (
        stripped.startswith("<p")
        or stripped.startswith("<table")
        or stripped.startswith("<figure")
        or stripped.startswith("<ul")
        or stripped.startswith("<ol")
        or stripped.startswith("<div")
        or stripped.startswith("<h")
        or stripped.startswith("<section")
    ):
        repaired = f"<p>{repaired}</p>"
    return promote_tables_in_html(repaired)


def promote_tables_in_html(html: str) -> str:
    """把预览/正文里仍是纯文本或 Markdown 的表格提升为 <table>。已有 <table> 不动。"""
    raw = (html or "").strip()
    if not raw or re.search(r"(?is)<table\b", raw):
        return raw

    # 整段无标签：直接尝试
    if not re.search(r"<[a-zA-Z]", raw):
        return _plain_text_to_table_html(raw) or f"<p>{_escape_text(raw)}</p>"

    # 连续 <p>…</p> 组成的伪表格 → 合并成一张表
    paras = re.findall(r"(?is)<p\b[^>]*>(.*?)</p>", raw)
    if len(paras) >= 2:
        lines = [re.sub(r"(?s)<[^>]+>", "", p).strip() for p in paras]
        lines = [ln for ln in lines if ln]
        table = _lines_to_table_html(lines)
        if table:
            # 若原文几乎全是这些 p，则整段替换
            without_p = re.sub(r"(?is)<p\b[^>]*>.*?</p>", "", raw).strip()
            if not without_p or len(without_p) < 8:
                return table
            return re.sub(r"(?is)(?:\s*<p\b[^>]*>.*?</p>)+", f"\n{table}\n", raw, count=1)

    plain = strip_html(raw)
    md = _markdown_pipe_table_to_html(plain)
    if md and "<table" in md and not re.search(r"(?is)<(ul|ol|table|h[1-6])\b", raw):
        return md
    as_table = _plain_text_to_table_html(plain)
    if as_table and plain.count("\n") >= 2:
        # 仅当 strip 后仍像表格且原 HTML 几乎无结构时替换
        if not re.search(r"(?is)<(ul|ol|table|h[1-6])\b", raw):
            return as_table
    return raw


def _plain_text_to_table_html(text: str) -> str | None:
    lines = [ln.rstrip() for ln in str(text or "").splitlines()]
    lines = [ln for ln in lines if ln.strip()]
    if len(lines) < 2:
        return None
    # 表题单独保留
    caption = ""
    body_lines = list(lines)
    if re.match(r"^表[\d.\-－—]*", body_lines[0].strip()) or "表" in body_lines[0][:6]:
        if not _split_table_row(body_lines[0]):
            caption = body_lines[0].strip()
            body_lines = body_lines[1:]
    table = _lines_to_table_html(body_lines)
    if not table:
        return None
    if caption:
        return f"<p><strong>{_escape_text(caption)}</strong></p>\n{table}"
    return table


def _markdown_pipe_table_to_html(text: str) -> str | None:
    lines = [ln.strip() for ln in str(text or "").splitlines() if ln.strip()]
    pipe_rows = [ln for ln in lines if ln.count("|") >= 2]
    if len(pipe_rows) < 2:
        return None
    rows: list[list[str]] = []
    for ln in pipe_rows:
        if re.match(r"^\|?\s*:?-{3,}", ln.replace(" ", "")):
            continue
        cells = [c.strip() for c in ln.strip("|").split("|")]
        if cells:
            rows.append(cells)
    if len(rows) < 2:
        return None
    return _rows_to_table_html(rows)


def _split_table_row(line: str) -> list[str] | None:
    text = str(line or "").rstrip()
    if not text.strip():
        return None
    if "|" in text and text.count("|") >= 2:
        return [c.strip() for c in text.strip("|").split("|")]
    if "\t" in text:
        cells = [c.strip() for c in text.split("\t")]
        return cells if len(cells) >= 2 else None
    # 两个及以上连续空格分列（工程特性表常见）
    cells = [c.strip() for c in re.split(r" {2,}|\u3000{2,}", text) if c.strip()]
    if len(cells) >= 2:
        return cells
    return None


def _lines_to_table_html(lines: list[str]) -> str | None:
    rows: list[list[str]] = []
    split_count = 0
    for ln in lines:
        cells = _split_table_row(ln)
        if cells:
            split_count += 1
            rows.append(cells)
        else:
            # 分组标题行（如「一、变电部分」）→ 单列跨行
            rows.append([ln.strip()])
    if split_count < 2:
        return None
    # 至少两行是多列，才认作表
    return _rows_to_table_html(rows)


def _rows_to_table_html(rows: list[list[str]]) -> str:
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    header = rows[0]
    use_thead = len(header) >= 2
    body_start = 1 if use_thead else 0
    parts = ['<table border="1" cellspacing="0" cellpadding="4">']
    if use_thead:
        cells = "".join(f"<th>{_escape_text(c)}</th>" for c in (header + [""] * width)[:width])
        parts.append(f"<thead><tr>{cells}</tr></thead>")
    parts.append("<tbody>")
    for row in rows[body_start:]:
        if len(row) == 1 and width > 1:
            parts.append(
                f'<tr><td colspan="{width}"><strong>{_escape_text(row[0])}</strong></td></tr>'
            )
            continue
        padded = (row + [""] * width)[:width]
        parts.append("<tr>" + "".join(f"<td>{_escape_text(c)}</td>" for c in padded) + "</tr>")
    parts.append("</tbody></table>")
    return "".join(parts)


def _escape_text(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


class _TagStack(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        name = tag.lower()
        if name in _VOID:
            return
        self.stack.append(name)

    def handle_endtag(self, tag: str) -> None:
        name = tag.lower()
        if name in _VOID:
            return
        if name in self.stack:
            while self.stack:
                top = self.stack.pop()
                if top == name:
                    break


def _close_open_tags(html: str) -> str:
    parser = _TagStack()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        return html
    if not parser.stack:
        return html
    closing = "".join(f"</{tag}>" for tag in reversed(parser.stack))
    return html + closing


def strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text or "")
