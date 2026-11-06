"""机器人 chat_prompt  lint：禁止把 LangChain Tool id 枚举写进 system prompt。"""

from __future__ import annotations

import re

_CATALOG_HEADER = re.compile(
    r"(工具列表|可用工具|Tool\s*清单|工具对照|tool\s*list|tools\s*:)",
    re.I,
)

# 一行内出现多个 snake_case 工具 id 视为枚举行
_SNAKE_ID = re.compile(r"\b[a-z][a-z0-9_]{2,48}\b")


def _known_tool_ids() -> frozenset[str]:
    """实际绑定到总控的 Tool.name，用来拦 system prompt 贴工具清单。"""
    from pathlib import Path

    from report_writing.agents.chat_control import ChatControlState
    from report_writing.agents.chat_tools import build_chat_tools
    from report_writing.domain.models import ChatSession

    state = ChatControlState(home=Path("."), session=ChatSession(id="_lint"))
    return frozenset(
        name
        for name in (
            str(getattr(item, "name", "") or "").strip() for item in build_chat_tools(state)
        )
        if name
    )


def chat_prompt_tool_catalog_violations(text: str) -> list[str]:
    """返回违规说明；空列表表示通过。"""
    body = (text or "").strip()
    if not body:
        return []
    known = _known_tool_ids()
    violations: list[str] = []
    for i, line in enumerate(body.splitlines(), 1):
        stripped = line.strip()
        if not stripped:
            continue
        if _CATALOG_HEADER.search(stripped):
            violations.append(f"第{i}行：含工具列表标题")
            continue
        ids = [tok for tok in _SNAKE_ID.findall(stripped) if tok in known]
        if len(ids) >= 2:
            violations.append(f"第{i}行：枚举多个 Tool id（{', '.join(ids[:5])}）")
        elif len(ids) == 1 and stripped.startswith(("-", "*", "•", "1.", "2.")):
            violations.append(f"第{i}行：列表项含 Tool id `{ids[0]}`")
    return violations


def sanitize_chat_prompt(text: str) -> tuple[str, list[str]]:
    """去掉明显 Tool 枚举行，返回 (清洗后文本, 被删原因)。"""
    body = (text or "").strip()
    if not body:
        return "", []
    known = _known_tool_ids()
    kept: list[str] = []
    removed: list[str] = []
    for i, line in enumerate(body.splitlines(), 1):
        stripped = line.strip()
        if not stripped:
            kept.append(line)
            continue
        if _CATALOG_HEADER.search(stripped):
            removed.append(f"第{i}行：工具列表标题")
            continue
        ids = [tok for tok in _SNAKE_ID.findall(stripped) if tok in known]
        if len(ids) >= 2 or (len(ids) == 1 and stripped.startswith(("-", "*", "•"))):
            removed.append(f"第{i}行：Tool 枚举")
            continue
        kept.append(line)
    out = "\n".join(kept).strip()
    return out, removed
