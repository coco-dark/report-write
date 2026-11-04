"""总控 Agent：自己决定何时调用学习、检索、编写和检查。"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from langchain.agents import create_agent
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage
from langchain_core.tools import tool

from report_writing.agents.check import run_check
from report_writing.agents.scripted import ScriptedChatModel, human_payload, tool_payloads
from report_writing.agents.study import run_study
from report_writing.agents.write import run_write
from report_writing.services.corpus import Corpus, ReportTemplate
from report_writing.services.graph import _brief_result, _chapters_from_brief, _prior_summary, _sample_from_template
from report_writing.domain.models import (
    ChapterRequirement,
    ChapterState,
    ChapterStatus,
    CheckAction,
    EvidenceCard,
    StepEvent,
    StudyRecord,
    WritingBrief,
)

EmitStepFn = Callable[[str, str, str, str, dict | None], None]
from report_writing.services.store import BriefCache


_log = logging.getLogger("uvicorn.error")

INSTRUCTIONS = """你是报告书写总控。你不检索、不写正文、不检查，只通过工具管理四个专员。
phase=study：只调用一次 call_study，然后用一句话结束，等待人工确认学习结果。
phase=write：学习结果已经确认。按给出的章节顺序，对每一章调用 call_search。
检索无采纳证据时仍调用 call_write（按要点由模型撰写一般性表述），再 call_check。
仅当 call_search 返回 skipped=true（如纯目录节点）时跳过编写。
检查结果为 revise 时再次 call_write 和 call_check。结果为 accept 或 needs_human 时进入下一章。
phase=revise：不要检索。对指定章节调用 call_write，再调用 call_check。
phase=search：只对给出的每一章调用 call_search，不要 call_write 或 call_check。
phase=full：先 call_study，再按 phase=write 的方式处理学习到的每一章。
全部章节处理完后用一句话结束，不要再调用工具。"""


class SupervisorScriptModel(ScriptedChatModel):
    role: str = "supervisor"

    def decide(self, messages: list[BaseMessage]):
        payload = human_payload(messages)
        phase = payload.get("phase") or "full"
        if phase in {"study", "full"} and not tool_payloads(messages, "call_study"):
            return self.call("call_study")
        if phase == "study":
            return self.finish("学习结果已提交，等待确认")
        if phase == "search":
            chapters = list(payload.get("chapters") or [])
            searches = {item["key"]: item for item in tool_payloads(messages, "call_search")}
            for chapter in chapters:
                key = chapter["key"]
                if key not in searches:
                    return self.call("call_search", {"key": key})
            return self.finish("检索已完成")
        chapters = list(payload.get("chapters") or [])
        if phase == "full":
            studied = tool_payloads(messages, "call_study")
            chapters = list(studied[-1].get("chapters") or []) if studied else []
        searches = {item["key"]: item for item in tool_payloads(messages, "call_search")}
        writes: dict[str, int] = {}
        checks: dict[str, list[dict]] = {}
        for item in tool_payloads(messages, "call_write"):
            writes[item["key"]] = writes.get(item["key"], 0) + 1
        for item in tool_payloads(messages, "call_check"):
            checks.setdefault(item["key"], []).append(item)
        for chapter in chapters:
            key = chapter["key"]
            if phase != "revise" and key not in searches:
                return self.call("call_search", {"key": key})
            if phase != "revise":
                found = searches[key]
                if found.get("skipped"):
                    continue
            written = writes.get(key, 0)
            reviewed = checks.get(key, [])
            if written == len(reviewed):
                if reviewed and reviewed[-1].get("action") != "revise":
                    continue
                return self.call("call_write", {"key": key})
            return self.call("call_check", {"key": key})
        return self.finish("各专员已完成本阶段")


@dataclass
class SupervisorResult:
    brief: WritingBrief | None = None
    chapters: list[ChapterState] = field(default_factory=list)
    study_runs: int = 0
    brief_from_cache: bool = False
    steps: list[StepEvent] = field(default_factory=list)


def _analyst_name(model) -> str:
    if model is None:
        return "scripted"
    name = str(getattr(model, "model_name", None) or getattr(model, "model", None) or model.__class__.__name__)
    return f"{name}:v2"


def execute_chapter_search(
    chapter: ChapterState,
    requirement: ChapterRequirement,
    corpus: Corpus | None,
    model,
    robot,
    emit: EmitStepFn,
    *,
    search_only: bool = False,
) -> dict:
    """单章检索与证据评级；search_only 时不进入写作态。"""
    if getattr(requirement, "structure_only", False):
        chapter.status = ChapterStatus.SKIPPED
        chapter.human_note = "目录节点，无需检索与写作"
        emit("search", "skipped", chapter.key, chapter.title, {"note": chapter.human_note})
        return {"key": chapter.key, "skipped": True, "adopted": 0, "note": chapter.human_note}
    if getattr(requirement, "fixed_output", False):
        fixed = (getattr(requirement, "reference_text", None) or "").strip()
        if not fixed:
            chapter.status = ChapterStatus.NEEDS_HUMAN
            chapter.human_note = "已开启固定内容，但未填写固定正文"
            emit("search", "skipped", chapter.key, chapter.title, {"note": chapter.human_note})
            return {
                "key": chapter.key,
                "skipped": True,
                "adopted": 0,
                "note": chapter.human_note,
                "fixed_output": True,
            }
        chapter.draft = None
        chapter.checks = []
        chapter.revision_count = 0
        chapter.official_text = ""
        chapter.evidence = []
        chapter.human_note = ""
        if search_only:
            chapter.status = ChapterStatus.NEEDS_HUMAN
            chapter.human_note = "固定内容，跳过检索"
        else:
            chapter.status = ChapterStatus.WRITING
        note = "固定内容，跳过检索"
        emit(
            "search",
            "done",
            chapter.key,
            chapter.title,
            {"adopted": 1, "cards": [], "note": note, "fixed_output": True},
        )
        return {"key": chapter.key, "skipped": False, "adopted": 1, "note": note, "fixed_output": True}
    element_results = list(getattr(requirement, "element_results", None) or [])
    element_ids = list(getattr(requirement, "element_ids", None) or [])
    has_points = bool(requirement.points or (getattr(requirement, "describes", None) or "").strip())

    # 过程条：先亮检索意图（槽位/检索词），避免用户只看到「按要点写」误以为没检索
    if element_ids and element_results:
        emit(
            "search",
            "running",
            chapter.key,
            chapter.title,
            {
                "note": f"正在按 {len(element_results)} 个关联要素检索资料…",
                "element_driven": True,
            },
        )
        for item in element_results[:8]:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or item.get("id") or "要素").strip() or "要素"
            kind = str(item.get("kind") or "").strip()
            query = str(item.get("query") or "").strip()
            if kind == "file_slot":
                gaps = item.get("slot_missing") or []
                if gaps:
                    tip = f"　▶ 引用槽位「{name}」：槽位 {('、'.join(str(n) for n in gaps))} 无文件，跳过"
                elif query:
                    tip = f"　▶ 引用槽位「{name}」检索：{query[:80]}"
                else:
                    tip = f"　▶ 引用槽位「{name}」检索中…"
            elif query:
                tip = f"　▶ 要素「{name}」检索：{query[:80]}"
            else:
                tip = f"　▶ 要素「{name}」（{kind or '配置'}）解析中…"
            emit("search", "running", chapter.key, chapter.title, {"note": tip})

    def _finish_element_search(
        note: str,
        *,
        fallback_write: bool = False,
        slot_missing: list[int] | None = None,
    ) -> dict:
        chapter.draft = None
        chapter.checks = []
        chapter.revision_count = 0
        chapter.official_text = ""
        filled = [item for item in element_results if str(item.get("value") or "").strip()]
        cards = [
            {
                "id": f"el-{item.get('id') or idx}",
                "point": str(item.get("name") or item.get("id") or "要素"),
                "source_file": "要素",
                "location": str(item.get("kind") or ""),
                "query": str(item.get("query") or "").strip(),
                "excerpt": str(item.get("value") or "")[:800],
                "adopted": True,
            }
            for idx, item in enumerate(filled)
        ]
        # 写入章节证据，供进度卡「证据」与过程条展示检索到的要素内容
        chapter.evidence = [
            EvidenceCard.from_dict(
                {
                    "id": card["id"],
                    "point": card["point"],
                    "source_file": card["source_file"],
                    "location": card["location"],
                    "query": card.get("query") or "",
                    "excerpt": card["excerpt"],
                    "adopted": True,
                    "relevance": 3,
                    "specificity": 3,
                    "source_score": 3,
                    "consistency": 3,
                }
            )
            for card in cards
        ]
        gaps = [int(n) for n in (slot_missing or []) if int(n) > 0]
        payload = {
            "adopted": len(cards),
            "cards": cards,
            "note": note,
            "fallback_write": fallback_write,
            "element_driven": bool(element_ids),
        }
        if gaps:
            payload["slot_missing"] = gaps
        emit("search", "done", chapter.key, chapter.title, payload)
        if search_only:
            chapter.status = ChapterStatus.NEEDS_HUMAN
            chapter.human_note = note
        else:
            chapter.status = ChapterStatus.WRITING
            chapter.human_note = note
        return {
            "key": chapter.key,
            "skipped": False,
            "adopted": len(cards),
            "note": note,
            "fallback_write": fallback_write,
            **({"slot_missing": gaps} if gaps else {}),
        }

    if element_ids:
        filled = [item for item in element_results if str(item.get("value") or "").strip()]
        slot_gaps: list[int] = []
        for item in element_results:
            if str(item.get("kind") or "").strip() != "file_slot":
                continue
            for no in item.get("slot_missing") or []:
                try:
                    n = int(no)
                except (TypeError, ValueError):
                    continue
                if n > 0 and n not in slot_gaps:
                    slot_gaps.append(n)
        # 引用槽位找不到对应资料包文件：不对该槽做检索（已在 resolve 跳过）
        if filled:
            note = f"已根据 {len(filled)} 个关联要素准备写作资料"
            if slot_gaps:
                gap_txt = "、".join(str(n) for n in slot_gaps)
                note = f"{note}；槽位 {gap_txt} 未对齐/未上传，已跳过检索"
            return _finish_element_search(note, fallback_write=False, slot_missing=slot_gaps)
        if slot_gaps:
            gap_txt = "、".join(str(n) for n in slot_gaps)
            if has_points:
                note = f"资料包槽位 {gap_txt} 未找到文件，已跳过检索；将结合章节要点撰写"
                return _finish_element_search(
                    note, fallback_write=True, slot_missing=slot_gaps
                )
            chapter.status = ChapterStatus.NEEDS_HUMAN
            chapter.human_note = (
                f"资料包槽位 {gap_txt} 未找到文件，已跳过检索；请上传槽位文件或补充写作要点"
            )
            emit(
                "search",
                "skipped",
                chapter.key,
                chapter.title,
                {"note": chapter.human_note, "slot_missing": slot_gaps},
            )
            return {
                "key": chapter.key,
                "skipped": True,
                "adopted": 0,
                "note": chapter.human_note,
                "slot_missing": slot_gaps,
            }
        if has_points:
            note = "要素未解析到内容，将结合章节要点撰写（请核对要素配置）"
            return _finish_element_search(note, fallback_write=True)
        chapter.status = ChapterStatus.NEEDS_HUMAN
        chapter.human_note = "关联要素未解析到可用内容，请检查要素配置或补充写作要点"
        emit("search", "skipped", chapter.key, chapter.title, {"note": chapter.human_note})
        return {"key": chapter.key, "skipped": True, "adopted": 0, "note": chapter.human_note}

    if requirement.search_terms():
        chapter.status = ChapterStatus.NEEDS_HUMAN
        chapter.human_note = "章节级检索词已停用，请为本章关联要素（含 RAG 要素）以拉取资料"
        emit("search", "skipped", chapter.key, chapter.title, {"note": chapter.human_note})
        return {"key": chapter.key, "skipped": True, "adopted": 0, "note": chapter.human_note}

    if not has_points:
        chapter.status = ChapterStatus.NEEDS_HUMAN
        chapter.human_note = "章节要点为空且未关联要素，未进入写作"
        emit("search", "skipped", chapter.key, chapter.title, {"note": chapter.human_note})
        return {"key": chapter.key, "skipped": True, "adopted": 0, "note": chapter.human_note}
    note = "将按章节要点由模型撰写（资料请用要素「文件」或资料包槽位）"
    return _finish_element_search(note, fallback_write=True)


def _reusable(brief, analyst: str) -> bool:
    marker = brief.analyzed_by or ""
    if analyst == "scripted":
        return marker in {"", "scripted"}
    return marker == analyst


def run_supervisor(
    *,
    phase: str,
    template: ReportTemplate,
    cache: BriefCache,
    corpus: Corpus | None = None,
    model: BaseChatModel | None = None,
    brief: WritingBrief | None = None,
    chapters: list[ChapterState] | None = None,
    force_study: bool = False,
    on_event=None,
    robot=None,
) -> SupervisorResult:
    result = SupervisorResult(
        brief=brief,
        chapters=list(chapters or []),
        study_runs=0,
    )
    library = cache.library

    def emit(step: str, status: str, chapter: str = "", title: str = "", payload: dict | None = None) -> None:
        event = {
            "type": "step",
            "step": step,
            "status": status,
            "chapter": chapter,
            "title": title,
            "result": payload or {},
        }
        result.steps.append(StepEvent.from_dict(event))
        if on_event is not None:
            on_event(event)

    def find(key: str) -> ChapterState:
        for item in result.chapters:
            if item.key == key:
                return item
        raise KeyError(key)

    @tool
    def call_study() -> str:
        """调用学习专员，分析模版并保存学习结果。"""
        if phase not in {"study", "full"}:
            return json.dumps({"error": "学习结果已确认，不要重新学习"}, ensure_ascii=False)
        emit("study", "running", title="分析样板")
        library.save_sample(_sample_from_template(template))
        analyst = _analyst_name(model)
        cached = None if force_study else library.load_study(template.id, template.version)
        if cached is not None and not _reusable(cached.brief, analyst):
            _log.info("已保存的学习结果来自 %s，当前模型是 %s，重新分析", cached.brief.analyzed_by or "未知", analyst)
            cached = None
        if cached is not None:
            _log.info("沿用已保存的学习结果，这次不调用模型")
            learned = cached.brief
            result.brief = learned
            result.brief_from_cache = True
            result.study_runs = 0
            result.chapters = [ChapterState.from_dict(item) for item in _chapters_from_brief(learned)]
            payload = _brief_result(learned, True)
            emit("study", "done", title="分析样板", payload=payload)
            return json.dumps(payload, ensure_ascii=False)
        _log.info("调用模型 %s 分析模版，共 %s 章", analyst, len(template.chapters))
        learned = run_study(template, model, robot=robot)
        learned.analyzed_by = analyst
        library.save_study(StudyRecord(sample_id=template.id, sample_version=template.version, brief=learned))
        result.brief = learned
        result.brief_from_cache = False
        result.study_runs = 1
        result.chapters = [ChapterState.from_dict(item) for item in _chapters_from_brief(learned)]
        payload = _brief_result(learned, False)
        emit("study", "done", title="分析样板", payload=payload)
        return json.dumps(payload, ensure_ascii=False)

    @tool
    def call_search(key: str) -> str:
        """按该章学习要点检索资料，并评级采纳。"""
        if phase == "study" or result.brief is None:
            return json.dumps({"key": key, "error": "尚未确认学习结果"}, ensure_ascii=False)
        chapter = find(key)
        requirement = result.brief.chapter(key)
        payload = execute_chapter_search(
            chapter,
            requirement,
            corpus,
            model,
            robot,
            emit,
            search_only=(phase == "search"),
        )
        return json.dumps(payload, ensure_ascii=False)

    @tool
    def call_write(key: str) -> str:
        """根据该章已采纳证据编写正文。"""
        if phase == "search":
            return json.dumps({"key": key, "error": "当前为仅检索阶段，不要写作"}, ensure_ascii=False)
        if result.brief is None:
            return json.dumps({"key": key, "error": "没有学习结果"}, ensure_ascii=False)
        chapter = find(key)
        requirement = result.brief.chapter(key)
        comments = []
        if chapter.checks and chapter.checks[-1].action == CheckAction.REVISE:
            comments = chapter.checks[-1].comments
        knowledge_fallback = not chapter.adopted_evidence()
        chapter.status = ChapterStatus.WRITING
        points = [str(p).strip() for p in (requirement.points or []) if str(p).strip()]
        emit(
            "write",
            "running",
            chapter.key,
            chapter.title,
            {
                "points": points[:20],
                "describes": (requirement.describes or "").strip()[:240],
                "aux_prompt": (requirement.aux_prompt or "").strip()[:240],
                "note": f"按 {len(points)} 条写作要点撰写" if points else "未配置写作要点",
            },
        )
        # 首写不带 previous；仅检查要求改稿时才沿用上一稿
        previous = chapter.draft if comments else None
        chapter.draft = run_write(
            requirement,
            result.brief.paragraph_format,
            chapter.evidence,
            comments=comments,
            previous=previous,
            prior_summary=_prior_summary([item.to_dict() for item in result.chapters], requirement.depends_on),
            model=model,
            robot=robot,
            knowledge_fallback=knowledge_fallback,
        )
        paragraphs = [item.text for item in chapter.draft.paragraphs]
        emit(
            "write",
            "done",
            chapter.key,
            chapter.title,
            {"paragraphs": paragraphs, "points": points[:20]},
        )
        chapter.status = ChapterStatus.CHECKING
        return json.dumps({"key": key, "paragraphs": paragraphs}, ensure_ascii=False)

    @tool
    def call_check(key: str) -> str:
        """检查该章文字、引用和要点覆盖。"""
        if phase == "search":
            return json.dumps({"key": key, "error": "当前为仅检索阶段，不要检查"}, ensure_ascii=False)
        if result.brief is None or find(key).draft is None:
            return json.dumps({"key": key, "error": "没有可检查的草稿"}, ensure_ascii=False)
        chapter = find(key)
        requirement = result.brief.chapter(key)
        emit("check", "running", chapter.key, chapter.title)
        checked = run_check(
            requirement,
            chapter.evidence,
            chapter.draft,
            chapter.revision_count,
            model,
            knowledge_fallback=not chapter.adopted_evidence(),
        )
        chapter.checks.append(checked)
        emit(
            "check",
            "done",
            chapter.key,
            chapter.title,
            {
                "action": checked.action.value,
                "reason": checked.reason,
                "comments": [item.to_dict() for item in checked.comments],
            },
        )
        if checked.action == CheckAction.REVISE:
            chapter.revision_count += 1
            chapter.status = ChapterStatus.WRITING
        else:
            # accept / needs_human：有正文直接保存为已确认，不再待人工确认
            from report_writing.services.pipeline import seal_written_chapter

            if not seal_written_chapter(
                chapter,
                note="写完自动保存" if checked.action == CheckAction.ACCEPT else (checked.reason or "写完自动保存"),
            ):
                chapter.status = ChapterStatus.NEEDS_HUMAN
                chapter.human_note = checked.reason or "没有可保存的正文"
        return json.dumps({"key": key, "action": checked.action.value, "reason": checked.reason}, ensure_ascii=False)

    chat = model if model is not None else SupervisorScriptModel()
    agent = create_agent(
        chat,
        [call_study, call_search, call_write, call_check],
        system_prompt=INSTRUCTIONS,
        name="supervisor",
    )
    user = {
        "phase": phase,
        "chapters": [
            {"key": item.key, "title": item.title, "points": list(result.brief.chapter(item.key).points) if result.brief else []}
            for item in result.chapters
        ],
    }
    chapter_count = max(len(template.chapters), len(result.chapters), 1)
    agent.invoke(
        {"messages": [{"role": "user", "content": json.dumps(user, ensure_ascii=False)}]},
        config={"recursion_limit": chapter_count * 16 + 16},
    )
    return result
