"""JSON 文件中的任务与模板书写要求缓存。"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from report_writing.domain.models import SampleDocument, StudyRecord, WritingBrief, WritingTask
from report_writing.infra.postgres import open_library
from report_writing.services.task_ids import is_blueprint_task_id, is_writing_task_id

_log = logging.getLogger(__name__)


class TaskNotFoundError(FileNotFoundError):
    """任务在目录库中不存在。"""


def _utc_saved_at() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _stamp_saved_at(data: dict) -> dict:
    payload = dict(data or {})
    payload["_saved_at"] = _utc_saved_at()
    return payload


def _read_json_file(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return raw if isinstance(raw, dict) else None


def _saved_at_of(data: dict | None) -> str:
    if not isinstance(data, dict):
        return ""
    return str(data.get("_saved_at") or "").strip()


class TaskStore:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.path = directory / "task.json"

    def save(self, task: WritingTask) -> None:
        tid = str(task.id or self.directory.name or "").strip()
        if not tid:
            return
        if is_blueprint_task_id(tid):
            from report_writing.services.blueprint_catalog import save_blueprint_task

            save_blueprint_task(task)
            self.directory.mkdir(parents=True, exist_ok=True)
            if self.path.is_file():
                self.path.unlink(missing_ok=True)
            # 蓝本确认目录等也会改 outline_confirmed，必须失效小窗 doc-outline 缓存
            self._invalidate_doc_outline(task, tid)
            return
        self.directory.mkdir(parents=True, exist_ok=True)
        # 写作副本以 MySQL 为准：先写库再镜像磁盘，避免对话读库、流水线读盘各拿一份
        disk_payload = _stamp_saved_at(task.to_dict())
        saved_at = str(disk_payload.get("_saved_at") or "")
        if is_writing_task_id(tid):
            try:
                from report_writing.services.writing_task_catalog import save_writing_task

                save_writing_task(task, saved_at=saved_at)
            except Exception as error:
                _log.warning("writing task MySQL save failed for %s: %s", tid, error)
                try:
                    from report_writing.services.writing_task_catalog import clear_writing_task_document

                    clear_writing_task_document(tid)
                except Exception:
                    pass
                raise RuntimeError(
                    "写作任务未能写入数据库，已中止保存，请稍后重试"
                ) from error
        self.path.write_text(json.dumps(disk_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        # 非蓝本写后统一失效（catalog 可能 no-op；重复 invalidate 无害）
        self._invalidate_doc_outline(task, tid)

    @staticmethod
    def _invalidate_doc_outline(task: WritingTask, tid: str) -> None:
        try:
            from report_writing.services.doc_outline_cache import invalidate_doc_outline_cache

            invalidate_doc_outline_cache(tid)
            source_bp = str(getattr(task, "source_blueprint_id", "") or "").strip()
            if source_bp:
                invalidate_doc_outline_cache(source_bp)
        except Exception:
            pass

    def load(self) -> WritingTask:
        tid = str(self.directory.name or "").strip()
        if is_writing_task_id(tid):
            mysql_raw = None
            try:
                from report_writing.services.writing_task_catalog import load_writing_task_document

                mysql_raw = load_writing_task_document(tid)
            except Exception:
                mysql_raw = None
            if mysql_raw is not None:
                return WritingTask.from_dict(mysql_raw)
            disk_raw = _read_json_file(self.path)
            if disk_raw is not None:
                return WritingTask.from_dict(disk_raw)
            raise TaskNotFoundError(tid)
        if is_blueprint_task_id(tid):
            from report_writing.services.blueprint_catalog import load_blueprint_task

            task = load_blueprint_task(tid)
            if task is not None:
                return task
            if self.path.is_file():
                legacy = WritingTask.from_dict(json.loads(self.path.read_text(encoding="utf-8")))
                self.save(legacy)
                return legacy
            raise TaskNotFoundError(tid)
        if self.path.is_file():
            return WritingTask.from_dict(json.loads(self.path.read_text(encoding="utf-8")))
        raise TaskNotFoundError(tid or str(self.directory))


class BriefCache:
    """兼容旧调用。书写要求实际写在样板库的 study.json。"""

    def __init__(self, directory: Path) -> None:
        self.library = open_library(directory)

    def load(self, template_id: str, version: str) -> WritingBrief | None:
        record = self.library.load_study(template_id, version)
        return record.brief if record else None

    def save(self, brief: WritingBrief) -> None:
        self.library.save_study(
            StudyRecord(sample_id=brief.template_id, sample_version=brief.template_version, brief=brief)
        )


class SampleLibrary:
    """持久化样板正文，以及从该样板学到的书写要求。"""

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def sample_path(self, sample_id: str, version: str) -> Path:
        return self.directory / "samples" / sample_id / version / "sample.json"

    def study_path(self, sample_id: str, version: str) -> Path:
        return self.directory / "samples" / sample_id / version / "study.json"

    def save_sample(self, sample: SampleDocument) -> None:
        path = self.sample_path(sample.id, sample.version)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(sample.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    def load_sample(self, sample_id: str, version: str) -> SampleDocument | None:
        path = self.sample_path(sample_id, version)
        if not path.exists():
            return None
        return SampleDocument.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def save_study(self, record: StudyRecord) -> None:
        path = self.study_path(record.sample_id, record.sample_version)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    def load_study(self, sample_id: str, version: str) -> StudyRecord | None:
        path = self.study_path(sample_id, version)
        if not path.exists():
            return None
        return StudyRecord.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def list_studies(self, biz_type: str) -> list:
        from report_writing.domain.models import StudiedTemplate

        root = self.directory / "samples"
        if not root.is_dir():
            return []
        prefix = f"{biz_type}-"
        found = []
        for path in sorted(root.glob("*/*/study.json")):
            sample_id = path.parent.parent.name
            version = path.parent.name
            if not sample_id.startswith(prefix):
                continue
            record = StudyRecord.from_dict(json.loads(path.read_text(encoding="utf-8")))
            sample = self.load_sample(sample_id, version)
            found.append(
                StudiedTemplate(
                    sample_id=sample_id,
                    sample_version=version,
                    title=(sample.title if sample and sample.title else sample_id),
                    biz_type=biz_type,
                    chapter_count=len(record.brief.chapters),
                )
            )
        return found
