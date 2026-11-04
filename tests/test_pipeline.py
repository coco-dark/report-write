import json
from pathlib import Path

from report_writing.agents.check import inspect_draft, run_check
from report_writing.domain.models import ChapterDraft, ChapterRequirement, EvidenceCard, HumanDecision, Paragraph
from report_writing.services.pipeline import confirm_chapter, export_confirmed, run_task


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "examples" / "template.json"
DATASET = ROOT / "examples" / "dataset"


def test_pipeline_rates_evidence_and_waits_for_confirmation(tmp_path: Path):
    task = run_task(TEMPLATE, DATASET, tmp_path / "task", tmp_path / "cache", "002")
    overview = task.chapter("overview")
    assert task.study_runs == 1
    assert overview.status.value == "awaiting_confirm"
    assert any(card.adopted and "12680" in card.excerpt for card in overview.evidence)
    assert all(not card.source_file.startswith("001") for card in overview.evidence)
    assert all(not card.adopted for card in overview.evidence if "附件" in card.source_file)
    assert task.biz_type == "002"
    assert "12680" in overview.draft.paragraphs[3].text
    figures = task.chapter("figures")
    assert figures.status.value == "needs_human"
    assert not (figures.draft and figures.draft.paragraphs)

    cached = run_task(TEMPLATE, DATASET, tmp_path / "task-2", tmp_path / "cache", "002")
    assert cached.brief_from_cache
    assert cached.study_runs == 0
    sample_file = tmp_path / "cache" / "samples" / "eia-narrative" / "1" / "sample.json"
    study_file = tmp_path / "cache" / "samples" / "eia-narrative" / "1" / "study.json"
    assert sample_file.exists()
    assert study_file.exists()

    confirmed = confirm_chapter(
        tmp_path / "task",
        TEMPLATE,
        DATASET,
        "overview",
        HumanDecision.APPROVE,
    )
    exported = export_confirmed(confirmed)
    assert "12680" in exported["chapters"]["overview"]
    assert exported["chapters"]["environment"] == ""
    assert exported["chapters"]["figures"] == ""


def test_check_sends_invented_number_back_to_writer():
    requirement = ChapterRequirement(key="overview", title="项目概况", points=["总投资"])
    evidence = [
        EvidenceCard(
            id="ev-1",
            point="总投资",
            source_file="项目说明书.md",
            location="第1段",
            excerpt="总投资为12680万元。",
            query="总投资",
            relevance=2,
            specificity=2,
            source_score=2,
            consistency=2,
            adopted=True,
        )
    ]
    draft = ChapterDraft(
        title="项目概况",
        paragraphs=[Paragraph(index=0, text="项目概况涉及总投资。总投资为1万元。 [ev-1]", evidence_ids=["ev-1"])],
        uncovered_points=[],
    )
    findings = inspect_draft(requirement, evidence, draft)
    assert any(item["kind"] == "number" for item in findings)
    result = run_check(requirement, evidence, draft, revision_count=0)
    assert result.action.value == "revise"
    assert result.comments[0].index == 0


def test_point_edit_does_not_rewrite_template_cache(tmp_path: Path):
    from report_writing.services.pipeline import update_points
    from report_writing.services.store import BriefCache

    run_task(TEMPLATE, DATASET, tmp_path / "task", tmp_path / "cache", "002")
    update_points(tmp_path / "task", tmp_path / "cache", "overview", ["项目名称"])
    cached = BriefCache(tmp_path / "cache").load("eia-narrative", "1")
    stored = json.loads((tmp_path / "task" / "task.json").read_text(encoding="utf-8"))
    assert cached.chapter("overview").points == ["项目名称", "建设地点", "建设内容", "总投资"]
    assert stored["brief"]["chapters"][0]["points"] == ["项目名称"]
