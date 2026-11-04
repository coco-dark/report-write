"""固定顺序的 LangGraph：学习一次，然后逐章检索、写作、检查。"""

from __future__ import annotations

from typing import Any, Literal

from langchain_core.language_models.chat_models import BaseChatModel
from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict

from report_writing.agents.check import run_check
from report_writing.agents.search import run_search
from report_writing.agents.study import run_study
from report_writing.agents.write import run_write
from report_writing.services.corpus import Corpus, ReportTemplate
from report_writing.domain.models import ChapterState, ChapterStatus, CheckAction, SampleChapter, SampleDocument, StudyRecord, WritingBrief
from report_writing.services.store import BriefCache


class PipelineState(TypedDict, total=False):
    brief: dict[str, Any] | None
    brief_from_cache: bool
    study_runs: int
    force_study: bool
    use_existing_brief: bool
    mode: Literal["full", "revise"]
    chapter_index: int
    chapters: list[dict[str, Any]]
    route: str


def build_pipeline(
    template: ReportTemplate,
    corpus: Corpus,
    cache: BriefCache,
    model: BaseChatModel | None = None,
    on_event=None,
):
    library = cache.library

    def emit(step: str, status: str, chapter: str = "", title: str = "", result: dict[str, Any] | None = None) -> None:
        if on_event is None:
            return
        on_event({
            "type": "step",
            "step": step,
            "status": status,
            "chapter": chapter,
            "title": title,
            "result": result or {},
        })

    def study_node(state: PipelineState) -> dict[str, Any]:
        emit("study", "running", title="分析样板")
        sample = _sample_from_template(template)
        library.save_sample(sample)
        if state.get("use_existing_brief") and state.get("brief"):
            brief = WritingBrief.from_dict(state["brief"])
            emit("study", "done", title="分析样板", result=_brief_result(brief, False))
            return {"brief_from_cache": False, "study_runs": state.get("study_runs") or 0}
        cached = None if state.get("force_study") else library.load_study(template.id, template.version)
        if cached is not None:
            emit("study", "done", title="分析样板", result=_brief_result(cached.brief, True))
            return {
                "brief": cached.brief.to_dict(),
                "brief_from_cache": True,
                "study_runs": 0,
                "chapters": _chapters_from_brief(cached.brief),
            }
        brief = run_study(template, model)
        library.save_study(StudyRecord(sample_id=template.id, sample_version=template.version, brief=brief))
        emit("study", "done", title="分析样板", result=_brief_result(brief, False))
        return {
            "brief": brief.to_dict(),
            "brief_from_cache": False,
            "study_runs": 1,
            "chapters": _chapters_from_brief(brief),
        }

    def open_chapter(state: PipelineState) -> dict[str, Any]:
        index = state["chapter_index"]
        chapters = list(state["chapters"])
        if index >= len(chapters):
            return {"route": "end"}
        if state.get("mode") == "revise":
            return {"route": "write"}
        chapter = ChapterState.from_dict(chapters[index])
        requirement = WritingBrief.from_dict(state["brief"]).chapter(chapter.key)
        if not requirement.points:
            chapter.status = ChapterStatus.NEEDS_HUMAN
            chapter.human_note = "章节要点为空，未进入写作"
            chapters[index] = chapter.to_dict()
            emit("search", "skipped", chapter.key, chapter.title, {"note": chapter.human_note})
            return {"chapters": chapters, "route": "advance"}
        chapter.status = ChapterStatus.SEARCHING
        chapters[index] = chapter.to_dict()
        return {"chapters": chapters, "route": "search"}

    def search_node(state: PipelineState) -> dict[str, Any]:
        index = state["chapter_index"]
        chapters = list(state["chapters"])
        chapter = ChapterState.from_dict(chapters[index])
        brief = WritingBrief.from_dict(state["brief"])
        requirement = brief.chapter(chapter.key)
        chapter.draft = None
        chapter.checks = []
        chapter.revision_count = 0
        chapter.official_text = ""
        emit("search", "running", chapter.key, chapter.title)
        chapter.evidence = run_search(corpus, requirement, model)
        adopted = chapter.adopted_evidence()
        fallback = not adopted
        note = "" if adopted else "知识库未检索到可采纳的证据，将按章节要点由模型撰写（具体数据须后续核对）"
        emit(
            "search",
            "done",
            chapter.key,
            chapter.title,
            {
                "adopted": len(adopted),
                "cards": [card.to_dict() for card in chapter.evidence],
                "note": note,
                "fallback_write": fallback,
            },
        )
        chapter.status = ChapterStatus.WRITING
        if fallback:
            chapter.human_note = note
        chapters[index] = chapter.to_dict()
        return {"chapters": chapters, "route": "write"}

    def write_node(state: PipelineState) -> dict[str, Any]:
        index = state["chapter_index"]
        chapters = list(state["chapters"])
        chapter = ChapterState.from_dict(chapters[index])
        brief = WritingBrief.from_dict(state["brief"])
        requirement = brief.chapter(chapter.key)
        comments = []
        if chapter.checks and chapter.checks[-1].action == CheckAction.REVISE:
            comments = chapter.checks[-1].comments
        chapter.status = ChapterStatus.WRITING
        emit("write", "running", chapter.key, chapter.title)
        knowledge_fallback = not chapter.adopted_evidence()
        chapter.draft = run_write(
            requirement,
            brief.paragraph_format,
            chapter.evidence,
            comments=comments,
            previous=chapter.draft,
            prior_summary=_prior_summary(chapters, requirement.depends_on),
            model=model,
            knowledge_fallback=knowledge_fallback,
        )
        emit(
            "write",
            "done",
            chapter.key,
            chapter.title,
            {"paragraphs": [item.text for item in chapter.draft.paragraphs]},
        )
        chapter.status = ChapterStatus.CHECKING
        chapters[index] = chapter.to_dict()
        return {"chapters": chapters}

    def check_node(state: PipelineState) -> dict[str, Any]:
        index = state["chapter_index"]
        chapters = list(state["chapters"])
        chapter = ChapterState.from_dict(chapters[index])
        brief = WritingBrief.from_dict(state["brief"])
        requirement = brief.chapter(chapter.key)
        emit("check", "running", chapter.key, chapter.title)
        result = run_check(
            requirement,
            chapter.evidence,
            chapter.draft,
            chapter.revision_count,
            model,
            knowledge_fallback=not chapter.adopted_evidence(),
        )
        chapter.checks.append(result)
        emit(
            "check",
            "done",
            chapter.key,
            chapter.title,
            {
                "action": result.action.value,
                "reason": result.reason,
                "comments": [item.to_dict() for item in result.comments],
            },
        )
        if result.action == CheckAction.REVISE:
            chapter.revision_count += 1
            chapter.status = ChapterStatus.WRITING
            route = "write"
        else:
            # accept / needs_human：有正文直接保存，不再待确认/待人工
            from report_writing.services.pipeline import seal_written_chapter

            if seal_written_chapter(
                chapter,
                note="写完自动保存" if result.action == CheckAction.ACCEPT else (result.reason or "写完自动保存"),
            ):
                route = "finish" if state.get("mode") == "revise" else "advance"
            else:
                chapter.status = ChapterStatus.NEEDS_HUMAN
                chapter.human_note = result.reason or "没有可保存的正文"
                route = "finish" if state.get("mode") == "revise" else "advance"
        chapters[index] = chapter.to_dict()
        return {"chapters": chapters, "route": route}

    def advance(state: PipelineState) -> dict[str, Any]:
        return {"chapter_index": state["chapter_index"] + 1}

    graph = StateGraph(PipelineState)
    graph.add_node("study", study_node)
    graph.add_node("open_chapter", open_chapter)
    graph.add_node("search", search_node)
    graph.add_node("write", write_node)
    graph.add_node("check", check_node)
    graph.add_node("advance", advance)
    graph.add_conditional_edges(
        START,
        lambda state: "write" if state.get("mode") == "revise" else "open_chapter" if state.get("use_existing_brief") else "study",
        {"study": "study", "open_chapter": "open_chapter", "write": "write"},
    )
    graph.add_conditional_edges(
        "study",
        lambda state: "end" if state.get("mode") == "study" else "open_chapter",
        {"open_chapter": "open_chapter", "end": END},
    )
    graph.add_conditional_edges(
        "open_chapter",
        lambda state: state["route"],
        {"search": "search", "advance": "advance", "write": "write", "end": END},
    )
    graph.add_conditional_edges("search", lambda state: state["route"], {"write": "write", "advance": "advance"})
    graph.add_edge("write", "check")
    graph.add_conditional_edges(
        "check",
        lambda state: state["route"],
        {"write": "write", "advance": "advance", "finish": END},
    )
    graph.add_edge("advance", "open_chapter")
    return graph.compile()


