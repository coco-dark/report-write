"""兼容导出：向导 Tool 与总控入口别名。"""

from report_writing.agents.subagents.blueprint import run_blueprint_subagent
from report_writing.agents.subagents.blueprint_wizard_impl import (
    _make_wizard_scripted_model,
    build_blueprint_wizard_subagent_tools,
)

run_blueprint_wizard_subagent = run_blueprint_subagent

__all__ = [
    "build_blueprint_wizard_subagent_tools",
    "_make_wizard_scripted_model",
    "run_blueprint_wizard_subagent",
    "run_blueprint_subagent",
]
