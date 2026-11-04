"""子 Agent 注册表：总控只注册 delegate tool，扩展在对应模块的 build_*_tools 中加 @tool。"""

SUBAGENT_DELEGATES = (
    {
        "id": "blueprint",
        "master_tool": "blueprint_subagent",
        "alias_tools": ("blueprint_wizard_subagent",),
        "run": "report_writing.agents.subagents.blueprint.run_blueprint_subagent",
        "tools": "report_writing.agents.subagents.blueprint.build_blueprint_subagent_tools",
    },
    {
        "id": "render",
        "master_tool": "render_subagent",
        "run": "report_writing.agents.subagents.render.run_render_subagent",
        "tools": "report_writing.agents.subagents.render.build_render_subagent_tools",
    },
    {
        "id": "style",
        "master_tool": "style_subagent",
        "run": "report_writing.agents.subagents.style.run_style_subagent",
        "tools": "report_writing.agents.subagents.style.build_style_subagent_tools",
    },
)

# 章节 search/write/check 不在对话子 Agent 内重复实现，见 agents/supervisor.py（写作任务运行期）
