from report_writing.controller.outline_flow import (
    _prefer_toc_rules_tree,
    _toc_driven_rules_trustworthy,
)
from report_writing.domain.models import OutlineNode, OutlineTree
from report_writing.domain.outline import choose_best_outline, finalize_outline, outline_tree_quality


def _tree(*pairs: tuple[str, str]) -> OutlineTree:
    nodes = [OutlineNode(sequence=s, title=t, level=s.count(".") + 1) for s, t in pairs]
    return finalize_outline(nodes)


def test_trustworthy_for_hyperlink_toc():
    tree = _tree(("1", "概述"), ("1.1", "目的"), ("2", "工程概况"), ("2.1", "简介"), ("3", "评价"), ("3.1", "结论"))
    meta = {"strategy": "hyperlink_toc"}
    assert _toc_driven_rules_trustworthy(tree, meta) is True


def test_prefer_rules_when_toc_heading_beats_flat_docx():
    rules = _tree(
        ("1", "概述"),
        ("1.1", "目的"),
        ("2", "工程概况"),
        ("2.1", "简介"),
        ("3", "评价"),
        ("3.1", "结论"),
        ("4", "附件"),
        ("4.1", "附图"),
    )
    docx = _tree(("1", "概述"), ("2", "工程概况"), ("3", "评价"))
    meta = {"strategy": "toc_heading"}
    picked = _prefer_toc_rules_tree(rules, docx, meta)
    assert picked is rules
    assert len(picked.chapters) == 8
    assert outline_tree_quality(picked) >= outline_tree_quality(docx)


def test_choose_best_accepts_three_numbered_chapters():
    tree = _tree(("1", "基本情况"), ("2", "鉴定过程"), ("3", "鉴定意见"))
    picked = choose_best_outline(("rules", tree), ("docx", None), ("html", tree))
    assert picked is not None
    assert picked[0] == "rules"
    assert len(picked[1].chapters) == 3


def test_choose_best_falls_back_when_only_unnumbered_list():
    nodes = [
        OutlineNode(sequence="", title=f"段{i}", level=1) for i in range(1, 6)
    ]
    tree = OutlineTree(chapters=nodes)
    picked = choose_best_outline(("rules", tree), ("docx", None))
    assert picked is not None
    assert len(picked[1].chapters) == 5


def test_choose_best_prefers_complete_nested_over_short_tree():
    small_pairs = [(str(i), f"章{i}") for i in range(1, 21)] + [(f"{i}.1", f"节{i}") for i in range(1, 21)]
    small = _tree(*small_pairs)
    large_pairs = []
    for i in range(1, 31):
        large_pairs.append((str(i), f"章{i}"))
        for j in range(1, 8):
            large_pairs.append((f"{i}.{j}", f"节{i}-{j}"))
    large = _tree(*large_pairs)
    picked = choose_best_outline(("docx", small), ("html", small), ("rules", large))
    assert picked is not None
    assert picked[0] == "rules"
    assert len(picked[1].chapters) == len(large.chapters)


def test_prefer_complete_tree_over_short_rules():
    rules = _tree(*[(str(i), f"章{i}") for i in range(1, 21)] + [(f"{i}.1", f"节{i}") for i in range(1, 21)])
    other_pairs = []
    for i in range(1, 31):
        other_pairs.append((str(i), f"章{i}"))
        for j in range(1, 8):
            other_pairs.append((f"{i}.{j}", f"节{i}-{j}"))
    other = _tree(*other_pairs)
    picked = _prefer_toc_rules_tree(rules, other, {"strategy": "toc_heading"})
    assert picked is other
