"""Check Agent：核对引用和要点，不重写、不重新检索。"""

from __future__ import annotations

import json
import re

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage
from langchain_core.tools import tool

from report_writing.agents.react import run_react, tool_result
from report_writing.agents.scripted import ScriptedChatModel, tool_payloads
from report_writing.domain.models import (
    MAX_REVISIONS,
    ChapterDraft,
    ChapterRequirement,
    CheckAction,
    CheckResult,
    EvidenceCard,
    ParagraphComment,
)
from report_writing.domain.rating import _NUMBER

INSTRUCTIONS = """你是报告检查员。先调用 verify，再根据结果选择 accept、revise 或 needs_human。
不要检索，不要重写正文。
revise 的 comments 含 index 和 message。needs_human 要给出 reason。

若 user 中 knowledge_fallback 为 true：表示本章无知识库采纳证据、已按要点写骨架正文。
此时只要 paragraphs 非空且对 points 有结构性覆盖，应 prefer accept（或 revise 润色），
不要因缺少项目专名/投资额等证据而 needs_human，也不要要求「正文必须为空」。
仅当 paragraphs 仍为空或完全未触及任何要点时，才 needs_human。"""


class CheckScriptModel(ScriptedChatModel):
    role: str = "check"

    def decide(self, messages: list[BaseMessage]):
        if tool_payloads(messages, "accept") or tool_payloads(messages, "revise") or tool_payloads(messages, "needs_human"):
            return self.finish("检查已提交")
        verified = tool_payloads(messages, "verify")
        if not verified:
            return self.call("verify")
        findings = verified[-1]["findings"]
        blocking = [item for item in findings if item["kind"] in {"uncovered", "coverage"}]
        if blocking:
            return self.call("needs_human", {"reason": blocking[0]["message"]})
        editable = [item for item in findings if "index" in item]
        if editable:
            return self.call(
                "revise",
                {"comments": [{"index": item["index"], "message": item["message"]} for item in editable]},
            )
        return self.call("accept")


def run_check(
    requirement: ChapterRequirement,
    evidence: list[EvidenceCard],
    draft: ChapterDraft,
    revision_count: int,
    model: BaseChatModel | None = None,
    *,
    knowledge_fallback: bool = False,
) -> CheckResult:
    if getattr(requirement, "fixed_output", False):
        return CheckResult(action=CheckAction.ACCEPT, reason="固定内容输出，跳过事实核对")

    @tool
    def verify() -> str:
        """对照书写要求和已采纳证据检查当前草稿。"""
        return json.dumps(
            {"findings": inspect_draft(requirement, evidence, draft, knowledge_fallback=knowledge_fallback)},
            ensure_ascii=False,
        )

    @tool
    def accept() -> str:
        """文字、覆盖和引用均通过。"""
        return json.dumps(CheckResult(action=CheckAction.ACCEPT).to_dict(), ensure_ascii=False)

    @tool
    def revise(comments: list[dict]) -> str:
        """把可修改的问题退回写作。comments 含段落序号和意见。"""
        if revision_count >= MAX_REVISIONS:
            result = CheckResult(action=CheckAction.NEEDS_HUMAN, reason="退回次数已达上限")
            return json.dumps(result.to_dict(), ensure_ascii=False)
        stored = [
            ParagraphComment(index=int(item["index"]), message=str(item.get("message") or ""))
            for item in comments
        ]
        return json.dumps(CheckResult(action=CheckAction.REVISE, comments=stored).to_dict(), ensure_ascii=False)

    @tool
    def needs_human(reason: str) -> str:
        """证据不足、要点无法覆盖或存在无法自动修改的问题。"""
        return json.dumps(CheckResult(action=CheckAction.NEEDS_HUMAN, reason=reason).to_dict(), ensure_ascii=False)

    chosen = model or CheckScriptModel()
    user = json.dumps(
        {
            "points": requirement.points,
            "boundary": requirement.boundary,
            "revision_count": revision_count,
            "knowledge_fallback": bool(knowledge_fallback),
            "evidence_adopted_count": len([c for c in evidence if c.adopted]),
            "draft": draft.to_dict(),
        },
        ensure_ascii=False,
    )
    result = run_react(chosen, [verify, accept, revise, needs_human], INSTRUCTIONS, user, f"check-{requirement.key}")
    messages = result["messages"]
    for name in ("needs_human", "revise", "accept"):
        payloads = [json.loads(message.content) for message in messages if getattr(message, "name", None) == name]
        if payloads:
            return CheckResult.from_dict(payloads[-1])
    return CheckResult.from_dict(tool_result(messages, "accept"))


