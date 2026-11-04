"""线上 MySQL 中的样板、书写要求和上传资料向量。连接读 config/application.yml。"""

from __future__ import annotations

import json
import logging
import os
import queue
import re
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs

import yaml

from report_writing.services.corpus import Corpus, CorpusHit
from report_writing.infra.embedding import cosine, embed_for_search, embed_query, embed_texts
from report_writing.domain.models import DatabaseSettings, EmbeddingModel, SampleDocument, StudyRecord

_log = logging.getLogger(__name__)
_JDBC = re.compile(r"jdbc:mysql://([^/:?]+)(?::(\d+))?/([^?]+)")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS writing_sample (
    sample_id varchar(128) NOT NULL,
    sample_version varchar(64) NOT NULL,
    title varchar(512) NOT NULL,
    document json NOT NULL,
    PRIMARY KEY (sample_id, sample_version)
);
CREATE TABLE IF NOT EXISTS writing_study (
    sample_id varchar(128) NOT NULL,
    sample_version varchar(64) NOT NULL,
    brief json NOT NULL,
    PRIMARY KEY (sample_id, sample_version)
);
CREATE TABLE IF NOT EXISTS writing_embedding_model (
    code varchar(64) PRIMARY KEY,
    provider varchar(64) NOT NULL,
    model_name varchar(128) NOT NULL,
    base_url varchar(512) NOT NULL,
    dimensions integer NOT NULL,
    active tinyint(1) NOT NULL DEFAULT 0
);
INSERT IGNORE INTO writing_embedding_model (code, provider, model_name, base_url, dimensions, active)
VALUES (
    'bailian-official',
    'bailian',
    'text-embedding-v3',
    'https://dashscope.aliyuncs.com/compatible-mode/v1',
    1024,
    1
);
DROP TABLE IF EXISTS writing_material_chunk;
CREATE TABLE IF NOT EXISTS writing_material_vector (
    id bigint NOT NULL AUTO_INCREMENT PRIMARY KEY,
    dataset_key varchar(1024) NOT NULL,
    source_file varchar(512) NOT NULL,
    location varchar(256) NOT NULL,
    excerpt text NOT NULL,
    biz_type varchar(8) NOT NULL,
    model_code varchar(64) NOT NULL,
    embedding json NOT NULL,
    KEY writing_material_vector_dataset_idx (dataset_key(191)),
    KEY writing_material_vector_type_idx (dataset_key(191), biz_type)
);
CREATE TABLE IF NOT EXISTS writing_element_node (
    task_id varchar(128) NOT NULL,
    element_id varchar(64) NOT NULL,
    name varchar(256) NOT NULL,
    kind varchar(32) NOT NULL,
    config json NOT NULL,
    result text,
    PRIMARY KEY (task_id, element_id),
    KEY writing_element_task_idx (task_id)
);
CREATE TABLE IF NOT EXISTS writing_knowledge_folder (
    id varchar(64) NOT NULL PRIMARY KEY,
    parent_id varchar(64) NOT NULL DEFAULT '',
    name varchar(256) NOT NULL,
    sort_order integer NOT NULL DEFAULT 0,
    created_at varchar(32) NOT NULL
);
CREATE TABLE IF NOT EXISTS writing_knowledge_file (
    id varchar(64) NOT NULL PRIMARY KEY,
    folder_id varchar(64) NOT NULL,
    original_name varchar(512) NOT NULL,
    local_path varchar(1024) NOT NULL,
    file_url varchar(1024) NOT NULL DEFAULT '',
    file_size bigint NOT NULL DEFAULT 0,
    file_type varchar(32) NOT NULL DEFAULT '',
    rag_task_id integer NOT NULL DEFAULT 0,
    rag_biz_id varchar(128) NOT NULL DEFAULT '',
    rag_status varchar(32) NOT NULL DEFAULT 'PENDING',
    rag_error varchar(1024) NOT NULL DEFAULT '',
    created_at varchar(32) NOT NULL,
    KEY writing_knowledge_file_folder_idx (folder_id)
);
CREATE TABLE IF NOT EXISTS writing_chat_session (
    id varchar(64) NOT NULL PRIMARY KEY,
    user_id varchar(64) NOT NULL DEFAULT 'default',
    robot_id varchar(64) NOT NULL DEFAULT '',
    phase varchar(64) NOT NULL DEFAULT 'intent',
    document json NOT NULL,
    title varchar(80) NOT NULL DEFAULT '',
    preview varchar(160) NOT NULL DEFAULT '',
    message_count int NOT NULL DEFAULT 0,
    created_at varchar(32) NOT NULL,
    updated_at varchar(32) NOT NULL,
    KEY writing_chat_session_user_idx (user_id),
    KEY writing_chat_session_robot_idx (robot_id),
    KEY writing_chat_session_updated_idx (updated_at)
);
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
);
CREATE TABLE IF NOT EXISTS writing_robot (
    id varchar(64) NOT NULL PRIMARY KEY,
    name varchar(255) NOT NULL DEFAULT '',
    description text,
    created_at varchar(32) NOT NULL,
    updated_at varchar(32) NOT NULL,
    KEY writing_robot_updated_idx (updated_at)
);
CREATE TABLE IF NOT EXISTS writing_robot_document (
    robot_id varchar(64) NOT NULL PRIMARY KEY,
    document json NOT NULL
);
CREATE TABLE IF NOT EXISTS writing_render_template (
    id varchar(64) NOT NULL PRIMARY KEY,
    name varchar(255) NOT NULL DEFAULT '',
    format varchar(32) NOT NULL DEFAULT 'DOCX',
    label_updated varchar(64) NOT NULL DEFAULT '',
    updated_at varchar(32) NOT NULL,
    KEY writing_render_template_updated_idx (updated_at)
);
CREATE TABLE IF NOT EXISTS writing_render_template_document (
    template_id varchar(64) NOT NULL PRIMARY KEY,
    document json NOT NULL
);
CREATE TABLE IF NOT EXISTS writing_blueprint (
    id varchar(128) NOT NULL PRIMARY KEY,
    title varchar(512) NOT NULL DEFAULT '',
    category varchar(128) NOT NULL DEFAULT '',
    tags json,
    description text,
    enabled tinyint(1) NOT NULL DEFAULT 1,
    outline_confirmed tinyint(1) NOT NULL DEFAULT 0,
    chapter_count int NOT NULL DEFAULT 0,
    element_count int NOT NULL DEFAULT 0,
    updated_at varchar(32) NOT NULL,
    KEY writing_blueprint_updated_idx (updated_at)
);
CREATE TABLE IF NOT EXISTS writing_blueprint_document (
    blueprint_id varchar(128) NOT NULL PRIMARY KEY,
    document json NOT NULL
);
"""

_STATEMENTS = [statement.strip() for statement in _SCHEMA.split(";") if statement.strip()]
_ready = False

# 简易连接池：避免每次请求握手新建 TCP
_POOL_MAX = max(2, min(32, int(os.environ.get("REPORT_WRITING_DB_POOL", "8") or 8)))
_pool: queue.Queue | None = None
_pool_key: tuple | None = None
_pool_lock = threading.Lock()
_pool_created = 0

# MySQL 可用性探测缓存（避免 list/get 每次 SELECT 1）
_mysql_probe: tuple[float, bool] | None = None
_MYSQL_OK_TTL_S = 30.0
_MYSQL_FAIL_TTL_S = 5.0


def load_database_settings() -> DatabaseSettings | None:
    from report_writing.infra.config_settings import database_jdbc_url_override, database_mode

    mode = database_mode()
    if mode in {"file", "off", "none"}:
        return None
    override = database_jdbc_url_override()
    if override:
        return _settings_from_jdbc(override, user="", password="")
    return _settings_from_config()


def open_library(directory: Path):
    from report_writing.services.store import SampleLibrary

    settings = load_database_settings()
    file_library = SampleLibrary(directory)
    if settings is None:
        return file_library
    return _DurableLibrary(file_library, WritingLibrary(settings))


class _DurableLibrary:
    """样板和学习结果同时写入线上库和本地文件。读取时线上库优先。"""

    def __init__(self, file_library, database) -> None:
        self.file_library = file_library
        self.database = database

    def save_sample(self, sample: SampleDocument) -> None:
        self.database.save_sample(sample)
        self.file_library.save_sample(sample)

    def load_sample(self, sample_id: str, version: str) -> SampleDocument | None:
        found = self.database.load_sample(sample_id, version)
        return found if found is not None else self.file_library.load_sample(sample_id, version)

    def save_study(self, record: StudyRecord) -> None:
        self.database.save_study(record)
        self.file_library.save_study(record)

    def load_study(self, sample_id: str, version: str) -> StudyRecord | None:
        found = self.database.load_study(sample_id, version)
        return found if found is not None else self.file_library.load_study(sample_id, version)

    def list_studies(self, biz_type: str) -> list:
        merged = {
            (item.sample_id, item.sample_version): item
            for item in self.file_library.list_studies(biz_type)
        }
        for item in self.database.list_studies(biz_type):
            merged[(item.sample_id, item.sample_version)] = item
        return sorted(merged.values(), key=lambda item: item.title)


class WritingLibrary:
    def __init__(self, settings: DatabaseSettings) -> None:
        self.settings = settings

    def save_sample(self, sample: SampleDocument) -> None:
        with _connect(self.settings) as connection:
            connection.execute(
                """
                INSERT INTO writing_sample (sample_id, sample_version, title, document)
                VALUES (%s, %s, %s, CAST(%s AS JSON)) AS incoming
                ON DUPLICATE KEY UPDATE title = incoming.title, document = incoming.document
                """,
                (sample.id, sample.version, sample.title, json.dumps(sample.to_dict(), ensure_ascii=False)),
            )

    def load_sample(self, sample_id: str, version: str) -> SampleDocument | None:
        with _connect(self.settings) as connection:
            row = connection.execute(
                "SELECT document FROM writing_sample WHERE sample_id = %s AND sample_version = %s",
                (sample_id, version),
            ).fetchone()
        if row is None:
            return None
        return SampleDocument.from_dict(row[0])

    def save_study(self, record: StudyRecord) -> None:
        with _connect(self.settings) as connection:
            connection.execute(
                """
                INSERT INTO writing_study (sample_id, sample_version, brief)
                VALUES (%s, %s, CAST(%s AS JSON)) AS incoming
                ON DUPLICATE KEY UPDATE brief = incoming.brief
                """,
                (
                    record.sample_id,
                    record.sample_version,
                    json.dumps(record.brief.to_dict(), ensure_ascii=False),
                ),
            )

    def load_study(self, sample_id: str, version: str) -> StudyRecord | None:
        with _connect(self.settings) as connection:
            row = connection.execute(
                "SELECT brief FROM writing_study WHERE sample_id = %s AND sample_version = %s",
                (sample_id, version),
            ).fetchone()
        if row is None:
            return None
        return StudyRecord(
            sample_id=sample_id,
            sample_version=version,
            brief=record_brief(row[0]),
        )

    def list_studies(self, biz_type: str) -> list:
        from report_writing.domain.models import StudiedTemplate

        prefix = f"{biz_type}-%"
        with _connect(self.settings) as connection:
            rows = connection.execute(
                """
                SELECT study.sample_id, study.sample_version, sample.title,
                       JSON_LENGTH(study.brief, '$.chapters')
                FROM writing_study AS study
                LEFT JOIN writing_sample AS sample
                  ON sample.sample_id = study.sample_id
                 AND sample.sample_version = study.sample_version
                WHERE study.sample_id LIKE %s
                """,
                (prefix,),
            ).fetchall()
        found = []
        for sample_id, version, title, chapter_count in rows:
            found.append(
                StudiedTemplate(
                    sample_id=sample_id,
                    sample_version=version,
                    title=title or sample_id,
                    biz_type=biz_type,
                    chapter_count=int(chapter_count or 0),
                )
            )
        return found


class MaterialIndex:
    def __init__(self, settings: DatabaseSettings) -> None:
        self.settings = settings

    def replace(self, dataset_key: str, hits: list[CorpusHit], on_progress=None) -> None:
        model = self._active_model()
        vectors = embed_texts([hit.excerpt for hit in hits], model, _api_key(), on_progress=on_progress) if hits else []
        with _connect(self.settings) as connection:
            types = sorted({hit.biz_type for hit in hits})
            for biz_type in types:
                connection.execute(
                    "DELETE FROM writing_material_vector WHERE dataset_key = %s AND biz_type = %s",
                    (dataset_key, biz_type),
                )
            for hit, vector in zip(hits, vectors):
                if len(vector) != model.dimensions:
                    raise RuntimeError(f"官方模型返回维度 {len(vector)}，与 {model.dimensions} 不一致")
                connection.execute(
                    """
                    INSERT INTO writing_material_vector
                        (dataset_key, source_file, location, excerpt, biz_type, model_code, embedding)
                    VALUES (%s, %s, %s, %s, %s, %s, CAST(%s AS JSON))
                    """,
                    (
                        dataset_key,
                        hit.source_file,
                        hit.location,
                        hit.excerpt,
                        hit.biz_type,
                        model.code,
                        json.dumps(vector),
                    ),
                )

    def search(self, dataset_key: str, biz_type: str, query: str, limit: int = 8) -> list[CorpusHit]:
        model = self._active_model()
        vector = embed_query(query, model, _api_key())
        with _connect(self.settings) as connection:
            rows = connection.execute(
                """
                SELECT source_file, location, excerpt, biz_type, embedding
                FROM writing_material_vector
                WHERE dataset_key = %s AND biz_type = %s AND model_code = %s
                """,
                (dataset_key, biz_type, model.code),
            ).fetchall()
        ranked = sorted(rows, key=lambda row: cosine(vector, _as_vector(row[4])), reverse=True)
        return [
            CorpusHit(
                id=f"vec-{index}",
                source_file=row[0],
                location=row[1],
                excerpt=row[2],
                biz_type=row[3],
            )
            for index, row in enumerate(ranked[:limit], start=1)
        ]

    def _active_model(self) -> EmbeddingModel:
        with _connect(self.settings) as connection:
            row = connection.execute(
                """
                SELECT code, provider, model_name, base_url, dimensions, active
                FROM writing_embedding_model
                WHERE active = 1
                ORDER BY code
                LIMIT 1
                """
            ).fetchone()
        if row is None:
            raise RuntimeError("writing_embedding_model 中没有启用的官方嵌入模型")
        return EmbeddingModel(
            code=row[0],
            provider=row[1],
            model_name=row[2],
            base_url=row[3],
            dimensions=int(row[4]),
            active=bool(row[5]),
        )


class MemoryVectorIndex:
    """本次资料的向量检索。未连接线上库时向量留在进程内，检索口径与入库后相同。"""

    def __init__(self, hits: list[CorpusHit]) -> None:
        self._hits = list(hits)
        self._vectors = embed_for_search([hit.excerpt for hit in self._hits])

    def search(self, dataset_key: str, biz_type: str, query: str, limit: int = 8) -> list[CorpusHit]:
        del dataset_key
        if not self._hits:
            return []
        query_vector = embed_for_search([query])[0]
        ranked = [
            (cosine(query_vector, vector), hit)
            for hit, vector in zip(self._hits, self._vectors)
            if hit.biz_type == biz_type
        ]
        ranked.sort(key=lambda item: item[0], reverse=True)
        return [hit for _, hit in ranked[:limit]]


def attach_materials(dataset_dir: Path, corpus: Corpus, on_progress=None) -> Corpus:
    from report_writing.infra.rag_parse import use_remote_parse

    dataset_key = str(dataset_dir.resolve())
    if use_remote_parse():
        from report_writing.services.rag_materials import RagMaterialIndex

        index = RagMaterialIndex(dataset_dir, corpus._biz_type)
        index.prepare(on_progress=on_progress)
        corpus.use_vector_index(index, dataset_key)
        return corpus
    settings = load_database_settings()
    if settings is None:
        corpus.use_vector_index(MemoryVectorIndex(corpus.all_hits()), dataset_key)
        return corpus
    index = MaterialIndex(settings)
    index.replace(dataset_key, corpus.all_hits(), on_progress=on_progress)
    corpus.use_vector_index(index, dataset_key)
    return corpus


def mysql_available(*, force: bool = False) -> bool:
    """带 TTL 的可用性探测；供 chat/robots/catalog 复用，避免每次新建探测连接。"""
    global _mysql_probe
    settings = load_database_settings()
    if settings is None:
        return False
    now = time.monotonic()
    if not force and _mysql_probe is not None and _mysql_probe[0] > now:
        return _mysql_probe[1]
    ok = False
    try:
        with _connect(settings) as connection:
            connection.execute("SELECT 1")
        ok = True
    except Exception as error:
        _log.debug("mysql_available probe failed: %s", error)
        ok = False
    _mysql_probe = (now + (_MYSQL_OK_TTL_S if ok else _MYSQL_FAIL_TTL_S), ok)
    return ok


def _settings_pool_key(settings: DatabaseSettings) -> tuple:
    return (
        settings.host,
        int(settings.port or 3306),
        settings.database,
        settings.user,
        bool(settings.ssl),
    )


def _new_raw_connection(settings: DatabaseSettings):
    import pymysql

    return pymysql.connect(
        host=settings.host,
        port=settings.port,
        user=settings.user,
        password=settings.password,
        database=settings.database,
        charset="utf8mb4",
        ssl={} if settings.ssl else None,
        autocommit=False,
        connect_timeout=8,
        read_timeout=60,
        write_timeout=60,
    )


def _discard_pool_locked(old) -> None:
    if old is None:
        return
    while True:
        try:
            c = old.get_nowait()
        except queue.Empty:
            break
        try:
            c.close()
        except Exception:
            pass


def _borrow_connection(settings: DatabaseSettings):
    global _pool, _pool_key, _pool_created, _ready
    key = _settings_pool_key(settings)
    while True:
        with _pool_lock:
            if _pool is None or _pool_key != key:
                old = _pool
                _pool = queue.Queue(maxsize=_POOL_MAX)
                _pool_key = key
                _pool_created = 0
                _ready = False
                _discard_pool_locked(old)
            try:
                conn = _pool.get_nowait()
                create = False
            except queue.Empty:
                conn = None
                if _pool_created < _POOL_MAX:
                    _pool_created += 1
                    create = True
                else:
                    create = False

        if conn is not None:
            try:
                conn.ping(reconnect=True)
                return conn
            except Exception:
                try:
                    conn.close()
                except Exception:
                    pass
                with _pool_lock:
                    _pool_created = max(0, _pool_created - 1)
                continue

        if create:
            try:
                return _new_raw_connection(settings)
            except Exception:
                with _pool_lock:
                    _pool_created = max(0, _pool_created - 1)
                raise

        # 池满：短等归还；超时则临时超额连接（用完关闭）
        assert _pool is not None
        try:
            conn = _pool.get(timeout=3.0)
        except queue.Empty:
            return _TemporaryConnection(_new_raw_connection(settings))
        try:
            conn.ping(reconnect=True)
            return conn
        except Exception:
            try:
                conn.close()
            except Exception:
                pass
            with _pool_lock:
                _pool_created = max(0, _pool_created - 1)
            continue


def pool_stats() -> dict:
    """测试/诊断用：当前池占用。"""
    with _pool_lock:
        idle = _pool.qsize() if _pool is not None else 0
        return {"max": _POOL_MAX, "created": _pool_created, "idle": idle}


def _return_connection(connection) -> None:
    global _pool_created
    if isinstance(connection, _TemporaryConnection):
        try:
            connection.raw.close()
        except Exception:
            pass
        return
    pool = _pool
    if pool is None:
        try:
            connection.close()
        except Exception:
            pass
        return
    try:
        pool.put_nowait(connection)
    except queue.Full:
        try:
            connection.close()
        except Exception:
            pass
        with _pool_lock:
            _pool_created = max(0, _pool_created - 1)


class _TemporaryConnection:
    """池满时的一次性连接包装，用完关闭不回池。"""

    def __init__(self, raw) -> None:
        self.raw = raw


def _connect(settings: DatabaseSettings):
    global _ready
    borrowed = _borrow_connection(settings)
    if isinstance(borrowed, _TemporaryConnection):
        connection = borrowed.raw
        pooled = False
        temp = borrowed
    else:
        connection = borrowed
        pooled = True
        temp = None

    if not _ready:
        with connection.cursor() as cursor:
            for statement in _STATEMENTS:
                cursor.execute(statement)
        connection.commit()
        _ready = True
    return _Connection(connection, pooled=pooled, temporary=temp)


class _Connection:
    def __init__(self, connection, *, pooled: bool = True, temporary=None) -> None:
        self.connection = connection
        self._pooled = pooled
        self._temporary = temporary

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        try:
            if exc_type is None:
                self.connection.commit()
            else:
                self.connection.rollback()
        except Exception:
            try:
                self.connection.rollback()
            except Exception:
                pass
        if self._temporary is not None:
            _return_connection(self._temporary)
        elif self._pooled:
            _return_connection(self.connection)
        else:
            try:
                self.connection.close()
            except Exception:
                pass

    def execute(self, sql: str, params: tuple | None = None):
        cursor = self.connection.cursor()
        cursor.execute(sql, params or ())
        return cursor


def _api_key() -> str:
    from report_writing.infra.bailian import load_bailian_settings

    return load_bailian_settings().api_key


def _settings_from_config() -> DatabaseSettings | None:
    from report_writing.infra.project_config import spring_datasource_master

    merged = spring_datasource_master()
    url = str(merged.get("url") or "").strip()
    if not url:
        return None
    settings = _settings_from_jdbc(url, str(merged.get("username") or ""), str(merged.get("password") or ""))
    return settings


def _settings_from_jdbc(url: str, user: str, password: str) -> DatabaseSettings:
    match = _JDBC.match(url.strip())
    if match is None:
        raise ValueError("业务库地址须为 jdbc:mysql://主机:端口/库名")
    query = parse_qs(url.split("?", 1)[1] if "?" in url else "")
    use_ssl = (query.get("useSSL") or ["false"])[0].lower() == "true"
    return DatabaseSettings(
        host=match.group(1),
        port=int(match.group(2) or 3306),
        database=match.group(3),
        user=user,
        password=password,
        ssl=use_ssl,
    )


def _as_vector(payload) -> list[float]:
    if isinstance(payload, str):
        payload = json.loads(payload)
    return [float(value) for value in payload]


def record_brief(payload):
    from report_writing.domain.models import WritingBrief

    if isinstance(payload, str):
        payload = json.loads(payload)
    return WritingBrief.from_dict(payload)
