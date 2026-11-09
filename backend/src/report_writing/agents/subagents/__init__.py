"""子 Agent：官方模式——总控 @tool 包装 create_agent，invoke 后结果回总控。"""

from report_writing.agents.subagents.blueprint import run_blueprint_subagent
from report_writing.agents.subagents.render import run_render_subagent
from report_writing.agents.subagents.style import run_style_subagent

__all__ = [
    "run_blueprint_subagent",
    "run_render_subagent",
    "run_style_subagent",
]
