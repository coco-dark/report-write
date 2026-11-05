"""从 config/application.yml（+ local）读取 report_writing 运行时配置；pytest 仍可用环境变量覆盖。"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

from report_writing.infra.project_config import _merged


@lru_cache(maxsize=1)
def _application() -> dict:
    return _merged("application")


def report_writing_block() -> dict[str, Any]:
    block = (_application().get("report_writing") or {})
    return dict(block) if isinstance(block, dict) else {}


def _nested(block: dict[str, Any], *keys: str) -> Any:
    cur: Any = block
    for key in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def setting(
    *yaml_keys: str,
    default: Any = None,
    env: str | None = None,
    coerce: type | None = None,
) -> Any:
    raw_env = (os.environ.get(env or "", "") or "").strip() if env else ""
    if raw_env:
        value: Any = raw_env
    else:
        value = _nested(report_writing_block(), *yaml_keys)
        if value is None or value == "":
            value = default
    if coerce is int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return int(default) if default is not None else 0
    if coerce is float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return float(default) if default is not None else 0.0
    if coerce is bool:
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "on"}
    return value


def data_home_name() -> str:
    return str(setting("data_home", default="data", env="REPORT_WRITING_HOME") or "data").strip() or "data"


def llm_mode() -> str:
    return str(setting("llm_mode", default="bailian", env="REPORT_WRITING_LLM") or "bailian").strip().lower()


def database_mode() -> str:
    raw = str(setting("database", "mode", default="auto", env="REPORT_WRITING_DATABASE") or "auto").strip().lower()
    return raw if raw in {"file", "mysql", "auto"} else "auto"


def document_parse_mode() -> str:
    raw = str(
        setting("document_parse", "mode", default="rag", env="REPORT_WRITING_PARSE") or "rag"
    ).strip().lower()
    return "local" if raw == "local" else "rag"


def document_parse_log_chunk() -> int:
    return int(setting("document_parse", "log_chunk", default=6000, env="REPORT_WRITING_RAG_LOG_CHUNK", coerce=int))


def outline_toc_max_chars() -> int:
    """目录区切片上限（几百章目录常超过旧默认 28k）。"""
    return max(
        28000,
        int(
            setting("outline", "toc_max_chars", default=250000, env="OUTLINE_TOC_MAX_CHARS", coerce=int)
            or 250000
        ),
    )


def outline_multiround_enabled() -> bool:
    return setting("outline", "multiround", default=True, env="OUTLINE_MULTIROUND", coerce=bool)


def outline_chapter_scan_max_chars() -> int:
    return max(
        12000,
        int(
            setting("outline", "chapter_scan_max_chars", default=80000, env="OUTLINE_CHAPTER_SCAN_MAX", coerce=int)
            or 80000
        ),
    )


def outline_prefer_local_docx() -> bool:
    return setting("outline", "prefer_local_docx", default=True, env="OUTLINE_PREFER_LOCAL_DOCX", coerce=bool)


def outline_try_rag_html() -> bool:
    return setting("outline", "try_rag_html", default=True, env="OUTLINE_TRY_RAG_HTML", coerce=bool)


def redis_url() -> str:
    url = str(setting("redis", "url", default="", env="REPORT_WRITING_REDIS_URL") or "").strip()
    if url:
        return url
    return (os.environ.get("REDIS_URL") or "").strip()


def control_model_name() -> str:
    return str(
        setting("chat_control", "model_name", default="", env="REPORT_WRITING_CONTROL_MODEL") or ""
    ).strip()


def control_max_tokens() -> int:
    return setting("chat_control", "max_tokens", default=1536, env="REPORT_WRITING_CONTROL_MAX_TOKENS", coerce=int)


def control_react_rounds() -> int:
    return setting("chat_control", "react_rounds", default=5, env="REPORT_WRITING_CONTROL_ROUNDS", coerce=int)


def control_dialogue_turns() -> int:
    return setting(
        "chat_control", "dialogue_turns", default=6, env="REPORT_WRITING_CONTROL_DIALOGUE_TURNS", coerce=int
    )


def intent_max_tokens() -> int:
    return setting("intent", "max_tokens", default=768, env="REPORT_WRITING_INTENT_MAX_TOKENS", coerce=int)


def reply_polish_enabled() -> bool:
    env = (os.environ.get("REPORT_WRITING_REPLY_POLISH", "") or "").strip().lower()
    if env in {"0", "false", "off", "no"}:
        return False
    if env in {"1", "true", "on", "yes"}:
        return True
    return setting("features", "reply_polish", default=True, coerce=bool)


def chain_debug_enabled() -> bool:
    env = (os.environ.get("REPORT_WRITING_CHAIN_DEBUG", "") or "").strip().lower()
    if env in {"1", "true", "on", "yes"}:
        return True
    return setting("features", "chain_debug", default=False, coerce=bool)


def blueprint_match_llm_enabled() -> bool:
    env = (os.environ.get("REPORT_WRITING_BP_MATCH_LLM", "") or "").strip().lower()
    if env in {"1", "true", "on", "yes"}:
        return True
    return setting("features", "blueprint_match_llm", default=False, coerce=bool)


def scripted_chat() -> bool:
    if os.environ.get("REPORT_WRITING_SCRIPTED_CHAT") == "1":
        return True
    return setting("features", "scripted_chat", default=False, coerce=bool)


def intent_plan_enabled() -> bool:
    env = (os.environ.get("REPORT_WRITING_INTENT_PLAN", "") or "").strip().lower()
    if env in {"0", "false", "off", "no"}:
        return False
    if env in {"1", "true", "on", "yes"}:
        return True
    return setting("features", "intent_plan", default=False, coerce=bool)


def intent_tool_catalog_in_prompt() -> bool:
    env = (os.environ.get("REPORT_WRITING_INTENT_TOOL_CATALOG", "") or "").strip().lower()
    if env in {"1", "true", "on", "yes"}:
        return True
    if env in {"0", "false", "off", "no"}:
        return False
    return setting("features", "intent_tool_catalog_in_prompt", default=False, coerce=bool)


def database_jdbc_url_override() -> str:
    return str(setting("database", "jdbc_url", default="", env="REPORT_WRITING_DATABASE_URL") or "").strip()
