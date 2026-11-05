"""wants_* 只是 classify_freeform_actions 的测试别名，生产收集器不得再导出。"""

from report_writing.chat.freeform_intent import (
    classify_freeform_actions,
    wants_generate_outline,
    wants_generate_points,
)
from report_writing.chat.freeform_outline import looks_like_outline_directory
from report_writing.chat.freeform_points import count_chapters_missing_points


def test_wants_generate_outline_matches_classifier():
    assert wants_generate_outline("生成目录", invoke=None) is True
    assert "generate_outline" in classify_freeform_actions("生成目录", invoke=None)
    assert wants_generate_points("生成目录", invoke=None) is False


def test_collectors_do_not_export_wants_aliases():
    import report_writing.chat.freeform_outline as outline
    import report_writing.chat.freeform_points as points

    assert not hasattr(outline, "wants_generate_outline")
    assert not hasattr(points, "wants_generate_points")
    assert callable(looks_like_outline_directory)
    assert callable(count_chapters_missing_points)
