"""Check Agent：核对引用和要点，不重写、不重新检索。"""

from __future__ import annotations

import json
import re
from typing import Annotated

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage
from langchain.tools import tool
from pydantic import Field

from report_writing.agents.react import run_react, tool_result
from report_writing.agents.scripted import ScriptedChatModel, tool_payloads
from report_writing.domain.models import (
    MAX_REVISIONS,
    WRITE_SCORE_PASS,
    ChapterDraft,
    ChapterRequirement,
    CheckAction,
    CheckResult,
    EvidenceCard,
    ParagraphComment,
)
from report_writing.domain.rating import _NUMBER

INSTRUCTIONS = """# Role
你是资深文档审稿人。文档类型见 user.doc_category（函件、方案、说明书、纪要、合同、述职、报告等）。不要用研究报告标准去卡函件或说明书。

# Task
先核对草稿，再根据 findings 选择通过、退回改写或交人工。不要检索，不要自己重写正文。

# Inputs
user JSON：draft、points、describes、writing_strategy、knowledge_fallback、evidence。

# Checks
- 是否按 points 的组成部分顺序成文，而不是把要点指令句贴成提纲。
- 版式是否与 describes / 要点中的「版式」相符（该表出表、该条列出条列）。
- 是否串写邻章专题。

# Strategy scale
- template_fill / fixed：骨架或原文输出，不要因缺证据/未改写而 needs_human。
- auto_generate 或 knowledge_fallback=true：无项目专名也可 accept；不要因缺投资额等证据交人工。paragraphs 非空且对 points 有结构性覆盖时 prefer accept（或 revise 润色）。仅当正文为空或完全未触及组成块时才 needs_human。
- source_only：无资料支撑的事实句才记 citation；不要要求照抄参考正文。
- hybrid：主题无关未写入可接受（uncovered_points）；不要因「没写无关专题」退回。

# Output
- 通过：accept
- 退回改写：revise，comments 含段落 index 与 message
- 交人工：needs_human，必须给 reason
"""


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
    from report_writing.domain.chapter_compose import COMPOSE_FIXED, COMPOSE_TEMPLATE_FILL, compose_mode

    mode = compose_mode(requirement)
    if getattr(requirement, "fixed_output", False) or mode in {COMPOSE_TEMPLATE_FILL, COMPOSE_FIXED}:
        return attach_writing_score(
            CheckResult(action=CheckAction.ACCEPT, reason="固定/填槽输出，跳过事实核对"),
            [],
        )

    @tool(
        "verify",
        description=(
            "何时：每轮检查必须先核对草稿，对照要点、引用和边界生成 findings。"
            "不要：用 accept/revise/needs_human 代替本工具。"
        ),
    )
    def verify() -> str:
        return json.dumps(
            {"findings": inspect_draft(requirement, evidence, draft, knowledge_fallback=knowledge_fallback)},
            ensure_ascii=False,
        )

    @tool(
        "accept",
        description=(
            "何时：verify 之后 findings 无可改问题，通过本章。"
            "不要：未 verify 时调用；有 uncovered/coverage 用 needs_human；有段落意见用 revise。"
        ),
    )
    def accept() -> str:
        return json.dumps(CheckResult(action=CheckAction.ACCEPT).to_dict(), ensure_ascii=False)

    @tool(
        "revise",
        description=(
            "何时：verify 之后有可改段落问题，退回写作。"
            "不要：用 accept 代替；无法自动改的用 needs_human。"
        ),
    )
    def revise(
        comments: Annotated[list[dict], Field(description="必填。每项含段落 index 与 message。")],
    ) -> str:
        stored = [
            ParagraphComment(index=int(item["index"]), message=str(item.get("message") or ""))
            for item in comments
        ]
        # 达上限仍保留缺陷意见，供下一轮（人工重试）写作改正
        if revision_count >= MAX_REVISIONS:
            result = CheckResult(
                action=CheckAction.NEEDS_HUMAN,
                reason="退回次数已达上限",
                comments=stored,
            )
            return json.dumps(result.to_dict(), ensure_ascii=False)
        return json.dumps(CheckResult(action=CheckAction.REVISE, comments=stored).to_dict(), ensure_ascii=False)

    @tool(
        "needs_human",
        description=(
            "何时：verify 之后存在无法自动修改的问题（正文空、要点未覆盖等），交人工。"
            "不要：knowledge_fallback 为 true 时因缺专名/数值调用；可改段落用 revise。"
        ),
    )
    def needs_human(reason: Annotated[str, Field(description="必填。交人工的原因。")]) -> str:
        return json.dumps(CheckResult(action=CheckAction.NEEDS_HUMAN, reason=reason).to_dict(), ensure_ascii=False)

    chosen = model or CheckScriptModel()
    user = json.dumps(
        {
            "points": requirement.points,
            "describes": requirement.describes,
            "boundary": requirement.boundary,
            "revision_count": revision_count,
            "max_revisions": MAX_REVISIONS,
            "write_score_pass": WRITE_SCORE_PASS,
            "knowledge_fallback": bool(knowledge_fallback),
            "writing_strategy": compose_mode(requirement),
            "evidence_adopted_count": len([c for c in evidence if c.adopted]),
            "draft": draft.to_dict(),
        },
        ensure_ascii=False,
    )
    result = run_react(chosen, [verify, accept, revise, needs_human], INSTRUCTIONS, user, f"check-{requirement.key}")
    messages = result["messages"]
    checked: CheckResult | None = None
    for name in ("needs_human", "revise", "accept"):
        payloads = [json.loads(message.content) for message in messages if getattr(message, "name", None) == name]
        if payloads:
            checked = CheckResult.from_dict(payloads[-1])
            break
    if checked is None:
        checked = CheckResult.from_dict(tool_result(messages, "accept"))
    findings = inspect_draft(requirement, evidence, draft, knowledge_fallback=knowledge_fallback)
    checked = enrich_check_with_findings(checked, findings)
    return attach_writing_score(checked, findings)


