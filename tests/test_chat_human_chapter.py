"""待人工：编辑确认正文、配置章节后重试。"""

from __future__ import annotations

from pathlib import Path

from report_writing.chat_orchestrator import (
    _apply_chapter_config,
    _progress_and_confirm_cards,
    handle_chat,
)
from report_writing.controller import ensure_task
from report_writing.domain.models import (
    ChapterRequirement,
    ChapterState,
    ChapterStatus,
    ChatRequest,
    HumanDecision,
    OutlineNode,
    OutlineTree,
    WritingBrief,
)
from report_writing.services.pipeline import confirm_chapter
from report_writing.services.store import TaskStore


def _seed_task(home: Path, task_id: str = "w_human") -> Path:
    path = home / "tasks" / task_id
    ensure_task(path)
    store = TaskStore(path)
    task = store.load()
    task.outline = OutlineTree(
        chapters=[
            OutlineNode(
                key="c1",
                title="站址环境",
                points=["写周边环境"],
                keywords=["站址"],
                describes="环境说明",
            )
        ]
    )
    task.outline_confirmed = True
    task.brief = WritingBrief(
        template_id="t",
        template_version="1",
        chapters=[
            ChapterRequirement(key="c1", title="站址环境", points=["写周边环境"], keywords=["站址"]),
        ],
    )
    task.brief_confirmed = True
    task.template_path = str(path / "outline-template.json")
    task.chapters = [
        ChapterState(key="c1", title="站址环境", status=ChapterStatus.NEEDS_HUMAN, human_note="没有达到采纳线的证据")
    ]
    store.save(task)
    return path


def test_focus_chapter_prefers_latest_needs_human_over_older_awaiting(tmp_path: Path):
    path = tmp_path / "tasks" / "w_focus2"
    ensure_task(path)
    store = TaskStore(path)
    task = store.load()
    task.outline = OutlineTree(
        chapters=[
            OutlineNode(key="c6", title="第六章", points=["p"], keywords=["k"]),
            OutlineNode(key="c7", title="第七章", points=["p"], keywords=["k"]),
        ]
    )
    task.outline_confirmed = True
    task.brief = WritingBrief(
        template_id="t",
        template_version="1",
        chapters=[
            ChapterRequirement(key="c6", title="第六章", points=["p"], keywords=["k"]),
            ChapterRequirement(key="c7", title="第七章", points=["p"], keywords=["k"]),
        ],
    )
    task.brief_confirmed = True
    task.chapters = [
        ChapterState(key="c6", title="第六章", status=ChapterStatus.AWAITING_CONFIRM),
        ChapterState(key="c7", title="第七章", status=ChapterStatus.NEEDS_HUMAN, human_note="检查未过"),
    ]
    store.save(task)
    card = _progress_and_confirm_cards(store.load(), pace="step")[0]
    assert card.payload["focus_chapter_key"] == "c7"


def test_focus_chapter_is_latest_awaiting_confirm(tmp_path: Path):
    path = tmp_path / "tasks" / "w_focus"
    ensure_task(path)
    store = TaskStore(path)
    task = store.load()
    task.outline = OutlineTree(
        chapters=[
            OutlineNode(key="c1", title="第一章", points=["p"], keywords=["k"]),
            OutlineNode(key="c2", title="第四章", points=["p"], keywords=["k"]),
            OutlineNode(key="c3", title="第六章", points=["p"], keywords=["k"]),
        ]
    )
    task.outline_confirmed = True
    task.brief = WritingBrief(
        template_id="t",
        template_version="1",
        chapters=[
            ChapterRequirement(key="c1", title="第一章", points=["p"], keywords=["k"]),
            ChapterRequirement(key="c2", title="第四章", points=["p"], keywords=["k"]),
            ChapterRequirement(key="c3", title="第六章", points=["p"], keywords=["k"]),
        ],
    )
    task.brief_confirmed = True
    task.chapters = [
        ChapterState(key="c1", title="第一章", status=ChapterStatus.CONFIRMED),
        ChapterState(key="c2", title="第四章", status=ChapterStatus.AWAITING_CONFIRM),
        ChapterState(key="c3", title="第六章", status=ChapterStatus.AWAITING_CONFIRM),
    ]
    store.save(task)
    card = _progress_and_confirm_cards(store.load(), pace="step")[0]
    assert card.payload["focus_chapter_key"] == "c3"


