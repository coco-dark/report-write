from report_writing.agents.write import _ensure_fallback_draft_body
from report_writing.domain.models import ChapterDraft, ChapterRequirement


def test_ensure_fallback_draft_when_model_returns_empty():
    req = ChapterRequirement(
        key="c2",
        title="工程内容及规模",
        points=["项目建设规模", "主要设备"],
        describes="说明工程建设内容与规模。",
    )
    empty = ChapterDraft(title="工程内容及规模", paragraphs=[], uncovered_points=["项目建设规模"])
    out = _ensure_fallback_draft_body(req, empty)
    assert len(out.paragraphs) >= 2
    plain = " ".join(p.text for p in out.paragraphs)
    assert "项目建设规模" in plain or "待补充" in plain
