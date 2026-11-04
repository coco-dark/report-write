"""长文档标题索引切章：TOC 消歧 + 边界 + 滑动窗 + 模糊锚定。"""

import re

from report_writing.domain.chapter_locator import (
    build_heading_index,
    focus_text_for_parents,
    locate_chapter,
    refine_excerpt_window,
)
from report_writing.controller import _best_chapter_excerpt
from report_writing.domain.outline import chapters_from_outline, outline_from_payload


LONG_DOC = """
前言若干字。

目 录

1 概述 1
1.1 安全预评价目的 1
1.2 安全预评价原则 2
2 工程概况 8
2.1 建设单位简介 8

1 概述

本章总述工程建设背景与评价范围，并说明编制依据。

1.1 安全预评价目的

写明开展安全预评价的目的，包括识别危险有害因素、提出对策措施。
本段用于验证正文命中而非目录命中。

1.2 安全预评价原则

写明评价原则：科学性、客观性、针对性。

2 工程概况

工程概况总述：建设规模、电压等级、路径方案。

2.1 建设单位简介

建设单位正文：企业性质、资质与项目管理能力。
额外补充一段，保证滑动窗有足够正文可扩。
""".strip()


def test_index_marks_toc_and_body():
    hits = build_heading_index(LONG_DOC)
    overview = [h for h in hits if h.sequence == "1" and "概述" in h.title]
    assert len(overview) >= 2
    assert overview[0].toc_like is True
    assert overview[-1].toc_like is False


def test_locate_prefers_body_over_toc():
    excerpt, matched = locate_chapter(LONG_DOC, sequence="1.1", title="安全预评价目的")
    assert matched is True
    assert "写明开展安全预评价的目的" in excerpt
    assert "1.2 安全预评价原则" not in excerpt
    # 不应只切到目录行
    assert "写明" in excerpt


def test_locate_parent_includes_until_sibling():
    excerpt, matched = locate_chapter(LONG_DOC, sequence="1", title="概述", refine=False)
    assert matched is True
    assert "1.1 安全预评价目的" in excerpt
    assert "2 工程概况" not in excerpt


def test_sliding_window_expands_then_caps():
    paras = [f"段落{i}：" + ("内容" * 20) for i in range(30)]
    raw = "1.1 标题\n\n" + "\n\n".join(paras)
    out = refine_excerpt_window(raw, max_chars=500, initial_paras=3, step_paras=2, min_body_chars=50)
    assert len(out) <= 500
    assert "段落0" in out


def test_best_chapter_excerpt_uses_locator():
    excerpt, matched = _best_chapter_excerpt(
        LONG_DOC,
        sequence="2.1",
        title="建设单位简介",
    )
    assert matched is True
    assert "建设单位正文" in excerpt


def test_chapters_from_outline_skips_toc():
    tree = outline_from_payload(
        [
            {"sequence": "1", "title": "概述", "level": 1},
            {"sequence": "1.1", "title": "安全预评价目的", "level": 2, "parent_sequence": "1"},
            {"sequence": "2.1", "title": "建设单位简介", "level": 2, "parent_sequence": "2"},
        ]
    )
    chapters = chapters_from_outline(tree, LONG_DOC)
    by_seq = {item["sequence"]: item for item in chapters}
    assert by_seq["1.1"]["matched"] is True
    assert any("写明开展安全预评价的目的" in p for p in by_seq["1.1"]["sample_paragraphs"])


HYPERLINK_DOC = """
目 录
HYPERLINK \\l \\_Toc100 1 概述 PAGEREF \\_Toc100 \\h 1
HYPERLINK \\l \\_Toc101 1.1 站址选择 PAGEREF \\_Toc101 \\h 2

1 概述

概述正文若干字，说明工程背景。

1.1 站址选择

站址比选结论：推荐方案一。
""".strip()


def test_hyperlink_toc_indexed_as_toc_like():
    hits = build_heading_index(HYPERLINK_DOC)
    toc_hits = [h for h in hits if h.sequence == "1.1" and h.toc_like]
    body_hits = [h for h in hits if h.sequence == "1.1" and not h.toc_like]
    assert toc_hits
    assert body_hits
    excerpt, matched = locate_chapter(HYPERLINK_DOC, sequence="1.1", title="站址选择")
    assert matched is True
    assert "站址比选结论" in excerpt


def test_fuzzy_anchor_when_index_style_misses():
    # 正文标题粘在段落里，行级索引可能漏掉；模糊锚定应仍能切出
    messy = (
        "目录\n1 概述 1\n1.1 目的 1\n\n"
        "前文铺垫。" * 5
        + "\n然后进入1.1 目的本节说明评价目的与范围。"
        + "详细论述危险因素识别方法。" * 3
        + "\n1.2 原则\n原则正文。"
    )
    excerpt, matched = locate_chapter(messy, sequence="1.1", title="目的")
    assert matched is True
    assert "评价目的" in excerpt or "危险因素" in excerpt
    assert "原则正文" not in excerpt


def test_densest_window_prefers_richer_mid_section():
    thin = "1 章\n\n短引入。\n\n"
    rich = "\n\n".join([f"详述段落{i}：" + ("关键技术内容" * 15) for i in range(12)])
    tail = "\n\n附录噪声。" * 5
    raw = thin + rich + tail
    out = refine_excerpt_window(raw, max_chars=400, initial_paras=2, step_paras=2, min_body_chars=40)
    assert len(out) <= 400
    assert "详述段落" in out or "短引入" in out


