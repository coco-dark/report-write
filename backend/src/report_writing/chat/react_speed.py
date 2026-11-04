"""ReAct 总控速度相关配置（读 config/application.yml）。"""

from __future__ import annotations

from report_writing.infra.config_settings import (
    control_dialogue_turns as _dialogue_turns,
    control_max_tokens as _control_max_tokens,
    control_react_rounds as _react_rounds,
    intent_max_tokens as _intent_max_tokens,
)


def control_react_rounds() -> int:
    n = _react_rounds()
    return max(2, min(n, 12))


def control_dialogue_max_turns() -> int:
    n = _dialogue_turns()
    return max(4, min(n, 16))


def control_max_tokens() -> int:
    n = _control_max_tokens()
    return max(256, min(n, 8192))


def intent_max_tokens() -> int:
    n = _intent_max_tokens()
    return max(128, min(n, 4096))
