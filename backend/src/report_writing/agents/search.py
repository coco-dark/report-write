"""Search Agent：按要点检索，并对证据卡片评级。"""

from __future__ import annotations

import json
import re

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage
from langchain_core.tools import tool

from report_writing.agents.react import run_react, tool_result
from report_writing.agents.scripted import ScriptedChatModel, human_payload, tool_payloads
from report_writing.services.corpus import Corpus
from report_writing.domain.models import (
    ADOPT_SCORE,
    MAX_ADOPTED,
    MAX_EVIDENCE_CARDS,
    SEARCH_TOP_K,
    ChapterRequirement,
    EvidenceCard,
)
from report_writing.domain.rating import rate_hit

INSTRUCTIONS = """你是报告证据检索员。按本章给出的检索关键字做向量检索，不要写报告。
检索只使用当前业务类型的资料。对 points 中的每一项调用一次 search，全部检索完成后调用 submit_evidence。
（points 可能是合并后的一条查询，也可能是多个分开的检索词，按列表项逐条即可。）"""

_SPACE = re.compile(r"\s+")


def _search_queries(requirement: ChapterRequirement) -> list[str]:
    """默认把检索词合并成一条查询；节点开启 search_terms_separate 时再分开查。"""
    terms = requirement.search_terms()
    if not terms:
        return []
    if bool(getattr(requirement, "search_terms_separate", False)):
        return terms
    return [" ".join(terms)]


class SearchScriptModel(ScriptedChatModel):
    role: str = "search"

    def decide(self, messages: list[BaseMessage]):
        if tool_payloads(messages, "submit_evidence"):
            return self.finish("证据已提交")
        points = human_payload(messages).get("points") or []
        done = tool_payloads(messages, "search")
        if len(done) < len(points):
            return self.call("search", {"point": points[len(done)]})
        return self.call("submit_evidence")


def run_search(
    corpus: Corpus,
    requirement: ChapterRequirement,
    model: BaseChatModel | None = None,
    *,
    robot=None,
) -> list[EvidenceCard]:
    cards: list[EvidenceCard] = []
    sequence = {"n": 1}
    seen_excerpts: set[str] = set()

    @tool
    def search(point: str) -> str:
        """按一个章节要点做向量检索，并为每条命中评级。"""
        adopted_text = [card.excerpt for card in cards if card.adopted]
        added = []
        # 知识库已下线：检索仅走任务本地 corpus + 要素解析结果
        source_files = list(requirement.material_files or []) or None
        hits = corpus.search(point, limit=SEARCH_TOP_K, source_files=source_files)
        for hit in hits:
            fingerprint = _excerpt_key(hit.excerpt)
            if fingerprint and fingerprint in seen_excerpts:
                continue
            scores = rate_hit(point, hit, adopted_text)
            total = (
                int(scores["relevance"])
                + int(scores["specificity"])
                + int(scores["source_score"])
                + int(scores["consistency"])
            )
            source_score = int(scores["source_score"])
            adopted = total >= ADOPT_SCORE and not scores["conflict"] and source_score > 0
            reason = None
            if scores["conflict"]:
                reason = str(scores["conflict"])
                adopted = False
            elif source_score == 0:
                reason = "来源不是正式材料"
                adopted = False
            elif total < ADOPT_SCORE:
                reason = f"总分 {total} 低于采纳线 {ADOPT_SCORE}"
            card = EvidenceCard(
                id=f"ev-{sequence['n']}",
                point=point,
                source_file=hit.source_file,
                location=hit.location,
                excerpt=hit.excerpt,
                query=point,
                relevance=int(scores["relevance"]),
                specificity=int(scores["specificity"]),
                source_score=int(scores["source_score"]),
                consistency=int(scores["consistency"]),
                adopted=adopted,
                reject_reason=reason,
            )
            sequence["n"] += 1
            if fingerprint:
                seen_excerpts.add(fingerprint)
            cards.append(card)
            if adopted:
                adopted_text.append(card.excerpt)
            added.append(card.to_dict())
        _cap_adopted(cards)
        return json.dumps({"point": point, "cards": added}, ensure_ascii=False)

    @tool
    def submit_evidence() -> str:
        """提交本章全部证据卡片。这是检索阶段的结束动作。"""
        pruned = _prune_cards(cards)
        cards.clear()
        cards.extend(pruned)
        return json.dumps([card.to_dict() for card in cards], ensure_ascii=False)

    chosen = model or SearchScriptModel()
    queries = _search_queries(requirement)
    user = json.dumps(
        {
            "sequence": requirement.sequence,
            "chapter": requirement.title,
            "describes": requirement.describes,
            "generate": requirement.generate,
            "aux_prompt": requirement.aux_prompt,
            "material_files": list(requirement.material_files or []),
            "knowledge_folder_ids": [],
            "element_results": list(requirement.element_results or []),
            "search_terms_separate": bool(getattr(requirement, "search_terms_separate", False)),
            "points": queries,
        },
        ensure_ascii=False,
    )
    result = run_react(
        chosen,
        [search, submit_evidence],
        INSTRUCTIONS,
        user,
        f"search-{requirement.key}",
        rounds=len(queries) + 2,
    )
    return [EvidenceCard.from_dict(item) for item in tool_result(result["messages"], "submit_evidence")]


def _excerpt_key(text: str) -> str:
    cleaned = _SPACE.sub("", (text or "").strip())
    return cleaned[:96].lower() if cleaned else ""


def _cap_adopted(cards: list[EvidenceCard]) -> None:
    adopted = sorted((card for card in cards if card.adopted), key=lambda card: card.total, reverse=True)
    keep = {card.id for card in adopted[:MAX_ADOPTED]}
    for card in cards:
        if card.adopted and card.id not in keep:
            card.adopted = False
            card.reject_reason = f"超过每章采纳上限 {MAX_ADOPTED}"


def _prune_cards(cards: list[EvidenceCard]) -> list[EvidenceCard]:
    """先保采纳（≤MAX_ADOPTED），再用高分未采纳填满到 MAX_EVIDENCE_CARDS。"""
    _cap_adopted(cards)
    adopted = sorted((card for card in cards if card.adopted), key=lambda card: card.total, reverse=True)[:MAX_ADOPTED]
    adopted_ids = {card.id for card in adopted}
    rejected = sorted(
        (card for card in cards if card.id not in adopted_ids),
        key=lambda card: card.total,
        reverse=True,
    )
    room = max(0, MAX_EVIDENCE_CARDS - len(adopted))
    kept = adopted + rejected[:room]
    # 按原出现顺序重排，便于对照时间线
    order = {card.id: index for index, card in enumerate(cards)}
    kept.sort(key=lambda card: order.get(card.id, 10**9))
    return kept
