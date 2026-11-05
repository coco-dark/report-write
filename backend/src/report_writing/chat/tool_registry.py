"""总控 Tool 语义单源（供 @tool docstring 与可选意图分类器 catalog 复用）。

LangChain `create_agent` 以 Tool description 为模型可见的工具说明；
意图分类器仅在 `intent_tool_catalog_in_prompt=true` 时在 system 中附带本 catalog。
"""

from __future__ import annotations

from typing import Any


def intent_catalog_lines(catalog: dict[str, str]) -> str:
    return "\n".join(f"- {k}: {v}" for k, v in catalog.items())


def apply_chat_control_tool_descriptions(tools: list[Any]) -> list[Any]:
    """用 registry 中的长描述覆盖 LangChain Tool.description（单源）。"""
    for item in tools:
        name = str(getattr(item, "name", "") or "").strip()
        doc = CHAT_CONTROL_TOOL_DOCS.get(name)
        if doc:
            item.description = doc.strip()
    return tools


# 对话总控 ReAct tools（name → LangChain tool description）
CHAT_CONTROL_TOOL_DOCS: dict[str, str] = {
    "list_blueprints": (
        "列出当前机器人/账号下可用蓝本。可选 q 关键词过滤。"
        "用户只想浏览库内蓝本时调用；选型写作通常用 match_blueprints。"
    ),
    "ask_entry_path": (
        "新建或缺入口时：读 onboarding gaps，只挂仍缺的澄清卡（定名/有蓝本/无蓝本路径等）。"
        "不静默执行 match 或开写；读返回的 suggested_next_tools 与 observation。"
    ),
    "match_blueprints": (
        "按用户描述匹配蓝本库；仅用户明确要匹配、或入口已选「先匹配蓝本」后调用。"
        "禁止擅自 select_blueprint；匹配结果以卡片为准。"
    ),
    "select_blueprint": (
        "选用指定 blueprint_id 绑定当前写作文档。"
        "须用户点选或口语明确点名；禁止低置信代选。"
    ),
    "skip_blueprint": (
        "放弃/不绑蓝本，走无蓝本（自动大纲或模版向导）。"
        "用户说不要这份蓝本、换无蓝本方式时调用。"
    ),
    "begin_blueprint_wizard": (
        "当前文档 w* 上开启「依据模版编写」向导（上传/目录/学习）；收录前不入蓝本库。"
        "须 explicit_create=true 或用户点选/确认创建；不是 save_freeform_as_blueprint。"
    ),
    "start_writing": (
        "开始按章写作（后台 supervisor：检索/写/检）。"
        "参数 writing_pace=batch|step；chapter_keys_json 为章节 key 数组。"
        "多章未指定 pace 或缺待补检查时会返回澄清卡，读 observation。"
    ),
    "select_write_chapters": (
        "勾选要写的章节（已分析/可写范围）；开写前选章。"
        "chapter_keys_json 为 key 列表；已绑蓝本时使用。"
    ),
    "write_analyzed_only": (
        "只写已分析完成的章节子集；chapter_keys_json 指定 key。"
    ),
    "set_session_meta": (
        "定名或改名：必须传结构化 title=短文档名（从用户话拆出），message=原话可选。"
        "禁止仅 message 含「改个名字 …」而不传 title；禁止把写题材/寒暄当 title。"
    ),
    "submit_outline": (
        "提交或再生成章节目录（无蓝本路径）。可 outline_text / regenerate。"
        "缺目录且用户要自动生成时 regenerate=true。"
    ),
    "generate_freeform_points": (
        "无蓝本任务：为已确认目录生成各章写作要点。"
        "须已有 submit_outline 确认的大纲。"
    ),
    "remember_writing_goal": (
        "记住岗位/项目/写作约束（session_goal），供后续各章遵守。notes=摘要。"
    ),
    "confirm_chapter": (
        "确认本章草稿；pause_after=true 时确认后暂停写作。"
    ),
    "prepare_export_word": (
        "导出 Word；收录/导出前须正式定名（writing_doc_named）。"
    ),
    "save_freeform_as_blueprint": (
        "把当前无蓝本写作任务收录进蓝本库；须正式定名。不是 begin_blueprint_wizard。"
    ),
    "blueprint_subagent": (
        "委派蓝本子助手：复杂蓝本配置/选型（匹配、列表、向导步）。"
        "简单一步仍优先直接调用 match/list/wizard 类 Tool。"
    ),
    "render_subagent": (
        "委派渲染模版子助手：列出/配置 HTML 导出模版（导出前）。"
    ),
    "style_subagent": (
        "委派 Word 样式子助手：列出/配置导出样式（导出前）。"
    ),
    "add_chapter_element": (
        "为某章新建要素草稿。payload_json：kind/name/chapter_key/query 等。"
        "文件要素 kind=file；勿与 collect_to_element_library 混淆。"
    ),
    "associate_elements": (
        "把已有要素关联到章节；打开关联面板。payload_json 含 element_id/name、chapter_key。"
    ),
    "dissociate_chapter_element": (
        "取消某章与要素的关联（不删要素库）。"
    ),
    "remove_document_element": (
        "从当前写作文档删除要素（不删个人要素库）。"
    ),
    "collect_to_element_library": (
        "把文档内已有要素收入个人要素库（不是新建章节要素）。"
    ),
    "open_element_library": "打开要素库浏览页（不是给章节加要素）。",
    "open_blueprint_library": "打开蓝本库管理页（不是 match 选型）。",
    "open_knowledge": "打开资料库页（兼容旧称知识库）。",
}

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
