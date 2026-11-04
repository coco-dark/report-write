"""模板阅读与数据集正文检索。检索只覆盖本次数据集目录。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from report_writing.services.documents import MATERIAL_SUFFIXES, read_material


@dataclass
class TemplateChapter:
    key: str
    title: str
    level: int
    sample_paragraphs: list[str]
    points: list[str]
    boundary: str
    depends_on: list[str]
    sequence: str = ""
    parent: str = ""


@dataclass
class ReportTemplate:
    id: str
    version: str
    title: str
    chapters: list[TemplateChapter]

    def chapter(self, key: str) -> TemplateChapter:
        for item in self.chapters:
            if item.key == key:
                return item
        raise KeyError(key)


@dataclass
class CorpusHit:
    id: str
    source_file: str
    location: str
    excerpt: str
    biz_type: str = ""


_BIZ_TYPE = re.compile(r"^\d{3}$")


def normalize_biz_type(value: str) -> str:
    text = (value or "").strip()
    if not _BIZ_TYPE.fullmatch(text):
        raise ValueError("资料类型须为 001、002 这样的三位业务编号")
    return text


def file_biz_type(relative: str, default: str) -> str:
    parts = Path(relative).parts
    if len(parts) > 1 and _BIZ_TYPE.fullmatch(parts[0]):
        return parts[0]
    return default


def load_template(path: Path) -> ReportTemplate:
    data = json.loads(path.read_text(encoding="utf-8"))
    chapters = [
        TemplateChapter(
            key=item["key"],
            title=item["title"],
            sequence=item.get("sequence") or "",
            level=int(item.get("level") or 1),
            parent=item.get("parent") or "",
            sample_paragraphs=list(item.get("sample_paragraphs") or []),
            points=list(item.get("points") or []),
            boundary=item.get("boundary") or "",
            depends_on=list(item.get("depends_on") or []),
        )
        for item in data.get("chapters") or []
    ]
    return ReportTemplate(
        id=data["id"],
        version=str(data["version"]),
        title=data.get("title") or data["id"],
        chapters=chapters,
    )


def open_corpus(dataset_dir: Path, biz_type: str) -> Corpus:
    from report_writing.infra.rag_parse import use_remote_parse

    return Corpus(dataset_dir, biz_type, prepare=not use_remote_parse())


def empty_corpus(biz_type: str = "002") -> Corpus:
    """无本地上传资料时的空语料；检索走知识库目录。"""
    return Corpus(Path("."), biz_type, prepare=False)


def load_task_corpus(dataset_dir: Path | str | None, biz_type: str, on_progress=None) -> Corpus:
    """优先用任务本地上传目录；没有则返回空语料（章节检索依赖知识库）。"""
    from report_writing.infra.postgres import attach_materials

    material_type = normalize_biz_type(biz_type or "002")
    root = Path(dataset_dir) if dataset_dir else None
    if root is None or not root.is_dir():
        return empty_corpus(material_type)
    return attach_materials(root, open_corpus(root, material_type), on_progress=on_progress)


def _filter_source_files(hits: list[CorpusHit], source_files: list[str] | None) -> list[CorpusHit]:
    if not source_files:
        return hits
    allowed_full = {str(item).strip().replace("\\", "/") for item in source_files if str(item).strip()}
    allowed_names = {Path(item).name for item in allowed_full}
    filtered = []
    for hit in hits:
        relative = str(hit.source_file or "").replace("\\", "/")
        if relative in allowed_full or Path(relative).name in allowed_names:
            filtered.append(hit)
    return filtered


class Corpus:
    """把数据集里的 Word、PDF、Excel、图片和文本按段落切成可检索片段。"""

    def __init__(self, root: Path, biz_type: str, prepare: bool = True) -> None:
        self.root = root
        self._biz_type = normalize_biz_type(biz_type)
        self._chunks: list[CorpusHit] = []
        self._index = None
        self._dataset_key = ""
        if prepare:
            self._load()

    def _load(self) -> None:
        files = sorted(
            path
            for path in self.root.rglob("*")
            if path.is_file() and path.suffix.lower() in MATERIAL_SUFFIXES
        )
        number = 1
        for path in files:
            paragraphs = read_material(path)
            relative = str(path.relative_to(self.root))
            for index, paragraph in enumerate(paragraphs, start=1):
                self._chunks.append(
                    CorpusHit(
                        id=f"hit-{number}",
                        source_file=relative,
                        location=f"第{index}段",
                        excerpt=paragraph,
                        biz_type=file_biz_type(relative, self._biz_type),
                    )
                )
                number += 1

    def use_vector_index(self, index, dataset_key: str) -> None:
        self._index = index
        self._dataset_key = dataset_key

    def search(self, query: str, limit: int = 8, source_files: list[str] | None = None) -> list[CorpusHit]:
        fetch = limit * 4 if source_files else limit
        if self._index is not None:
            hits = self._index.search(self._dataset_key, self._biz_type, query, fetch)
        else:
            tokens = [token for token in re.split(r"[\s，。；、：:]+", query) if len(token) >= 2]
            ranked: list[tuple[int, CorpusHit]] = []
            for hit in self._chunks:
                if hit.biz_type != self._biz_type:
                    continue
                score = sum(1 for token in tokens if token in hit.excerpt)
                if score > 0:
                    ranked.append((score, hit))
            ranked.sort(key=lambda item: item[0], reverse=True)
            hits = [hit for _, hit in ranked[:fetch]]
        return _filter_source_files(hits, source_files)[:limit]

    def all_hits(self) -> list[CorpusHit]:
        return list(self._chunks)
