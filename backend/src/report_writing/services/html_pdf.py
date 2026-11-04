"""把章节 HTML 片段导出为 PDF（WeasyPrint）。"""

from __future__ import annotations

import html as html_module
import logging

from report_writing.domain.word_style import WordStyleSettings

_log = logging.getLogger("uvicorn.error")

_BASE_CSS = """
@page {
  size: A4;
  margin: 2cm 2.2cm;
}
html {
  color: #1a1a1a;
}
body {
  font-family: "PingFang SC", "Microsoft YaHei", "Noto Sans CJK SC", "SimSun", sans-serif;
  font-size: 12pt;
  line-height: 1.55;
  word-wrap: break-word;
}
h1, h2, h3, h4, h5, h6 {
  font-family: "PingFang SC", "Microsoft YaHei", "SimHei", sans-serif;
  page-break-after: avoid;
  line-height: 1.35;
}
h1 { font-size: 20pt; margin: 0 0 16px; }
h2 { font-size: 16pt; margin: 18px 0 10px; }
h3 { font-size: 14pt; margin: 14px 0 8px; }
h4 { font-size: 13pt; margin: 12px 0 6px; }
p { margin: 0 0 8px; text-align: justify; }
table {
  border-collapse: collapse;
  width: 100%;
  margin: 10px 0;
  font-size: 11pt;
}
th, td {
  border: 1px solid #cbd5e1;
  padding: 4px 6px;
  vertical-align: top;
}
figure.rw-figure {
  margin: 12px 0;
  text-align: center;
  page-break-inside: avoid;
}
figure.rw-figure img {
  max-width: 100%;
  max-height: 16cm;
  height: auto;
}
ul, ol { margin: 0 0 8px 1.2em; padding: 0; }
.rw-img-missing {
  color: #94a3b8;
  font-size: 11pt;
}
"""


def _css_for_style(style: WordStyleSettings | None) -> str:
    if style is None:
        return _BASE_CSS
    body_pt = float(getattr(style, "body_pt", 12) or 12)
    title_pt = float(getattr(style, "report_title_pt", 20) or 20)
    h2_pt = float((style.chapter_title_pt or {}).get(2, 16) or 16)
    h3_pt = float((style.chapter_title_pt or {}).get(3, 14) or 14)
    indent_cm = float(getattr(style, "first_line_indent_cm", 0.74) or 0)
    body_font = str(getattr(style, "body_font", "") or "宋体").strip() or "宋体"
    title_font = str(getattr(style, "title_font", "") or "黑体").strip() or "黑体"
    line_height = float(getattr(style, "line_spacing", 1.5) or 1.5)
    indent_rule = ""
    if indent_cm > 0.01:
        indent_rule = f"p {{ text-indent: {indent_cm:.2f}cm; }}"
    return (
        _BASE_CSS
        + f"""
body {{
  font-family: "{html_module.escape(body_font)}", "PingFang SC", "Microsoft YaHei", sans-serif;
  font-size: {body_pt:.1f}pt;
  line-height: {line_height:.2f};
}}
h1, h2, h3, h4 {{
  font-family: "{html_module.escape(title_font)}", "PingFang SC", "SimHei", sans-serif;
}}
h1 {{ font-size: {title_pt:.1f}pt; }}
h2 {{ font-size: {h2_pt:.1f}pt; }}
h3 {{ font-size: {h3_pt:.1f}pt; }}
{indent_rule}
"""
    )


def html_to_pdf_bytes(
    html: str,
    *,
    title: str = "",
    style: WordStyleSettings | None = None,
    base_url: str | None = None,
) -> bytes:
    """HTML 片段 → PDF 字节。base_url 便于解析 file:// 与相对路径图片。"""
    try:
        from weasyprint import CSS, HTML
    except ImportError as error:
        raise ValueError(
            "未安装 PDF 渲染依赖 weasyprint。请在 backend 环境执行：pip install weasyprint"
        ) from error

    fragment = str(html or "").strip()
    if not fragment:
        raise ValueError("HTML 为空，无法生成 PDF")
    safe_title = html_module.escape(str(title or "").strip())
    document = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"/>
<title>{safe_title}</title>
</head>
<body>{fragment}</body>
</html>"""
    try:
        pdf = HTML(string=document, base_url=base_url or ".").write_pdf(
            stylesheets=[CSS(string=_css_for_style(style))]
        )
    except Exception as error:
        _log.warning("PDF 渲染失败: %s", error)
        raise ValueError(f"PDF 生成失败：{error}") from error
    if not pdf:
        raise ValueError("PDF 生成结果为空")
    return bytes(pdf)
