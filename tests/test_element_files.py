"""要素「文件」类型：上传不解析；手动解析后可检索。"""

from __future__ import annotations

from pathlib import Path

from report_writing.domain.elements import resolve_elements_for_chapter
from report_writing.domain.models import ChapterRequirement, ElementAttachedFile, ElementNode
from report_writing.services import element_files as ef


def test_upload_pending_until_manual_parse(tmp_path: Path, monkeypatch):
    attached = ef.upload_file(tmp_path, filename="概况.txt", data="建设规模三万平方米。".encode("utf-8"))
    assert attached.parse_status == "pending"
    assert ef.read_parsed_text(tmp_path, attached.file_id) == ""

    def fake_parse(path, result_type="markdown"):
        return Path(path).read_text(encoding="utf-8")

    monkeypatch.setattr("report_writing.infra.rag_parse.parse_full_text", fake_parse)
    parsed = ef.parse_file(tmp_path, attached.file_id)
    assert parsed.parse_status == "parsed"
    assert "三万平方米" in ef.read_parsed_text(tmp_path, attached.file_id)


def test_resolve_file_kind_uses_parsed_attachments(tmp_path: Path, monkeypatch):
    attached = ef.upload_file(tmp_path, filename="a.txt", data="消防验收结论合格。".encode("utf-8"))

    def fake_parse(path, result_type="markdown"):
        return Path(path).read_text(encoding="utf-8")

    monkeypatch.setattr("report_writing.infra.rag_parse.parse_full_text", fake_parse)
    ef.parse_file(tmp_path, attached.file_id)
    attached = ef.get_file(tmp_path, attached.file_id)

    elements = [
        ElementNode(
            id="e1",
            name="消防",
            kind="file",
            query="消防验收",
            files=[attached],
        )
    ]
    req = ChapterRequirement(key="ch1", title="一", element_ids=["e1"])
    resolve_elements_for_chapter(req, elements, None, home=tmp_path)
    assert "消防验收" in (req.element_results[0]["value"] or "")


def test_resolve_unparsed_file_returns_empty(tmp_path: Path):
    attached = ef.upload_file(tmp_path, filename="b.txt", data="尚未解析的正文".encode("utf-8"))
    elements = [
        ElementNode(
            id="e1",
            name="资料",
            kind="file",
            query="正文",
            files=[ElementAttachedFile.model_validate(attached.model_dump())],
        )
    ]
    req = ChapterRequirement(key="ch1", title="一", element_ids=["e1"])
    resolve_elements_for_chapter(req, elements, None, home=tmp_path)
    assert req.element_results[0]["value"] == ""
