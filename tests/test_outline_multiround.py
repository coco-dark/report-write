from report_writing.domain.models import OutlineNode
from report_writing.domain.outline import finalize_outline
from report_writing.domain.outline_refine import expand_outline_chapterwise


def test_expand_outline_chapterwise_adds_children():
    text = """
目 录
1 概述 1
2 工程概况 3

1 概述
1.1 编制目的
编制目的正文较长，说明本报告用途。
1.2 编制依据
依据正文。

2 工程概况
2.1 项目简介
简介正文。
2.2 建设规模
规模正文。
"""
    rough = finalize_outline(
        [
            OutlineNode(sequence="1", title="概述", level=1),
            OutlineNode(sequence="2", title="工程概况", level=1),
        ]
    )
    expanded = expand_outline_chapterwise(text, rough, use_llm=False, max_chars_per_chapter=50000)
    seqs = [item.sequence for item in expanded.chapters]
    assert "1.1" in seqs and "1.2" in seqs
    assert "2.1" in seqs and "2.2" in seqs
    assert seqs.index("1.1") < seqs.index("1.2") < seqs.index("2")