def _sample_from_template(template: ReportTemplate) -> SampleDocument:
    return SampleDocument(
        id=template.id,
        version=template.version,
        title=template.title,
        chapters=[
            SampleChapter(
                key=item.key,
                title=item.title,
                level=item.level,
                paragraphs=list(item.sample_paragraphs),
                depends_on=list(item.depends_on),
            )
            for item in template.chapters
        ],
    )


def _brief_result(brief: WritingBrief, from_cache: bool) -> dict[str, Any]:
    return {
        "from_cache": from_cache,
        "persisted": True,
        "sample_id": brief.template_id,
        "sample_version": brief.template_version,
        "voice": brief.voice,
        "layout": brief.layout,
        "paragraph_format": brief.paragraph_format,
        "chapters": [
            {
                "key": item.key,
                "sequence": item.sequence,
                "title": item.title,
                "describes": item.describes,
                "generate": item.generate,
                "keywords": list(item.keywords),
                "points": list(item.points),
            }
            for item in brief.chapters
        ],
    }


def _chapters_from_brief(brief: WritingBrief) -> list[dict[str, Any]]:
    return [ChapterState(key=item.key, title=item.title).to_dict() for item in brief.chapters]


def _prior_summary(chapters: list[dict[str, Any]], depends_on: list[str]) -> str:
    texts = []
    for key in depends_on:
        for raw in chapters:
            if raw["key"] == key and raw.get("status") == ChapterStatus.CONFIRMED.value and raw.get("official_text"):
                texts.append(str(raw["official_text"]))
    return "\n".join(texts)
