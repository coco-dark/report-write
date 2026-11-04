"""蓝本向导短语规则（已弃用意图短切；保留模块以免旧 import 崩）。

口语意图一律走 classify_wizard_action_llm；本模块不再抢路由。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from report_writing.domain.models import ChatSession


def message_means_outline_extract_done(message: str) -> bool:
    """Deprecated: 意图由向导分类器判定，恒为 False。"""
    return False


def rule_wizard_action_from_message(
    message: str,
    wizard: dict[str, Any],
    *,
    home: Path | None = None,
    session: ChatSession | None = None,
) -> dict[str, Any] | None:
    """Deprecated: 不再用正则抢先于 LLM。"""
    return None


def coerce_reextract_to_outline_done(
    message: str,
    wizard: dict[str, Any],
    action: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Deprecated: 信任分类器结果。"""
    return action
