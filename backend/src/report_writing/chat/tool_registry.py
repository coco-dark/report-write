"""总控 Tool 语义单源（供 @tool docstring 与可选意图分类器 catalog 复用）。

LangChain `create_agent` 以 Tool description 为模型可见的工具说明；
意图分类器仅在 `intent_tool_catalog_in_prompt=true` 时在 system 中附带本 catalog。
"""

from __future__ import annotations


def intent_catalog_lines(catalog: dict[str, str]) -> str:
    return "\n".join(f"- {k}: {v}" for k, v in catalog.items())


# 写作中途专用分类器词表（非全量 chat tools）
WRITING_CONTROL_TOOLS: dict[str, str] = {
    "add_chapter_element": "为某一章新建或配置写作要素草稿（键值对、固定文案、文件、引用槽位、引用其它要素结果等）",
    "collect_to_element_library": "把本任务里已有的要素收入个人要素库（不是新建章节要素，也不是打开要素库浏览）",
    "associate_elements": "把已有要素关联到某章（打开关联面板勾选，不是新建）",
    "dissociate_chapter_element": "取消某章与要素的关联（不删要素、不删要素库）；同名多个时先澄清",
    "remove_document_element": "从当前写作文档删除要素（含各章关联）；不删除个人要素库",
    "open_element_library": "用户要查看/打开要素库（浏览个人要素，不是给章节加要素）",
    "open_blueprint_library": "用户要查看/浏览蓝本库（管理页，不是 match 选型、也不是 list 选用）",
    "open_knowledge": "用户要查看/打开资料库（资料库是放资料包的地方；兼容旧称「知识库」）",
    "remember_writing_goal": "记住用户身份、岗位、项目背景等后续写作都要遵守的约束",
    "submit_points": "用户在补充各章「怎么写」的写作要点（通常较长、或明确在谈写法/结构）",
    "none": "其它意图：由总控自行选工具、澄清或闲聊",
}

# 蓝本向导命令（wizard 分类器）
WIZARD_COMMAND_HINTS: dict[str, str] = {
    "submit_meta": "提交蓝本名称并创建蓝本（当前在「填写名称」步，用户给出了名称）",
    "mark_upload_done": "用户表示样板已上传完成，进入「提取目录」步（与点上传完成同类）",
    "re_extract_outline": "从已上传样板识别、生成或修正章节目录（提取/重新提取目录）",
    "re_upload_sample": "重新上传或更换 Word/PDF 样板文件",
    "go_outline_confirm": "进入「确认目录」步骤，在拓扑图里核对章节（尚未最终确认）",
    "confirm_outline": "用户确认目录无误（只确认目录，不自动学习；与点「确认目录」同类）",
    "go_study": "进入分析要点并开始分析全部章节写作要点（与点「开始分析全部」同类；用户说「开始学习」也算）；指定章节范围请用 re_study_chapters",
    "re_study_chapters": "对指定章节重新分析写作要点（如第1-2章、把第三章再分析一遍；用户说「学习/再学/重新分析本章」也算）。未指定方式时后端会追问：根据原文 / 依据样本或自动分析",
    "abandon_wizard": "不要当前这份蓝本、退出向导、换别的蓝本或改用不绑蓝本写（不是跳过某一步）",
    "none": "与当前向导无关、闲聊，或信息不足需要追问",
}
