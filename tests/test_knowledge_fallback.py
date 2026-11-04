"""检索无采纳证据时仍应尝试按要点撰写（fallback），而非留空章。"""

from report_writing.agents.check import inspect_draft
from report_writing.agents.supervisor import execute_chapter_search
from report_writing.domain.models import ChapterDraft, ChapterRequirement, ChapterState, Paragraph


def test_inspect_draft_fallback_allows_no_evidence_ids():
    requirement = ChapterRequirement(key="c1", title="概况", points=["说明评价依据"])
    draft = ChapterDraft(
        title="概况",
        paragraphs=[Paragraph(index=0, text="<p>本章依据国家及行业相关规范开展评价工作。</p>", evidence_ids=[])],
        uncovered_points=["具体项目名称待补充"],
    )
    strict = inspect_draft(requirement, [], draft, knowledge_fallback=False)
    assert any(item["kind"] == "citation" for item in strict)
    relaxed = inspect_draft(requirement, [], draft, knowledge_fallback=True)
    assert not any(item["kind"] == "citation" for item in relaxed)


def test_execute_chapter_search_no_hits_enters_write_not_skip():
    chapter = ChapterState(key="c1", title="测试章")
    requirement = ChapterRequirement(key="c1", title="测试章", points=["写一段说明"], keywords=["环评"])
    events = []

    def emit(step, status, ch="", title="", payload=None):
        events.append((step, status, payload or {}))

    result = execute_chapter_search(
        chapter,
        requirement,
        None,
        None,
        None,
        emit,
        search_only=False,
    )
    assert result.get("skipped") is False
    assert result.get("adopted") == 0
    assert result.get("fallback_write") is True
    assert chapter.status.value == "writing"

