from report_writing.services.corpus import CorpusHit, _filter_source_files
from report_writing.domain.elements import chapter_subtree_keys, resolve_elements_for_chapter
from report_writing.domain.models import ChapterRequirement, ElementNode, OutlineNode, OutlineTree


def test_chapter_subtree_keys_includes_descendants():
    tree = OutlineTree(
        chapters=[
            OutlineNode(key="a", title="一", level=1),
            OutlineNode(key="b", title="一.一", level=2, parent="a"),
            OutlineNode(key="c", title="一.一.一", level=3, parent="b"),
            OutlineNode(key="d", title="二", level=1),
        ]
    )
    assert chapter_subtree_keys(tree, "a") == ["a", "b", "c"]
    assert chapter_subtree_keys(tree, "b") == ["b", "c"]
    assert chapter_subtree_keys(tree, "a", include_children=False) == ["a"]


def test_resolve_kv_element():
    req = ChapterRequirement(key="ch1", title="测试", element_ids=["e1"])
    elements = [ElementNode(id="e1", name="名称", kind="kv", value="小李")]
    resolve_elements_for_chapter(req, elements, None)
    assert len(req.element_results) == 1
    assert req.element_results[0]["value"] == "小李"
    assert req.element_results[0]["name"] == "名称"


def test_chapter_search_terms_no_keywords():
    req = ChapterRequirement(key="ch1", title="测试", keywords=["旧检索词"], points=["要点"])
    assert req.search_terms() == []


def test_filter_source_files_by_name():
    hits = [
        CorpusHit(id="1", source_file="002/a.pdf", location="1", excerpt="x"),
        CorpusHit(id="2", source_file="002/b.docx", location="1", excerpt="y"),
    ]
    filtered = _filter_source_files(hits, ["a.pdf"])
    assert [item.source_file for item in filtered] == ["002/a.pdf"]
