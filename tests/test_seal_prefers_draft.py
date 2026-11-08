"""重写落盘：本轮草稿覆盖旧 official_text。"""

from report_writing.domain.models import ChapterDraft, ChapterState, ChapterStatus, Paragraph
from report_writing.services.pipeline import seal_written_chapter


def test_seal_overwrites_old_official_with_new_draft():
    chapter = ChapterState(
        key="c1",
        title="建设规模",
        status=ChapterStatus.CHECKING,
        official_text="<p>国网咸宁供电公司</p>",
        draft=ChapterDraft(
            title="建设规模",
            paragraphs=[Paragraph(index=0, text="<p>本工程建设单位</p>")],
        ),
    )
    assert seal_written_chapter(chapter)
    assert "本工程建设单位" in chapter.official_text
    assert "国网咸宁供电公司" not in chapter.official_text
    assert chapter.status == ChapterStatus.CONFIRMED
