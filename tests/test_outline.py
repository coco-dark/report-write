from report_writing.domain.outline import (
    chapters_from_outline,
    load_outline_json,
    outline_from_text,
    slice_toc_region,
)


SAMPLE = """
**前 言**

正文。

**目 录 TOC \\O "1-2" \\H \\Z \\U**

**HYPERLINK \\L \\_TOC7634 1 概述	 PAGEREF \\_TOC7634 \\H 1**

HYPERLINK \\l \\_Toc20716 1.1 安全预评价目的	 PAGEREF \\_Toc20716 \\h 1

HYPERLINK \\l \\_Toc8156 1.2 安全预评价原则	 PAGEREF \\_Toc8156 \\h 1

**HYPERLINK \\L \\_TOC9383 2 工程概况	 PAGEREF \\_TOC9383 \\H 8**

HYPERLINK \\l \\_Toc32033 2.1 建设单位简介	 PAGEREF \\_Toc32033 \\h 8

路径方案图**概述**

1．充分贯彻落实。

**安全预评价目的**

写明评价目的。

**安全预评价原则**

写明原则。

**工程概况**

工程概况正文。

**建设单位简介**

建设单位正文。
"""


def test_skips_bare_page_number_sequence_in_toc():
    text = """
目 录
1 概述 1
1.1 安全预评价目的 2
16
2 工程概况 8
2.1 建设单位简介 9
"""
    tree = outline_from_text(text)
    seqs = [n.sequence for n in tree.chapters]
    assert "1.1" in seqs and "2.1" in seqs
    assert "16" not in seqs


def test_outline_from_html_numbered_toc():
    from report_writing.domain.outline import outline_from_html

    html = """
<html><body>
<h1>目录</h1>
<p>1 概述 1</p>
<p>1.1 安全预评价目的 2</p>
<p>2 工程概况 8</p>
<p>2.1 建设单位简介 9</p>
</body></html>
"""
    tree = outline_from_html(html)
    assert tree is not None
    seqs = [n.sequence for n in tree.chapters]
    assert "1.1" in seqs and "2.1" in seqs


def test_outline_from_toc_domain_keeps_hierarchy():
    tree = outline_from_text(SAMPLE)
    assert [item.sequence for item in tree.chapters] == ["1", "1.1", "1.2", "2", "2.1"]
    assert tree.chapters[0].title == "概述"
    assert tree.chapters[1].level == 2
    assert tree.chapters[1].parent == tree.chapters[0].key


def test_chapters_match_bold_titles_after_outline():
    tree = outline_from_text(SAMPLE)
    chapters = chapters_from_outline(tree, SAMPLE)
    by_title = {item["title"]: item for item in chapters}
    assert by_title["安全预评价目的"]["matched"] is True
    assert "写明评价目的。" in by_title["安全预评价目的"]["sample_paragraphs"]
    assert by_title["建设单位简介"]["matched"] is True


def test_import_outline_json():
    tree = load_outline_json(
        {
            "chapters": [
                {"sequence": "1", "title": "概述", "level": 1},
                {"sequence": "1.1", "title": "目的", "level": 2, "parent_sequence": "1"},
            ]
        }
    )
    assert len(tree.chapters) == 2
    assert tree.chapters[1].parent == tree.chapters[0].key


def test_slice_toc_ignores_long_body_noise():
    toc = "目 录\n\n1 概述 1\n1.1 目的 2\n2 工程概况 8\n2.1 简介 9\n\n"
    # 正文里再塞大量「假章节」编号，若扫全文会污染
    body_noise = "\n".join(
        [
            f"{i}.{j} 正文粘连标题" + ("详细论述内容与工程技术参数说明" * 40)
            for i in range(3, 40)
            for j in range(1, 8)
        ]
    )
    full = toc + "1 概述\n本章总述。\n\n" + body_noise
    assert len(full) > 100000
    chunk, meta = slice_toc_region(full)
    assert meta["strategy"] in {"toc_heading", "dense_prefix", "full_head"}
    assert len(chunk) < len(full) * 0.15
    tree = outline_from_text(full)
    seqs = [item.sequence for item in tree.chapters]
    assert "1" in seqs and "1.1" in seqs and "2.1" in seqs
    # 不应把正文噪声章节大批抽进来
    assert len(tree.chapters) <= 12


def test_hyperlink_toc_still_preferred_on_long_doc():
    head = SAMPLE
    tail = "\n" + ("本章继续写很多字。" * 5000)
    tree = outline_from_text(head + tail)
    assert [item.sequence for item in tree.chapters] == ["1", "1.1", "1.2", "2", "2.1"]


def test_large_numbered_toc_not_truncated_at_28k():
    lines = ["目 录", ""]
    for i in range(1, 121):
        lines.append(f"{i}.1 子节标题{i}  {i}")
        lines.append(f"{i}.2 另一子节{i}  {i + 1}")
    toc = "\n".join(lines) + "\n\n"
    body = "1 概述\n" + ("正文段落较长。" * 200) + "\n"
    full = toc + body
    chunk, meta = slice_toc_region(full)
    assert meta["strategy"] == "toc_heading"
    tree = outline_from_text(full)
    assert len(tree.chapters) >= 200


