import json
import re
from pathlib import Path

from fastapi.testclient import TestClient

from report_writing.api import app
from report_writing.infra.bailian import load_bailian_settings
from report_writing.domain.models import BailianSettings

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "examples" / "template.json"
DATASET = ROOT / "examples" / "dataset"


def test_bailian_settings_from_repo_config():
    from report_writing.infra.bailian import load_bailian_intent_model_name

    settings = load_bailian_settings()
    assert settings.base_url == "https://dashscope.aliyuncs.com/compatible-mode/v1"
    assert settings.completions_path == "/chat/completions"
    assert settings.model_name == "deepseek-v4.1-flash"
    assert load_bailian_intent_model_name() == "deepseek-v4.1-flash"
    assert settings.chat_template_kwargs_enabled is False
    assert settings.request_extras() == {"enable_thinking": False}

    online = BailianSettings(
        api_key="test",
        base_url=settings.base_url,
        model_name="deepseek-v4-flash",
        thinking_level="HIGH",
        chat_template_kwargs_enabled=False,
    )
    assert online.request_extras() == {"enable_thinking": True}


def test_home_page_is_served():
    client = TestClient(app)
    page = client.get("/")
    assert page.status_code == 200
    assert "智能文档协作" in page.text
    script = re.search(r'src="(/assets/[^"]+\.js)"', page.text)
    assert script
    asset = client.get(script.group(1))
    assert asset.status_code == 200
    assert "书写蓝本" in asset.text or "生成要点" in asset.text or "开始分析" in asset.text
    assert "Word" in asset.text or "使用说明" in asset.text


def test_create_task_and_confirm_through_http(tmp_path, monkeypatch):
    monkeypatch.setenv("REPORT_WRITING_HOME", str(tmp_path))
    monkeypatch.setenv("REPORT_WRITING_LLM", "scripted")
    client = TestClient(app)
    created = client.post(
        "/api/writing/tasks",
        json={
            "template_path": str(TEMPLATE),
            "dataset_dir": str(DATASET),
            "task_id": "demo",
            "biz_type": "002",
        },
    )
    assert created.status_code == 200
    body = created.json()
    assert body["status"] == "awaiting_confirm"
    assert body["chapters"][0]["status"] == "awaiting_confirm"

    confirmed = client.post(
        "/api/writing/tasks/demo/chapters/overview/confirm",
        json={"decision": "approve"},
    )
    assert confirmed.status_code == 200
    exported = client.get("/api/writing/tasks/demo/export")
    assert exported.status_code == 200
    assert "12680" in exported.json()["chapters"]["overview"]
    assert exported.json()["chapters"]["figures"] == ""
    sample = client.get("/api/writing/samples/eia-narrative/1")
    study = client.get("/api/writing/samples/eia-narrative/1/study")
    assert sample.status_code == 200
    assert "项目名称为某某工程" in sample.json()["chapters"][0]["paragraphs"][0]
    assert study.status_code == 200
    assert study.json()["brief"]["chapters"][0]["points"][:2] == ["项目名称", "建设地点"]


