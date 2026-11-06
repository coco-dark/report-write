"""ReAct 总控速度相关配置（读 config/application.yml）。"""

from __future__ import annotations

from report_writing.infra.config_settings import (
    control_dialogue_turns as _dialogue_turns,
    control_react_rounds as _react_rounds,
)


def control_react_rounds() -> int:
    n = _react_rounds()
    return max(2, min(n, 12))


def control_dialogue_max_turns() -> int:
    n = _dialogue_turns()
    return max(4, min(n, 16))
