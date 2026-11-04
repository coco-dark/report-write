"""证据四项评级。每项 0–2 分，采纳线由调用方决定。"""

from __future__ import annotations

import re

from report_writing.services.corpus import CorpusHit

_NUMBER = re.compile(r"\d+(?:\.\d+)?")
_UNIT = re.compile(r"万元|千伏|兆伏安|分贝|米|公里|kV|MVA|日|年|月|号")
_INFORMAL = re.compile(r"目录|附件|封面")
_FACT_LABELS = ("投资", "容量", "昼间", "夜间", "电压")


def score_relevance(point: str, excerpt: str) -> int:
    tokens = [token for token in re.split(r"[\s，。；、：:]+", point) if len(token) >= 2]
    if not tokens:
        return 0
    hits = sum(1 for token in tokens if token in excerpt)
    if hits >= 2 or (hits == 1 and len(tokens) == 1):
        return 2
    if hits == 1:
        return 1
    return 0


def score_specificity(excerpt: str) -> int:
    score = 0
    if _NUMBER.search(excerpt):
        score += 1
    if _UNIT.search(excerpt):
        score += 1
    return score


def score_source(source_file: str) -> int:
    if _INFORMAL.search(source_file):
        return 0
    return 2


def labeled_numbers(text: str) -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for label in _FACT_LABELS:
        if label not in text:
            continue
        window = text[text.find(label) : text.find(label) + 24]
        numbers = set(_NUMBER.findall(window))
        if numbers:
            found[label] = numbers
    return found


def score_consistency(excerpt: str, adopted_excerpts: list[str]) -> tuple[int, str | None]:
    current = labeled_numbers(excerpt)
    for previous in adopted_excerpts:
        other = labeled_numbers(previous)
        for label, numbers in current.items():
            previous_numbers = other.get(label)
            if previous_numbers and numbers.isdisjoint(previous_numbers):
                return 0, f"与已采纳证据在「{label}」上的数值不一致"
    return 2, None


def rate_hit(point: str, hit: CorpusHit, adopted_excerpts: list[str]) -> dict[str, int | str | None]:
    consistency, conflict = score_consistency(hit.excerpt, adopted_excerpts)
    return {
        "relevance": score_relevance(point, hit.excerpt),
        "specificity": score_specificity(hit.excerpt),
        "source_score": score_source(hit.source_file),
        "consistency": consistency,
        "conflict": conflict,
    }
