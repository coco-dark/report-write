"""渲染：占位符填充与 HTML→Word。"""

from __future__ import annotations

from pathlib import Path

from report_writing.services.html_docx import html_to_docx_bytes
from report_writing.domain.models import (
    ChapterDraft,
    ChapterState,
    ChapterStatus,
    OutlineNode,
    OutlineTree,
    Paragraph,
    WritingTask,
)
import pytest

from report_writing.services.render import (
    RenderChapter,
    collect_chapters,
    fill_template,
    list_templates,
    plain_document_html,
    render_task,
    render_task_docx,
    render_task_pdf,
    save_template,
)


def test_fill_template_placeholders():
    chapters = [
        RenderChapter(key="ch-1", sequence="1", title="概述", html="<p>甲</p>"),
        RenderChapter(key="ch-2", sequence="1.1", title="目的", html="<p>乙</p>"),
    ]
    html, filled, missing = fill_template(
        "<h1>{{title:ch-1}}</h1>{{ch-1}}<h2>{{seq:ch-2}} {{title:ch-2}}</h2>{{body:ch-2}}{{missing}}",
        chapters,
    )
    assert "<h1>概述</h1>" in html
    assert "<p>甲</p>" in html
    assert "1.1 目的" in html
    assert "<p>乙</p>" in html
    assert "ch-1" in filled or "title:ch-1" in filled
    assert "missing" in missing


def test_html_to_docx_bytes_roundtrip():
    data = html_to_docx_bytes("<h1>标题</h1><p>正文<strong>加粗</strong></p><ul><li>一项</li></ul>")
    assert data[:2] == b"PK"
    assert len(data) > 1000


def test_render_template_and_plain(tmp_path: Path):
    save_template(
        tmp_path,
        {
            "id": "rt_demo",
            "name": "演示",
            "html": "<h1>{{title:c1}}</h1>{{c1}}",
        },
    )
    assert any(item.id == "rt_demo" for item in list_templates(tmp_path))

    task = WritingTask(
        id="w1",
        title="演示报告",
        outline=OutlineTree(chapters=[OutlineNode(key="c1", sequence="1", title="概况")]),
        chapters=[
            ChapterState(
                key="c1",
                title="概况",
                status=ChapterStatus.CONFIRMED,
                official_text="<p>确认正文</p>",
            )
        ],
    )
    rows = collect_chapters(task)
    assert rows[0].html == "<p>确认正文</p>"

    result = render_task(tmp_path, task, template_id="rt_demo", mode="template")
    assert result.mode == "template"
    assert "概况" in result.html
    assert "确认正文" in result.html

    plain = plain_document_html(
        rows,
        report_title="演示报告",
        outline=task.outline,
    )
    assert "演示报告" in plain
    assert "目录" in plain
    assert "概况" in plain

    data, rendered = render_task_docx(tmp_path, task, template_id="rt_demo", mode="template")
    assert rendered.mode == "template"
    assert data[:2] == b"PK"


def test_llm_mode_uses_invoke(tmp_path: Path):
    task = WritingTask(
        id="w2",
        title="LLM 报告",
        outline=OutlineTree(chapters=[OutlineNode(key="c1", title="章一")]),
        chapters=[
            ChapterState(key="c1", title="章一", status=ChapterStatus.CONFIRMED, official_text="<p>内容</p>")
        ],
    )

    def invoke(system: str, user: str) -> str:
        assert "排版" in system or "HTML" in system
        return "<html><body><h1>LLM 报告</h1><h2>章一</h2><p>内容</p></body></html>"

    result = render_task(tmp_path, task, mode="llm", invoke=invoke)
    assert result.mode == "llm"
    assert "LLM 报告" in result.html


def test_collect_chapters_includes_draft_when_not_confirmed_only():
    task = WritingTask(
        id="w3",
        outline=OutlineTree(chapters=[OutlineNode(key="c1", title="章一")]),
        chapters=[
            ChapterState(
                key="c1",
                title="章一",
                status=ChapterStatus.AWAITING_CONFIRM,
                draft=ChapterDraft(
                    title="章一",
                    paragraphs=[Paragraph(index=0, text="<p>草稿</p>")],
                ),
            )
        ],
    )
    assert not collect_chapters(task, confirmed_only=True)[0].html
    assert "草稿" in collect_chapters(task, confirmed_only=False)[0].html


def test_render_docx_rejects_empty_body(tmp_path: Path):
    task = WritingTask(
        id="w4",
        title="",
        outline=None,
        chapters=[ChapterState(key="c1", title="空", status=ChapterStatus.PENDING)],
    )
    with pytest.raises(ValueError, match="没有可导出"):
        render_task_docx(tmp_path, task, mode="plain", confirmed_only=False)


def test_render_task_pdf(monkeypatch, tmp_path: Path):
    task = WritingTask(
        id="w5",
        title="PDF 报告",
        outline=OutlineTree(chapters=[OutlineNode(key="c1", sequence="1", title="概况")]),
        chapters=[
            ChapterState(
                key="c1",
                title="概况",
                status=ChapterStatus.CONFIRMED,
                official_text="<p>正文</p>",
            )
        ],
    )

    def fake_pdf(html: str, **kwargs):
        assert "正文" in html
        return b"%PDF-1.4 fake"

    monkeypatch.setattr(
        "report_writing.services.html_pdf.html_to_pdf_bytes",
        fake_pdf,
    )
    data, rendered = render_task_pdf(tmp_path, task, mode="plain", confirmed_only=True)
    assert rendered.mode == "plain"
    assert data.startswith(b"%PDF")
