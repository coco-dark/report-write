"""子 Agent：各自暴露 Tool 集合，由总控通过 delegate tool 调用。"""

from report_writing.agents.subagents.blueprint import run_blueprint_subagent
from report_writing.agents.subagents.blueprint_wizard import run_blueprint_wizard_subagent
from report_writing.agents.subagents.render import run_render_subagent
from report_writing.agents.subagents.style import run_style_subagent

__all__ = [
    "run_blueprint_subagent",
    "run_blueprint_wizard_subagent",
    "run_render_subagent",
    "run_style_subagent",
]
