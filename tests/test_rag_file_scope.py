"""单文件 RAG：manifest 登记 taskId/bizId，检索时限定范围。"""

from __future__ import annotations

from pathlib import Path

import pytest


def test_search_scope_from_record_reads_aliases():
    from report_writing.services.rag_materials import search_scope_from_record

    biz, tasks = search_scope_from_record(
        {
            "rag_biz_id": "server-biz",
            "rag_task_id": 42,
            "search_biz_ids": ["server-biz"],
            "search_task_ids": [42],
        }
    )
    assert biz == ["server-biz"]
    assert tasks == [42]


def test_search_file_rag_drops_foreign_task_hits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    path = tmp_path / "doc.txt"
    path.write_text("正文", encoding="utf-8")

    record = {
        "biz_id": "b-local",
        "task_id": 99,
        "rag_biz_id": "b-local",
        "rag_task_id": 99,
        "search_biz_ids": ["b-local"],
        "search_task_ids": [99],
    }

    def fake_ensure(p: Path, *, biz_type: str = "002"):
        assert p == path
        return record

    def fake_search(query: str, biz_ids: list[str], task_ids: list[int], limit: int):
        assert 99 in task_ids
        return {
            "records": [
                {"taskId": 99, "segement": {"content": "本文件命中"}},
                {"taskId": 1, "segement": {"content": "其它文件"}},
            ]
        }

    monkeypatch.setattr(
        "report_writing.services.rag_materials.ensure_file_vectorized",
        fake_ensure,
    )
    monkeypatch.setattr(
        "report_writing.services.rag_materials.search_materials",
        fake_search,
    )
    from report_writing.services.rag_materials import search_file_rag_excerpts

    hits = search_file_rag_excerpts(path, "查询", limit=3)
    assert hits == ["本文件命中"]