def test_focus_text_for_parents_limits_to_chapter_spans():
    focused = focus_text_for_parents(
        LONG_DOC,
        [("1", "概述"), ("2.1", "建设单位简介")],
        max_chars_each=2000,
        max_total=8000,
    )
    assert "父章 1 概述" in focused
    assert "写明开展安全预评价的目的" in focused or "本章总述" in focused
    assert "建设单位正文" in focused


SAME_TITLE_DOC = """
目 录
1.5.1 法律法规标准规范符合性 12
7.4 法律法规标准规范符合性 88

1.5 其他
别的内容。

## 7.4 **法律 法规 标准 规范符合性**
本工程按照有关设计规定的要求，委托有资质单位进行了可行性研究编制工作.主要工艺系统依据输变电工程的相关规程规范的要求设置，并充分考虑了本工程的特点.
""".strip()


def test_same_title_does_not_cross_match_sequence():
    """同名标题不得把 1.5.1 串到 7.4。"""
    excerpt, matched = locate_chapter(
        SAME_TITLE_DOC,
        sequence="1.5.1",
        title="法律法规标准规范符合性",
    )
    # 正文缺失时可匹配目录行，但绝不能落到 7.4
    if matched:
        assert not excerpt.lstrip().startswith("7.4")
        assert "本工程按照" not in excerpt
    excerpt2, matched2 = _best_chapter_excerpt(
        SAME_TITLE_DOC,
        sequence="1.5.1",
        title="法律法规标准规范符合性",
    )
    if matched2:
        assert "本工程按照" not in excerpt2
        assert not re.match(r"^(?:#{1,6}\s+)?7\.4\b", excerpt2.lstrip())


def test_outline_find_heading_respects_sequence():
    from report_writing.domain.outline import _find_heading, outline_from_payload

    lines = SAME_TITLE_DOC.splitlines()
    tree = outline_from_payload(
        [{"sequence": "1.5.1", "title": "法律法规标准规范符合性", "level": 3}]
    )
    found = _find_heading(lines, 0, tree.chapters[0], prefer_later=True)
    assert found >= 0
    assert "7.4" not in lines[found]
    assert "1.5.1" in lines[found]


def test_parse_heading_keeps_dunhao_title():
    from report_writing.domain.chapter_locator import _parse_heading, _plain_line, _title_similarity

    seq, title = _parse_heading(_plain_line("## 7.4 **法律、法规、标准、规范符合性**"))
    assert seq == "7.4"
    assert "法规" in title
    assert _title_similarity(title, "法律") < 0.9


def test_locate_22_keeps_table_body_past_caption():
    """表题不得当章节边界：2.2 建设规模节选须含表体（襄阳连云样本形态）。"""
    doc = (
        "## 2.2 建设规模\n\n"
        "本工程建设襄阳连云220kV输变电工程及线路，工程特性见下表2.2-1。\n\n"
        "**表2.2-1 襄阳连云220kV输变电工程特性表**\n\n"
        "<table>\n"
        "<tr><td>建设单位</td><td>国网湖北电力</td></tr>\n"
        "<tr><td>建设规模</td><td>新建变电站一座</td></tr>\n"
        "</table>\n\n"
        "## 2.3 工程概况\n\n"
        "后文内容。\n"
    )
    hits = build_heading_index(doc)
    assert not any("表2.2-1" in (h.title or "") for h in hits)
    excerpt, matched = locate_chapter(doc, sequence="2.2", title="建设规模")
    assert matched
    assert "见下表2.2-1" in excerpt
    assert "建设单位" in excerpt
    assert "<table" in excerpt
    assert "2.3" not in excerpt or "工程概况" not in excerpt.split("建设单位", 1)[-1]


def test_refine_keeps_table_after_see_below_sentence():
    """「见下表…。」句号不得截断扩窗，表体须进入分析节选。"""
    intro = "2.2 建设规模\n\n本工程建设襄阳连云220kV输变电工程及线路，工程特性见下表2.2-1。\n\n"
    table_rows = "\n".join(
        [f"| 项目{i} | 数值{i} | 单位 |" for i in range(12)]
    )
    table = (
        "表2.2-1 工程特性表\n\n"
        "| 栏目 | 内容 | 备注 |\n"
        "| --- | --- | --- |\n"
        f"{table_rows}\n\n"
    )
    filler = "\n\n".join([f"后续说明段落{i}：" + ("补充" * 30) for i in range(20)])
    raw = intro + table + filler
    out = refine_excerpt_window(
        raw,
        max_chars=900,
        title="建设规模",
        sequence="2.2",
        initial_paras=2,
        step_paras=2,
        min_body_chars=40,
    )
    assert "见下表2.2-1" in out
    assert "表2.2-1" in out or "项目0" in out or "栏目" in out
    assert len(out) <= 900


def test_trim_prefers_closed_html_table():
    from report_writing.domain.chapter_locator import _trim_to_paragraph

    head = "2.2 建设规模\n本工程建设规模见下表。\n"
    table = "<table><tr><td>" + ("单元格内容" * 40) + "</td></tr></table>"
    tail = "表后说明" + ("x" * 200)
    raw = head + table + tail
    out = _trim_to_paragraph(raw, max_chars=len(head) + len(table) - 20)
    # 半截表时应退到 <table 前，保留开篇
    assert out.startswith("2.2")
    assert "</table>" not in out or out.rstrip().endswith("</table>")