def enrich_check_with_findings(result: CheckResult, findings: list[dict] | None) -> CheckResult:
    """把核对 findings / reason 固化进 comments，下一轮写作才能看到缺陷。"""
    comments = list(result.comments or [])
    seen = {(int(c.index), str(c.message or "").strip()) for c in comments}
    for item in findings or []:
        if not isinstance(item, dict):
            continue
        msg = str(item.get("message") or "").strip()
        if not msg:
            continue
        raw_idx = item.get("index")
        try:
            idx = int(raw_idx) if raw_idx is not None and str(raw_idx).strip() != "" else -1
        except (TypeError, ValueError):
            idx = -1
        key = (idx, msg)
        if key in seen:
            continue
        comments.append(ParagraphComment(index=idx, message=msg))
        seen.add(key)
    reason = str(result.reason or "").strip()
    if reason and (-1, reason) not in seen and not any(str(c.message or "").strip() == reason for c in comments):
        comments.append(ParagraphComment(index=-1, message=reason))
    if not reason and comments:
        reason = str(comments[0].message or "").strip()
    return result.model_copy(update={"comments": comments, "reason": reason})


def revision_feedback_from_checks(checks: list[CheckResult] | None) -> list[ParagraphComment]:
    """取最近一次未通过检查的缺陷，交给下一轮写作。"""
    rows = [item for item in (checks or []) if isinstance(item, CheckResult)]
    for item in reversed(rows):
        if item.action == CheckAction.ACCEPT:
            continue
        out = list(item.comments or [])
        reason = str(item.reason or "").strip()
        if reason and not any(str(c.message or "").strip() == reason for c in out):
            out.append(ParagraphComment(index=-1, message=reason))
        return out
    return []


def compute_writing_score(findings: list[dict] | None, action: CheckAction | str) -> int:
    """根据核对 findings 与最终动作估一个 0–100 写作分，供进度卡展示。"""
    act = action.value if isinstance(action, CheckAction) else str(action or "").strip()
    rows = [item for item in (findings or []) if isinstance(item, dict)]
    score = 100
    for item in rows:
        kind = str(item.get("kind") or "")
        if kind in {"coverage", "uncovered"}:
            score -= 22
        elif kind == "boundary":
            score -= 16
        elif kind == "number":
            score -= 10
        elif kind == "citation":
            score -= 8
        else:
            score -= 6
    score = max(0, min(100, score))
    if act == CheckAction.ACCEPT.value:
        return max(score, WRITE_SCORE_PASS) if score >= WRITE_SCORE_PASS - 5 else score
    if act == CheckAction.REVISE.value:
        return min(score, WRITE_SCORE_PASS - 1)
    # needs_human
    return min(score, max(0, WRITE_SCORE_PASS - 15))


def attach_writing_score(result: CheckResult, findings: list[dict] | None) -> CheckResult:
    score = compute_writing_score(findings, result.action)
    below = score < WRITE_SCORE_PASS
    return result.model_copy(update={"score": int(score), "below_threshold": bool(below)})


def inspect_draft(
    requirement: ChapterRequirement,
    evidence: list[EvidenceCard],
    draft: ChapterDraft,
    *,
    knowledge_fallback: bool = False,
) -> list[dict]:
    from report_writing.domain.chapter_compose import COMPOSE_FIXED, COMPOSE_TEMPLATE_FILL, compose_mode

    if getattr(requirement, "fixed_output", False) or compose_mode(requirement) in {
        COMPOSE_TEMPLATE_FILL,
        COMPOSE_FIXED,
    }:
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
        if point in draft.uncovered_points or f"待补充：{point}" in draft.uncovered_points:
            continue
        if any(needle and needle in covered for needle in _point_coverage_needles(point)):
            continue
        findings.append({"kind": "coverage", "message": f"组成块「{point}」未写入正文，也未标为待补充"})
    if knowledge_fallback and draft.paragraphs and not any(
        f.get("kind") == "coverage" and "正文为空" in f.get("message", "") for f in findings
    ):
        findings = [f for f in findings if f.get("kind") != "uncovered"]
    return findings


def _point_coverage_needles(point: str) -> list[str]:
    """正文不必回显整句要点指令；组成块名或要点前半句出现即视为覆盖。"""
    text = str(point or "").strip()
    if not text:
        return []
    needles = [text]
    m = re.match(r"【([^】]+)】", text)
    if m:
        label = m.group(1).strip()
        if label:
            needles.append(label)
    head = re.split(r"[；;]", text, 1)[0].strip()
    head = re.sub(r"^【[^】]+】", "", head).strip()
    if head and len(head) >= 4:
        needles.append(head[:24])
    return needles


def _banned_titles(requirement: ChapterRequirement) -> list[str]:
    if "禁止写入：" not in requirement.boundary:
        return []
    tail = requirement.boundary.split("禁止写入：", 1)[1]
    return [item.strip("。 ") for item in re.split(r"[、，,]", tail) if item.strip("。 ")]
