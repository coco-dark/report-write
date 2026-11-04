from pathlib import Path

import pytest

from report_writing.services.rag_materials import (
    RagMaterialIndex,
    _RATE_LIMIT_HINT,
    _file_url,
    _is_rate_limit,
    _request_json,
)


def test_file_url_reads_nested_upload_data() -> None:
    body = {
        "success": True,
        "data": {"url": "https://images.smartqa.cn/file/images/a.txt", "fileName": "a.txt"},
    }
    assert _file_url(body) == "https://images.smartqa.cn/file/images/a.txt"


def test_search_queries_every_material_task(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "a.txt").write_text("甲", encoding="utf-8")
    (tmp_path / "b.txt").write_text("乙", encoding="utf-8")
    created = iter([11, 22])
    uploads: list[str] = []

    def upload(path: Path) -> str:
        uploads.append(path.name)
        return f"https://files.example/{path.name}"

    def create(url: str, biz_id: str) -> dict:
        return {"id": next(created), "done": True, "url": url, "biz": biz_id}

    captured: dict = {}

    def request(method: str, url: str, payload=None, headers=None):
        captured["method"] = method
        captured["url"] = url
        captured["payload"] = payload
        return {
            "records": [
                {"taskId": 11, "segement": {"id": 3, "content": "甲的原文"}},
                {"taskId": 22, "segement": {"id": 8, "content": "乙的原文"}},
            ]
        }

    monkeypatch.setattr("report_writing.services.rag_materials.upload_material", upload)
    monkeypatch.setattr("report_writing.services.rag_materials.create_vector_task", create)
    monkeypatch.setattr("report_writing.services.rag_materials._request_json", request)

    index = RagMaterialIndex(tmp_path, "002")
    index.prepare()
    hits = index.search(str(tmp_path), "002", "安全措施", 8)

    assert uploads == ["a.txt", "b.txt"]
    assert captured["payload"]["taskIdIn"] == [11, 22]
    assert captured["payload"]["bizIdIn"]
    assert [hit.excerpt for hit in hits] == ["甲的原文", "乙的原文"]
    assert hits[0].source_file == "a.txt"
    assert hits[1].location == "片段 8"

    def refuse(path: Path) -> str:
        raise AssertionError("已有任务不应再次上传")

    monkeypatch.setattr("report_writing.services.rag_materials.upload_material", refuse)
    monkeypatch.setattr("report_writing.services.rag_materials._task_done", lambda task_id: True)
    again = RagMaterialIndex(tmp_path, "002")
    again.prepare()
    assert [item["task_id"] for item in again._tasks] == [11, 22]


def test_is_rate_limit_detects_aliyun_payload():
    detail = '搜索服务异常: 向量化处理失败: HTTP 429 - {"error":{"code":"limit_requests"}}'
    assert _is_rate_limit(detail) is True
    assert _is_rate_limit("ok") is False
    assert _is_rate_limit("", status=429) is True


def test_request_json_retries_rate_limit(monkeypatch):
    calls = {"n": 0}

    def once(method, url, payload=None, headers=None):
        calls["n"] += 1
        if calls["n"] < 3:
            raise ValueError('RAG 请求失败：HTTP 429 - {"code":"limit_requests"}')
        return {"records": []}

    sleeps: list[float] = []
    monkeypatch.setattr("report_writing.services.rag_materials._request_json_once", once)
    monkeypatch.setattr("report_writing.services.rag_materials.time.sleep", sleeps.append)

    body = _request_json("POST", "http://example/search", {"query": "x"})
    assert body == {"records": []}
    assert calls["n"] == 3
    assert sleeps == [2.0, 4.0]


def test_request_json_rate_limit_exhausted(monkeypatch):
    monkeypatch.setattr(
        "report_writing.services.rag_materials._request_json_once",
        lambda *a, **k: (_ for _ in ()).throw(ValueError("RAG 请求失败：limit_requests")),
    )
    monkeypatch.setattr("report_writing.services.rag_materials.time.sleep", lambda *_: None)
    with pytest.raises(ValueError, match="检索暂时限流"):
        _request_json("POST", "http://example/search", {})
    assert _RATE_LIMIT_HINT
