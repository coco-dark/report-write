from report_writing.domain.doc_genre import (
    genre_writing_hint,
    normalize_doc_category,
    writer_persona,
)


def test_empty_category_is_document_not_report():
    assert normalize_doc_category("") == "文档"
    assert normalize_doc_category("报告") == "文档"
    assert "报告章节写作员" not in writer_persona("")
    assert "文档" in writer_persona("")


def test_letter_genre_not_research_report():
    hint = genre_writing_hint("工作联系函")
    assert "函" in hint
    assert "评价专篇" in hint or "研究报告" in hint


def test_plan_genre():
    hint = genre_writing_hint("实施方案")
    assert "方案" in hint