def _sample_docx() -> bytes:
    from io import BytesIO

    from docx import Document

    document = Document()
    document.add_paragraph("第一章 项目概况")
    document.add_paragraph("项目名称为某某工程。建设地点位于某某地。建设内容为新增主变并写明容量。总投资为若干万元。")
    document.add_paragraph("第二章 环境现状")
    document.add_paragraph("环境空气执行二级标准。声环境执行2类标准，并给出昼间和夜间限值。")
    document.add_paragraph("第三章 附图")
    buffer = BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def test_upload_separates_biz_type_and_stream_reports_each_step(tmp_path, monkeypatch):
    monkeypatch.setenv("REPORT_WRITING_HOME", str(tmp_path))
    monkeypatch.setenv("REPORT_WRITING_LLM", "scripted")
    monkeypatch.setenv("REPORT_WRITING_DATABASE", "file")
    client = TestClient(app)
    uploaded = client.post(
        "/api/writing/uploads",
        data={"task_id": "upload-demo", "biz_type": "002", "template_biz_type": "002"},
        files=[
            ("files", ("说明.txt", "总投资为12680万元。".encode(), "text/plain")),
            ("template", ("样板.docx", _sample_docx(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")),
        ],
    )
    assert uploaded.status_code == 200
    body = uploaded.json()
    assert body["files"] == ["002/说明.txt"]
    assert body["template_biz_type"] == "002"
    assert body["template_source"].endswith("样板.docx")
    stored = json.loads(Path(body["template_path"]).read_text(encoding="utf-8"))
    assert stored["id"] == "002-样板"
    assert stored["biz_type"] == "002"
    assert len(stored["version"]) == 16
    assert stored["chapters"][0]["title"] == "项目概况"
    assert stored["chapters"][0]["points"] == []
    assert "项目名称为某某工程" in stored["chapters"][0]["sample_paragraphs"][0]
    assert (tmp_path / "uploads" / "upload-demo" / "materials" / "002" / "说明.txt").is_file()

    from report_writing.services.pipeline import run_task

    learned = run_task(Path(body["template_path"]), DATASET, tmp_path / "task-a", tmp_path / "cache", "002")
    again = run_task(Path(body["template_path"]), DATASET, tmp_path / "task-b", tmp_path / "cache", "002")
    assert learned.study_runs == 1
    assert again.brief_from_cache is True
    assert again.study_runs == 0
    assert (tmp_path / "cache" / "samples" / stored["id"] / stored["version"] / "study.json").is_file()

    rejected = client.post(
        "/api/writing/uploads",
        data={"task_id": "upload-demo", "biz_type": "002", "template_biz_type": "002"},
        files=[
            ("files", ("说明.txt", "总投资为12680万元。".encode(), "text/plain")),
            ("template", ("template.json", TEMPLATE.read_bytes(), "application/json")),
        ],
    )
    assert rejected.status_code == 400

    mismatched = client.post(
        "/api/writing/uploads",
        data={"task_id": "upload-demo", "biz_type": "002", "template_biz_type": "001"},
        files=[
            ("files", ("说明.txt", "总投资为12680万元。".encode(), "text/plain")),
            ("template", ("template.json", TEMPLATE.read_bytes(), "application/json")),
        ],
    )
    assert mismatched.status_code == 400

    with client.stream(
        "POST",
        "/api/writing/tasks/stream",
        json={
            "template_path": str(TEMPLATE),
            "dataset_dir": str(DATASET),
            "task_id": "stream-demo",
            "biz_type": "002",
        },
    ) as response:
        assert response.status_code == 200
        payload = "".join(response.iter_text())
    events = [json.loads(line[6:]) for line in payload.splitlines() if line.startswith("data: ")]
    pairs = [(item.get("step"), item.get("status")) for item in events if item.get("type") == "step"]
    assert ("study", "running") in pairs
    assert ("study", "done") in pairs
    assert ("search", "done") in pairs
    assert ("write", "done") in pairs
    assert ("check", "done") in pairs
    assert ("search", "skipped") in pairs
    assert events[-1]["type"] == "task"
    assert events[-1]["task"]["steps"]


def _events(payload: str) -> list[dict]:
    return [json.loads(line[6:]) for line in payload.splitlines() if line.startswith("data: ")]


def test_controller_confirms_study_before_search_and_write(tmp_path, monkeypatch):
    monkeypatch.setenv("REPORT_WRITING_HOME", str(tmp_path))
    monkeypatch.setenv("REPORT_WRITING_LLM", "scripted")
    monkeypatch.setenv("REPORT_WRITING_DATABASE", "file")
    client = TestClient(app)
    with client.stream(
        "POST",
        "/api/writing/tasks/analyze/stream",
        data={"task_id": "ctrl", "biz_type": "002"},
        files=[("template", ("样板.docx", _sample_docx(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document"))],
    ) as response:
        assert response.status_code == 200
        analyzed = _events("".join(response.iter_text()))[-1]["task"]
    assert analyzed["brief_confirmed"] is False
    assert analyzed["status"] == "studying"
    assert (tmp_path / "cache" / "samples" / analyzed["template_id"] / analyzed["template_version"] / "study.json").is_file()
    brief = analyzed["brief"]
    confirmed = client.put(
        "/api/writing/tasks/ctrl/study",
        json={
            "voice": "确认后的客观陈述",
            "layout": brief["layout"],
            "paragraph_format": brief["paragraph_format"],
            "chapters": [
                {"key": item["key"], "title": item["title"], "points": item["points"]}
                for item in brief["chapters"]
            ],
        },
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["brief_confirmed"] is True
    assert confirmed.json()["brief"]["voice"] == "确认后的客观陈述"
    blocked = client.post("/api/writing/tasks/ctrl/write/stream")
    assert blocked.status_code == 400
    uploaded = client.post(
        "/api/writing/uploads",
        data={"task_id": "ctrl", "biz_type": "002"},
        files=[("files", ("项目说明书.md", (DATASET / "项目说明书.md").read_bytes(), "text/markdown"))],
    )
    assert uploaded.status_code == 200
    with client.stream("POST", "/api/writing/tasks/ctrl/write/stream") as response:
        assert response.status_code == 200
        written = _events("".join(response.iter_text()))
    pairs = [(item.get("step"), item.get("status")) for item in written if item.get("type") == "step"]
    assert ("search", "done") in pairs
    assert ("write", "done") in pairs
    assert ("check", "done") in pairs
    assert "study" not in {step for step, _status in pairs}
    assert written[-1]["task"]["brief"]["voice"] == "确认后的客观陈述"


def test_studied_template_can_be_selected_again(tmp_path, monkeypatch):
    monkeypatch.setenv("REPORT_WRITING_HOME", str(tmp_path))
    from report_writing.domain.models import ChapterRequirement, SampleChapter, SampleDocument, StudyRecord, WritingBrief
    from report_writing.infra.postgres import open_library

    library = open_library(tmp_path / "cache")
    library.save_sample(
        SampleDocument(
            id="002-示范",
            version="v1",
            title="示范模版",
            chapters=[SampleChapter(key="c1", title="概况", paragraphs=["正文"])],
        )
    )
    library.save_study(
        StudyRecord(
            sample_id="002-示范",
            sample_version="v1",
            brief=WritingBrief(
                template_id="002-示范",
                template_version="v1",
                chapters=[ChapterRequirement(key="c1", title="概况", sequence="1")],
            ),
        )
    )
    client = TestClient(app)
    listed = client.get("/api/writing/studies", params={"biz_type": "002"})
    assert listed.status_code == 200
    assert listed.json() == [
        {
            "sample_id": "002-示范",
            "sample_version": "v1",
            "title": "示范模版",
            "biz_type": "002",
            "chapter_count": 1,
        }
    ]
    reused = client.post(
        "/api/writing/tasks/use-study",
        json={"task_id": "w-reuse", "biz_type": "002", "sample_id": "002-示范", "sample_version": "v1"},
    )
    assert reused.status_code == 200
    body = reused.json()
    assert body["brief_from_cache"] is True
    assert body["brief"]["chapters"][0]["title"] == "概况"
    assert (tmp_path / "uploads" / "w-reuse" / "templates" / "002" / "template.json").is_file()
