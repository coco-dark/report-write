"""写作任务章节：已确认正文同步到 MySQL。"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from report_writing.domain.models import ChapterStatus, WritingTask
from report_writing.infra.postgres import load_database_settings

_log = logging.getLogger(__name__)

_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS writing_task_chapter (
    task_id varchar(128) NOT NULL,
    chapter_key varchar(128) NOT NULL,
    sequence varchar(64) NOT NULL DEFAULT '',
    title varchar(512) NOT NULL DEFAULT '',
    status varchar(64) NOT NULL DEFAULT '',
    official_text mediumtext,
    document json,
    updated_at varchar(32) NOT NULL,
    PRIMARY KEY (task_id, chapter_key),
    KEY writing_task_chapter_task_idx (task_id)
)
"""


def _chapter_meta(task: WritingTask, chapter_key: str) -> tuple[str, str]:
    sequence = ""
    title = chapter_key
    if task.outline is not None:
        node = next((n for n in task.outline.chapters if n.key == chapter_key), None)
        if node is not None:
            sequence = str(getattr(node, "sequence", "") or "")
            title = str(getattr(node, "title", "") or title)
    chapter = next((c for c in (task.chapters or []) if c.key == chapter_key), None)
    if chapter is not None and getattr(chapter, "title", ""):
        title = str(chapter.title or title)
    return sequence, title


def sync_confirmed_chapters(task: WritingTask) -> int:
    """将 status=confirmed 的章节 upsert 到 writing_task_chapter。无库配置时跳过。"""
    settings = load_database_settings()
    if settings is None:
        return 0
    saved = 0
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        from report_writing.infra.postgres import _connect

        with _connect(settings) as connection:
            connection.execute(_TABLE_SQL)
            for chapter in task.chapters or []:
                if chapter.status != ChapterStatus.CONFIRMED:
                    continue
                sequence, title = _chapter_meta(task, chapter.key)
                doc = {
                    "human_note": chapter.human_note or "",
                    "checks": [
                        {
                            "action": str(getattr(c, "action", "")),
                            "reason": getattr(c, "reason", "") or "",
                        }
                        for c in (chapter.checks or [])[-5:]
                    ],
                }
                connection.execute(
                    """
                    INSERT INTO writing_task_chapter
                        (task_id, chapter_key, sequence, title, status, official_text, document, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        sequence = VALUES(sequence),
                        title = VALUES(title),
                        status = VALUES(status),
                        official_text = VALUES(official_text),
                        document = VALUES(document),
                        updated_at = VALUES(updated_at)
                    """,
                    (
                        task.id,
                        chapter.key,
                        sequence,
                        title[:512],
                        ChapterStatus.CONFIRMED.value,
                        chapter.official_text or "",
                        json.dumps(doc, ensure_ascii=False),
                        now,
                    ),
                )
                saved += 1
    except Exception as error:
        _log.warning("同步已确认章节失败 task=%s: %s", task.id, error)
        raise
    return saved
