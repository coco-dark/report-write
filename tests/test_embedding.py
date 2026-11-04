import json

from report_writing.infra.embedding import embed_texts
from report_writing.domain.models import EmbeddingModel


def test_official_embedding_request_uses_the_new_model_row():
    model = EmbeddingModel(
        code="bailian-official",
        provider="bailian",
        model_name="text-embedding-v3",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        dimensions=1024,
    )
    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def read(self):
            return json.dumps(
                {"data": [{"index": 0, "embedding": [0.2, 0.8]}]}
            ).encode("utf-8")

    def opener(request, timeout):
        captured["url"] = request.full_url
        captured["body"] = json.loads(request.data.decode("utf-8"))
        captured["timeout"] = timeout
        return Response()

    vectors = embed_texts(["总投资为12680万元。"], model, "test-key", opener)
    assert captured["url"].endswith("/embeddings")
    assert captured["body"]["model"] == "text-embedding-v3"
    assert captured["body"]["dimensions"] == 1024
    assert vectors == [[0.2, 0.8]]


def test_vector_search_keeps_the_current_biz_type():
    from report_writing.services.corpus import CorpusHit
    from report_writing.infra.postgres import MemoryVectorIndex

    index = MemoryVectorIndex(
        [
            CorpusHit("1", "001/说明.txt", "第1段", "总投资为1万元。", "001"),
            CorpusHit("2", "项目说明书.md", "第4段", "总投资为12680万元。", "002"),
            CorpusHit("3", "项目说明书.md", "第5段", "环境空气质量满足二级标准。", "002"),
        ]
    )
    hits = index.search("dataset", "002", "总投资", limit=2)
    assert hits
    assert hits[0].excerpt == "总投资为12680万元。"
    assert all(hit.biz_type == "002" for hit in hits)