def test_needs_human_progress_is_editable(tmp_path: Path):
    path = _seed_task(tmp_path)
    task = TaskStore(path).load()
    card = _progress_and_confirm_cards(task)[0]
    row = card.payload["chapters"][0]
    assert row["editable"] is True
    assert row["config"]["keywords"] == ["站址"]
    assert "draft_text" in row


def test_progress_draft_keeps_html(tmp_path: Path):
    from report_writing.domain.models import ChapterDraft, Paragraph

    path = _seed_task(tmp_path)
    store = TaskStore(path)
    task = store.load()
    chapter = task.chapter("c1")
    chapter.status = ChapterStatus.AWAITING_CONFIRM
    chapter.draft = ChapterDraft(
        title="站址环境",
        paragraphs=[
            Paragraph(index=0, text="<p>周边为<strong>山地</strong>。</p>"),
            Paragraph(index=1, text="<table><tr><td>A</td></tr></table>"),
        ],
    )
    store.save(task)
    row = _progress_and_confirm_cards(store.load())[0].payload["chapters"][0]
    assert "<strong>山地</strong>" in row["draft_text"]
    assert "<table>" in row["draft_text"]
    assert row["has_draft"] is True


def test_confirm_needs_human_with_edited_text(tmp_path: Path):
    path = _seed_task(tmp_path)
    updated = confirm_chapter(
        path,
        Path(TaskStore(path).load().template_path),
        path,
        "c1",
        HumanDecision.APPROVE,
        text="人工修订后的正文。",
    )
    chapter = updated.chapter("c1")
    assert chapter.status == ChapterStatus.CONFIRMED
    assert chapter.official_text == "人工修订后的正文。"


