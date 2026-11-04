"""要素 file / file_slot 检索优先走 RAG hybridSearch。"""

from __future__ import annotations

from pathlib import Path

import pytest


def test_search_in_slot_file_uses_rag_when_available(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    path = tmp_path / "slot.txt"
    path.write_text("无关段落\n\n建设规模五百兆瓦\n", encoding="utf-8")

    def fake_rag(p: Path, query: str, *, limit: int = 5, biz_type: str = "002"):
        assert p == path
        assert query == "建设规模"
        return ["RAG 命中：500MW"]

    monkeypatch.setattr(
        "report_writing.services.rag_materials.search_file_rag_excerpts",
        fake_rag,
    )
    from report_writing.services.materials_pack import search_in_slot_file

    hits = search_in_slot_file(path, "建设规模", limit=2)
    assert hits == ["RAG 命中：500MW"]


def test_search_parsed_files_uses_rag_when_available(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from report_writing.services import element_files as ef

    src = tmp_path / "report.txt"
    src.write_text("正文", encoding="utf-8")

    def fake_rag(p: Path, query: str, *, limit: int = 5, biz_type: str = "002"):
        assert p == src
        return [f"rag:{query}"]

    monkeypatch.setattr(ef, "read_parsed_text", lambda _home, _fid: "已解析正文足够长" * 3)
    monkeypatch.setattr(ef, "resolve_path", lambda _home, _attached: src)
    monkeypatch.setattr(
        "report_writing.services.rag_materials.search_file_rag_excerpts",
        fake_rag,
    )

    text, unparsed = ef.search_parsed_files(
        tmp_path,
        [
            {
                "file_id": "ef-abc",
                "file_name": "report.txt",
                "parse_status": "parsed",
            }
        ],
        "查询词",
    )
    assert not unparsed
    assert "rag:查询词" in text
