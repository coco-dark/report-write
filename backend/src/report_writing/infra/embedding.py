"""调用百炼官方嵌入模型。模型名称和维度来自 writing_embedding_model，不读旧的模型表。"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import urllib.request
from collections.abc import Callable

from report_writing.domain.models import EmbeddingModel

_BATCH = 10
_log = logging.getLogger("uvicorn.error")


def embed_texts(
    texts: list[str],
    model: EmbeddingModel,
    api_key: str,
    opener: Callable | None = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> list[list[float]]:
    if not api_key:
        raise RuntimeError("未读到百炼 api-key，无法调用官方嵌入模型")
    send = opener or urllib.request.urlopen
    vectors: list[list[float]] = []
    total = len(texts)
    if total:
        _log.info("开始向量化 %s 段，模型 %s", total, model.model_name)
    for start in range(0, total, _BATCH):
        end = min(start + _BATCH, total)
        _log.info("向量化 %s-%s/%s", start + 1, end, total)
        if on_progress is not None:
            on_progress(end, total)
        batch = [text.strip() or " " for text in texts[start:end]]
        payload = {
            "model": model.model_name,
            "input": batch,
            "dimensions": model.dimensions,
            "encoding_format": "float",
        }
        request = urllib.request.Request(
            model.base_url.rstrip("/") + "/embeddings",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with send(request, timeout=60) as response:
            body = json.loads(response.read().decode("utf-8"))
        ordered = sorted(body["data"], key=lambda item: item["index"])
        vectors.extend(item["embedding"] for item in ordered)
    if total:
        _log.info("向量化完成 %s 段", total)
    return vectors


def embed_query(text: str, model: EmbeddingModel, api_key: str) -> list[float]:
    return embed_texts([text], model, api_key)[0]


def to_literal(vector: list[float]) -> str:
    return "[" + ",".join(f"{value:.6f}" for value in vector) + "]"


def cosine(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or not left:
        return -1.0
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return -1.0
    return dot / (left_norm * right_norm)


def embed_for_search(texts: list[str]) -> list[list[float]]:
    """在线检索走官方 text-embedding-v3。脚本模式用离线向量，测试不调用百炼。"""
    if not texts:
        return []
    from report_writing.infra.config_settings import llm_mode

    if llm_mode() == "scripted":
        return _lexical_vectors(texts)
    from report_writing.infra.bailian import load_bailian_settings

    model = EmbeddingModel(
        code="bailian-official",
        provider="bailian",
        model_name="text-embedding-v3",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        dimensions=1024,
    )
    return embed_texts(texts, model, load_bailian_settings().api_key)


def _lexical_vectors(texts: list[str], dimensions: int = 128) -> list[list[float]]:
    vectors: list[list[float]] = []
    for text in texts:
        values = [0.0] * dimensions
        compact = "".join(text.split())
        grams = [compact[index : index + 2] for index in range(max(len(compact) - 1, 0))] or [compact or " "]
        for gram in grams:
            slot = int(hashlib.md5(gram.encode("utf-8")).hexdigest(), 16) % dimensions
            values[slot] += 1.0
        norm = math.sqrt(sum(value * value for value in values)) or 1.0
        vectors.append([value / norm for value in values])
    return vectors