def test_numbered_toc_beats_few_atx_headings():
    md = """
目 录

1.1 第一节 1
1.2 第二节 2
2.1 第三节 3

# 1 **概述**

前言。

# 2 **说明**
"""
    for i in range(3, 53):
        md += f"\n{i}.1 章{i}小节  {i}\n"
    tree = outline_from_text(md)
    assert len(tree.chapters) >= 50


def test_tab_leader_toc_lines():
    toc = "目 录\n\n1  概述\t1\n1.1  设计依据\t2\n2  工程概况\t8\n\n"
    tree = outline_from_text(toc + "正文很长。" * 50)
    assert [c.sequence for c in tree.chapters] == ["1", "1.1", "2"]


def test_body_like_titles_pruned_from_numbered_toc():
    toc = (
        "目 录\n\n"
        "1 概述 1\n"
        "1.1 设计依据 2\n"
        "2 工程概况，应当符合规范要求，详见附件。 8\n"
        "3 站址选择 9\n"
    )
    tree = outline_from_text(toc)
    titles = [c.title for c in tree.chapters]
    assert "工程概况" in " ".join(titles) or len(tree.chapters) >= 2
    assert not any("应当符合" in t for t in titles)


def test_markdown_atx_headings_from_rag_style():
    # RAG 常见形态；目录页损坏也不影响
    md = """
**目 录**

**1**

8

# 1 **概述**

前言正文。

## 1.1 **安全预评价目的**

写目的。

## 1.2 **安全预评价原则**

写原则。

# 2 **工程概况**

## 2.1 **建设单位简介**

简介正文。
"""
    tree = outline_from_text(md)
    assert [item.sequence for item in tree.chapters] == ["1", "1.1", "1.2", "2", "2.1"]
    assert tree.chapters[0].title == "概述"
    assert tree.chapters[1].level == 2


def test_toc_region_survives_one_long_unnumbered_line():
    lines = ["目 录", ""]
    for i in range(1, 31):
        lines.append(f"{i} 第{i}章标题  {i}")
        lines.append(f"{i}.1 子节标题{i}  {i}")
        if i == 8:
            lines.append("本页目录续下页，请参见后续章节编排说明与页码对照")
    toc = "\n".join(lines) + "\n\n1 概述\n" + ("正文段落较长。" * 40)
    tree = outline_from_text(toc)
    assert len(tree.chapters) >= 40


def test_try_outline_from_cached_html(tmp_path):
    from report_writing.domain.outline import try_outline_from_rag_html

    src = tmp_path / "sample.docx"
    src.write_bytes(b"dummy")
    html = """
    <html><body>
    <h1>目录</h1>
    <p>1 概述 1</p>
    <p>1.1 安全预评价目的 2</p>
    <p>2 工程概况 8</p>
    <p>2.1 建设单位简介 9</p>
    </body></html>
    """
    (tmp_path / "sample.docx.rag-parse.html").write_text(html, encoding="utf-8")
    tree = try_outline_from_rag_html(src)
    assert tree is not None
    seqs = [n.sequence for n in tree.chapters]
    assert "1.1" in seqs and "2.1" in seqs


def test_finalize_outline_preserve_parents_keeps_topology():
    from report_writing.domain.models import OutlineNode
    from report_writing.domain.outline import finalize_outline

    nodes = [
        OutlineNode(key="ch_overview", sequence="1", title="概述", level=1, parent=""),
        OutlineNode(
            key="ch_purpose",
            sequence="1.1",
            title="目的",
            level=2,
            parent="ch_overview",
            parent_sequence="1",
        ),
        OutlineNode(
            key="ch_scope",
            sequence="1.2",
            title="范围",
            level=2,
            parent="ch_overview",
            parent_sequence="1",
        ),
        OutlineNode(key="ch_eng", sequence="2", title="工程概况", level=1, parent=""),
    ]
    # 故意打乱 level 暗示：若按栈重算会把 1.2 挂到 1.1 下
    nodes[2].level = 3
    tree = finalize_outline(nodes, preserve_parents=True)
    by_key = {n.key: n for n in tree.chapters}
    assert list(by_key) == ["ch_overview", "ch_purpose", "ch_scope", "ch_eng"]
    assert by_key["ch_purpose"].parent == "ch_overview"
    assert by_key["ch_scope"].parent == "ch_overview"
    assert by_key["ch_eng"].parent == ""


def test_finalize_preserve_strips_wrong_parent_on_top_level_sequences():
    from report_writing.domain.models import OutlineNode
    from report_writing.domain.outline import finalize_outline

    nodes = [
        OutlineNode(key="ch1", sequence="1", title="编制依据", level=1, parent=""),
        OutlineNode(key="ch2", sequence="2", title="工程内容", level=1, parent="ch1"),
        OutlineNode(key="ch3", sequence="3", title="评价等级", level=1, parent="ch1"),
    ]
    tree = finalize_outline(nodes, preserve_parents=True)
    by_key = {n.key: n for n in tree.chapters}
    assert by_key["ch2"].parent == ""
    assert by_key["ch3"].parent == ""
