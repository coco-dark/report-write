"""Turn-plan 意图分类器 system prompt（策略与 JSON schema；工具语义默认不在此重复）。"""

from __future__ import annotations


def turn_plan_system_prompt(
    allowed: list[str],
    *,
    freeform_path: bool,
    include_tool_catalog: bool,
    multi_action_hint: str,
) -> str:
    """构建 classify_turn_plan / control plan JSON 的 system 段。"""
    allowed_csv = ", ".join(allowed)
    system = (
        "你是对话写文档的意图调度器（输出 JSON 计划，供后端串行或转交 ReAct 总控）。"
        "根据本轮、近期对话与会话快照选出 0～4 个 tool id；说法不固定，按意思判断。\n"
        "一句可含多个步骤（定名+匹配+资料包+开写等），须全部列入 tools，不要只返回第一个。\n"
        "\n"
        "## 输出\n"
        "只返回 JSON，不要 Markdown：\n"
        '{"confidence":0.0,"uncertain":false,'
        '"slots":{"doc_name":null,"writing_path":null},"tools":[{"tool":"<id>","args":{}}],'
        '"missing":[]}\n'
        f"tool ∈ {{{allowed_csv}}}\n"
        "slots：本轮已确定的要素，没有就 null。"
        "writing_path ∈ {match, template, freeform, null}。"
        "missing：本轮目标还缺的槽，∈ {doc_name, writing_path, freeform_mode, chapter_key, blueprint_id}。\n"
        "若 user 块含「规则观察」行，仅为后端只读快照，勿机械覆盖你的 missing/tools。\n"
        f"{multi_action_hint}\n"
        "tools 是唯一执行清单；后端按 JSON 的 tools 执行。"
        "各 tool 的调用时机与参数约束以 LangChain 绑定 Tool 的 description 为准，此处只列 id。\n"
        "确定的槽位 → 列入 tools；缺的 → missing；说不清 → uncertain=true，勿猜开写/选蓝本/删要素/收录。\n"
        "\n"
        "## 策略（与总控 ReAct 一致）\n"
        "默认操作当前焦点文档；另写一份 / 切到已有文档 / 多文档歧义 → uncertain 或 ask_intent，勿静默改标题。\n"
        "工作名（写一份××）≠ 正式定名（名字叫…）；题材勿当 doc_name 覆盖焦点文档。\n"
        "写正文 vs 学/分析：已绑蓝本写章用 select_write_chapters / write_analyzed_only；"
        "分析要点用 re_study_chapters（填 chapter_sequences、study_scope）。\n"
        "要素是写作物料，蓝本是模板；勿把「要素」当蓝本去 match。\n"
        "寒暄（你好/谢谢）→ tools=[]，勿 set_session_meta / ask_entry_path。\n"
        "禁止低置信静默 start_writing、选蓝本、破坏性删除或收录入库。\n"
    )
    if include_tool_catalog:
        from report_writing.chat.tool_registry import CHAT_CONTROL_TOOL_DOCS

        lines = [
            f"- {tid}: {CHAT_CONTROL_TOOL_DOCS[tid]}"
            for tid in allowed
            if tid in CHAT_CONTROL_TOOL_DOCS
        ]
        if lines:
            system += "\n## Tool 含义（摘要）\n" + "\n".join(lines[:40]) + "\n"
    if freeform_path:
        system += (
            "\n## 当前已走无蓝本\n"
            "禁止 match/list/select/begin 蓝本与 re_study_chapters；"
            "目录 submit_outline；要点 generate_freeform_points；定名 set_session_meta(args.title)。\n"
        )
    return system
