import io
import json

from report_writing.agents.study import learn_points, run_study
from report_writing.services.corpus import ReportTemplate, TemplateChapter
from report_writing.services.corpus import Corpus
from report_writing.services.documents import chapters_from_lines, read_material


def test_list_items_stay_inside_the_section_and_dotted_numbers_keep_depth():
    chapters = chapters_from_lines(
        [
            "**竖向布置**",
            "站内竖向采用平坡式布置。",
            "1.道路布置",
            "站内道路采用公路型道路。",
            "2.管沟布置",
            "站内管沟布置统筹规划。",
            "3.1.5.5机械伤害",
            "机械伤害需有防护措施。",
        ]
    )
    assert [(item["sequence"], item["level"], item["title"]) for item in chapters] == [
        ("", 1, "竖向布置"),
        ("3.1.5.5", 4, "机械伤害"),
    ]
    assert "1.道路布置" in chapters[0]["sample_paragraphs"]
    assert "2.管沟布置" in chapters[0]["sample_paragraphs"]


def test_dates_and_long_sentences_stay_inside_the_chapter():
    chapters = chapters_from_lines(
        [
            "1.5 编制依据",
            "1.5.1 法律",
            "2021.09 01",
            "中华人民共和国安全生产法",
            "1 充分贯彻落实安全第一的方针，找出本工程中固有或潜在的危险、有害因素，提出科学、合理、可行的安全对策措施建议。",
            "1.1 安全预评价目的",
        ]
    )
    assert [(item["sequence"], item["title"]) for item in chapters] == [
        ("1.5", "编制依据"),
        ("1.5.1", "法律"),
        ("1.1", "安全预评价目的"),
    ]
    assert "2021.09 01" in chapters[1]["sample_paragraphs"]
    assert any(item.startswith("1 充分贯彻落实") for item in chapters[1]["sample_paragraphs"])


def test_nested_headings_keep_sequence_level_and_parent():
    chapters = chapters_from_lines(
        [
            "第一章 项目概况",
            "项目名称为某某工程。",
            "1.1 建设规模",
            "建设内容为新增主变。",
            "1.2 投资",
            "总投资为若干万元。",
        ]
    )
    assert [(item["sequence"], item["level"], item["parent"]) for item in chapters] == [
        ("第一章", 1, ""),
        ("1.1", 2, "ch-1"),
        ("1.2", 2, "ch-1"),
    ]


def test_template_chapters_come_from_rag_html(tmp_path, monkeypatch):
    from report_writing.services.documents import template_document

    monkeypatch.setenv("REPORT_WRITING_PARSE", "rag")

    def fake_parse(path, result_type="markdown"):
        assert result_type == "html"
        return (
            "<h1>第一章 项目概况</h1><p>项目名称为某某工程。</p>"
            "<h2>1.1 建设规模</h2><p>建设内容为新增主变。</p>"
        )

    monkeypatch.setattr("report_writing.infra.rag_parse.parse_full_text", fake_parse)
    source = tmp_path / "样板.docx"
    source.write_bytes(b"unused")
    document = template_document(source, "002", "v1")
    chapters = document["chapters"]
    assert [(item["sequence"], item["title"], item["level"], item["parent"]) for item in chapters] == [
        ("第一章", "项目概况", 1, ""),
        ("1.1", "建设规模", 2, "ch-1"),
    ]
    assert chapters[0]["sample_paragraphs"] == ["项目名称为某某工程。"]


def test_material_uses_rag_markdown(tmp_path, monkeypatch):
    from report_writing.services.documents import read_material

    monkeypatch.setenv("REPORT_WRITING_PARSE", "rag")
    monkeypatch.setattr(
        "report_writing.infra.rag_parse.parse_full_text",
        lambda path: "总投资为1万元。\n\n| 列 | 值 |\n| --- | --- |\n| 容量 | 50 |",
    )
    source = tmp_path / "说明.txt"
    source.write_text("本地原文不使用", encoding="utf-8")
    lines = read_material(source)
    assert lines[0] == "总投资为1万元。"
    assert "容量" in lines[1]


