"""chat_prompt 禁止 Tool 枚举 lint。"""

from __future__ import annotations

from report_writing.chat.chat_prompt_lint import (
    chat_prompt_tool_catalog_violations,
    sanitize_chat_prompt,
)


def test_detects_tool_list_line():
    text = "可用工具：\n- match_blueprints\n- start_writing\n"
    v = chat_prompt_tool_catalog_violations(text)
    assert v


def test_sanitize_strips_enumeration():
    raw = "你是小墨。\n- match_blueprints：匹配\n- ask_entry_path：入口\n仍要口语化。"
    cleaned, removed = sanitize_chat_prompt(raw)
    assert "match_blueprints" not in cleaned
    assert removed
    assert "口语化" in cleaned
