from report_writing.services.corpus import CorpusHit
from report_writing.domain.rating import rate_hit, score_relevance, score_source


def test_formal_excerpt_scores_above_adopt_line():
    hit = CorpusHit("hit-1", "项目说明书.md", "第4段", "总投资为12680万元。")
    scores = rate_hit("总投资", hit, [])
    total = scores["relevance"] + scores["specificity"] + scores["source_score"] + scores["consistency"]
    assert total >= 5
    assert score_source("项目说明书.md") == 2


def test_attachment_loses_source_score_and_conflicts_with_adopted_number():
    hit = CorpusHit("hit-2", "附件-估算.txt", "第1段", "总投资为99999万元。")
    scores = rate_hit("总投资", hit, ["总投资为12680万元。"])
    assert score_relevance("总投资", hit.excerpt) == 2
    assert scores["source_score"] == 0
    assert scores["consistency"] == 0
    assert scores["conflict"]


def test_unrelated_excerpt_is_not_relevant():
    assert score_relevance("环境空气", "第一章 概述") == 0