def test_word_headings_become_chapters_without_preset_points():
    chapters = chapters_from_lines(
        [
            "第一章 项目概况",
            "项目名称为某某工程。建设地点位于某某地。建设内容为新增主变并写明容量。总投资为若干万元。",
            "第二章 环境现状",
            "环境空气执行二级标准。声环境执行2类标准，并给出昼间和夜间限值。",
            "第三章 附图",
        ]
    )
    assert [item["sequence"] for item in chapters] == ["第一章", "第二章", "第三章"]
    assert [item["title"] for item in chapters] == ["项目概况", "环境现状", "附图"]
    assert chapters[0]["points"] == []
    assert learn_points(chapters[0]["sample_paragraphs"]) == ["项目名称", "建设地点", "建设内容", "总投资"]
    assert learn_points(chapters[1]["sample_paragraphs"]) == ["环境空气", "声环境"]
    assert chapters[2]["sample_paragraphs"] == []


def test_word_excel_and_image_materials_become_searchable_paragraphs(tmp_path, monkeypatch):
    from docx import Document
    from openpyxl import Workbook

    word = tmp_path / "说明.docx"
    document = Document()
    document.add_paragraph("总投资为12680万元。")
    document.save(word)

    book = Workbook()
    sheet = book.active
    sheet.title = "投资"
    sheet.append(["项目", "总投资为880万元"])
    excel = tmp_path / "表.xlsx"
    book.save(excel)

    image = tmp_path / "现场.jpg"
    image.write_bytes(b"not-a-real-image")

    def fake_open(request, timeout=120):
        sent = json.loads(request.data.decode("utf-8"))
        assert sent["model"] == "qwen-vl-ocr-latest"
        assert sent["messages"][0]["content"][0]["image_url"]["url"].startswith("data:image/jpeg;base64,")
        payload = {"choices": [{"message": {"content": "昼间噪声为55分贝。"}}]}
        return io.BytesIO(json.dumps(payload).encode("utf-8"))

    monkeypatch.setattr("report_writing.services.documents.urllib.request.urlopen", fake_open)
    hits = Corpus(tmp_path, "002").all_hits()
    excerpts = [hit.excerpt for hit in hits]
    assert "总投资为12680万元。" in excerpts
    assert any("总投资为880万元" in item for item in excerpts)
    assert "昼间噪声为55分贝。" in excerpts
    assert read_material(image) == ["昼间噪声为55分贝。"]
    assert (tmp_path / "现场.jpg.paragraphs.json").is_file()


def test_study_finishes_when_the_template_has_many_chapters():
    chapters = [
        TemplateChapter(
            key=f"ch-{index}",
            title=f"题目{index}",
            level=1,
            sample_paragraphs=[f"项目名称为示例{index}。"],
            points=[],
            boundary="",
            depends_on=[],
        )
        for index in range(1, 21)
    ]
    brief = run_study(ReportTemplate(id="long", version="1", title="长样板", chapters=chapters))
    assert len(brief.chapters) == 20
    assert brief.chapters[0].keywords == ["项目名称"]
    assert brief.chapters[0].extracts[0].name == "项目名称"
    assert brief.chapters[0].extracts[0].kind == "文本"
    assert "项目名称" in brief.chapters[0].generate
    assert brief.chapters[0].points == ["项目名称"]


def test_model_study_keeps_chapter_order_and_sequence():
    from report_writing.agents.study import _study_with_model

    class Reply:
        def __init__(self, content: str) -> None:
            self.content = content

    class Model:
        def invoke(self, messages):
            del messages
            return Reply(
                json.dumps(
                    [
                        {"key": "ch-2", "sequence": "乱序", "describes": "后一章", "generate": "写规模", "extracts": [], "keywords": []},
                        {"key": "ch-1", "sequence": "乱序", "describes": "前一章", "generate": "写概况", "extracts": [], "keywords": []},
                    ],
                    ensure_ascii=False,
                )
            )

    template = ReportTemplate(
        id="ordered",
        version="1",
        title="顺序",
        chapters=[
            TemplateChapter(key="ch-1", title="概况", sequence="第一章", level=1, sample_paragraphs=["项目名称为示例。"], points=[], boundary="", depends_on=[]),
            TemplateChapter(key="ch-2", title="规模", sequence="1.2", level=2, parent="ch-1", sample_paragraphs=["建设规模为两台主变。"], points=[], boundary="", depends_on=[]),
        ],
    )
    brief = _study_with_model(template, Model())
    assert [(item.sequence, item.title) for item in brief.chapters] == [("第一章", "概况"), ("1.2", "规模")]
    assert brief.chapters[0].describes == "前一章"