def inspect_draft(
    requirement: ChapterRequirement,
    evidence: list[EvidenceCard],
    draft: ChapterDraft,
    *,
    knowledge_fallback: bool = False,
) -> list[dict]:
    if getattr(requirement, "fixed_output", False):
        return []
    adopted = {card.id: card for card in evidence if card.adopted}
    findings: list[dict] = []
    if not draft.paragraphs:
        findings.append({"kind": "coverage", "message": "本章正文为空，请根据要点撰写内容"})
    if draft.uncovered_points and not knowledge_fallback:
        findings.append({"kind": "uncovered", "message": "要点无法覆盖：" + "、".join(draft.uncovered_points)})
    banned = _banned_titles(requirement)
    for paragraph in draft.paragraphs:
        html = paragraph.text or ""
        if "image-placeholder" in html or "<figure" in html.lower():
            continue
        if not knowledge_fallback:
            if not paragraph.evidence_ids:
                findings.append({"kind": "citation", "index": paragraph.index, "message": "段落没有证据编号"})
                continue
            missing = [item_id for item_id in paragraph.evidence_ids if item_id not in adopted]
            if missing:
                findings.append(
                    {
                        "kind": "citation",
                        "index": paragraph.index,
                        "message": "引用了不存在或未采纳的证据：" + "、".join(missing),
                    }
                )
                continue
            excerpts = "\n".join(adopted[item_id].excerpt for item_id in paragraph.evidence_ids)
            from report_writing.domain.content_blocks import strip_html

            plain = re.sub(r"\[ev-\d+\]", "", strip_html(paragraph.text))
            excerpt_numbers = set(_NUMBER.findall(excerpts))
            invented = [number for number in _NUMBER.findall(plain) if number not in excerpt_numbers]
            if invented:
                findings.append(
                    {
                        "kind": "number",
                        "index": paragraph.index,
                        "message": "出现证据中没有的数值：" + "、".join(invented),
                    }
                )
        crossed = [title for title in banned if title and title in paragraph.text]
        if crossed:
            findings.append(
                {
                    "kind": "boundary",
                    "index": paragraph.index,
                    "message": "写入了本章边界之外的内容：" + "、".join(crossed),
                }
            )
    covered = "\n".join(paragraph.text for paragraph in draft.paragraphs)
    for point in requirement.points:
        if point in draft.uncovered_points or point in covered:
            continue
        if knowledge_fallback and (point in covered or f"待补充：{point}" in draft.uncovered_points):
            continue
        findings.append({"kind": "coverage", "message": f"要点「{point}」未写入正文，也未标为待补充"})
    if knowledge_fallback and draft.paragraphs and not any(
        f.get("kind") == "coverage" and "正文为空" in f.get("message", "") for f in findings
    ):
        findings = [f for f in findings if f.get("kind") != "uncovered"]
    return findings


def _banned_titles(requirement: ChapterRequirement) -> list[str]:
    if "禁止写入：" not in requirement.boundary:
        return []
    tail = requirement.boundary.split("禁止写入：", 1)[1]
    return [item.strip("。 ") for item in re.split(r"[、，,]", tail) if item.strip("。 ")]