def test_confirm_all_chapters_unified(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("REPORT_WRITING_DATABASE", "file")
    path = _seed_task(tmp_path)
    store = TaskStore(path)
    task = store.load()
    task.outline.chapters.append(
        OutlineNode(key="c2", title="竖向布置", points=["p"], keywords=["k"])
    )
    task.brief.chapters.append(ChapterRequirement(key="c2", title="竖向布置", points=["p"], keywords=["k"]))
    task.chapters.append(ChapterState(key="c2", title="竖向布置", status=ChapterStatus.NEEDS_HUMAN))
    store.save(task)

    resp = handle_chat(
        tmp_path,
        ChatRequest(
            action="confirm_all_chapters",
            action_payload={
                "task_id": "w_human",
                "skip_empty": True,
                "chapters": [
                    {"chapter_key": "c1", "text": "站址环境正式稿"},
                    {"chapter_key": "c2", "text": ""},
                ],
            },
        ),
        invoke=lambda s, u: "ok",
    )
    assert "统一确认" in resp.reply
    loaded = TaskStore(path).load()
    assert loaded.chapter("c1").status == ChapterStatus.CONFIRMED
    assert loaded.chapter("c1").official_text == "站址环境正式稿"
    assert loaded.chapter("c2").status == ChapterStatus.SKIPPED
    assert any(c.type == "progress" for c in resp.cards)
    assert not any(c.type == "chapter_confirm" for c in resp.cards)


def test_configure_chapter_via_chat_action(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("REPORT_WRITING_DATABASE", "file")
    path = _seed_task(tmp_path)
    task = TaskStore(path).load()
    _apply_chapter_config(
        task,
        "c1",
        {
            "points_text": "补充气候\n补充水文",
            "keywords_text": "气候，水文",
            "describes": "加强环境描述",
            "aux_prompt": "语气正式",
            "knowledge_folder_ids": ["kb1"],
        },
    )
    TaskStore(path).save(task)
    loaded = TaskStore(path).load()
    node = loaded.outline.chapters[0]
    assert node.points == ["补充气候", "补充水文"]
    assert node.keywords == ["气候", "水文"]
    assert node.describes == "加强环境描述"
    # 章节级知识库目录已废弃，资料改由关联要素解析；配置后 brief 侧应为空
    assert loaded.brief.chapter("c1").knowledge_folder_ids == []
    assert loaded.brief.chapter("c1").points == ["补充气候", "补充水文"]

    resp = handle_chat(
        tmp_path,
        ChatRequest(
            message="",
            session_id="",
            action="configure_chapter",
            action_payload={
                "task_id": "w_human",
                "chapter_key": "c1",
                "points_text": "再改一点",
                "keywords_text": "站址",
            },
        ),
        invoke=lambda s, u: "ok",
    )
    assert "配置我记下了" in resp.reply or "配置已保存" in resp.reply
    assert any(c.type == "progress" for c in resp.cards)


def test_run_chapters_search_only_never_calls_supervisor(tmp_path: Path, monkeypatch):
    from report_writing.services.pipeline import run_chapters

    path = _seed_task(tmp_path)

    def boom(*_args, **_kwargs):
        raise AssertionError("run_supervisor must not run when search_only=True")

    monkeypatch.setattr("report_writing.services.pipeline.run_supervisor", boom)

    def fake_search(chapter, requirement, corpus, model, robot, emit, *, search_only=False):
        assert search_only is True
        emit("search", "running", chapter.key, chapter.title)
        emit("search", "done", chapter.key, chapter.title, {"adopted": 0, "cards": []})
        chapter.status = ChapterStatus.NEEDS_HUMAN

    monkeypatch.setattr("report_writing.agents.supervisor.execute_chapter_search", fake_search)

    task = run_chapters(path, chapter_keys=["c1"], search_only=True)
    new_steps = [s for s in task.steps if s.step == "search"]
    assert len(new_steps) >= 2
    assert not any(s.step in {"write", "check"} for s in task.steps)


def test_handle_chat_retry_search_bypasses_agent(tmp_path: Path, monkeypatch):
    from report_writing.domain.models import ChapterStatus

    _seed_task(tmp_path)
    process_msgs: list[str] = []

    def capture_emit(event: dict) -> None:
        if event.get("type") == "process":
            process_msgs.append(str(event.get("message") or ""))

    def fake_search(chapter, requirement, corpus, model, robot, emit, *, search_only=False):
        assert search_only is True
        emit("search", "running", chapter.key, chapter.title)
        emit("search", "done", chapter.key, chapter.title, {"adopted": 0, "cards": []})
        chapter.status = ChapterStatus.NEEDS_HUMAN

    monkeypatch.setattr("report_writing.agents.supervisor.execute_chapter_search", fake_search)

    class _WrongAgent:
        """若仍走 Agent，invoke 会被调用。"""

        model_name = "wrong"

    resp = handle_chat(
        tmp_path,
        ChatRequest(
            message="",
            session_id="",
            action="retry_search_chapter",
            action_payload={"task_id": "w_human", "chapter_key": "c1"},
        ),
        invoke=lambda _s, _u: (_ for _ in ()).throw(AssertionError("agent must not run")),
        emit=capture_emit,
        model=_WrongAgent(),
    )
    joined = "\n".join(process_msgs)
    assert "仅检索" in joined
    assert "正在重试章节" not in joined
    assert "正在想怎么帮你" not in joined
    assert "检索" in resp.reply or "关键词" in resp.reply
    assert any(e.get("type") == "search_branch_result" for e in resp.events)
    assert resp.cards == []
