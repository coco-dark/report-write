"""窄 JSON 分类器：澄清卡回复、槽位、向导 command。

这不是「用户意图 → 选 Tool」的第二大脑。口语选 Tool 只走 LangChain `create_agent`。
实现暂仍在 `intent_llm.py`（旧 import 兼容）；新代码从本模块引用。
"""

from __future__ import annotations

from report_writing.chat.intent_llm import (
    classify_clarify_reply_llm,
    classify_doc_meta_line_llm,
    classify_doc_name_reply_llm,
    classify_document_scope_llm,
    classify_entry_routing_llm,
    classify_explicit_doc_name_given_llm,
    classify_explicit_remember_goal_llm,
    classify_no_match_path_llm,
    classify_study_scope_llm,
    classify_unclear_intent_llm,
    classify_user_own_sample_llm,
    classify_wizard_action_llm,
    classify_wizard_command_llm,
    classify_write_scope_llm,
    extract_collect_element_name_llm,
    extract_write_subject_title_llm,
    infer_element_payload_llm,
    pick_blueprint_id_llm,
    resolve_wizard_action,
    resolve_wizard_command,
)

__all__ = [
    "classify_clarify_reply_llm",
    "classify_doc_meta_line_llm",
    "classify_doc_name_reply_llm",
    "classify_document_scope_llm",
    "classify_entry_routing_llm",
    "classify_explicit_doc_name_given_llm",
    "classify_explicit_remember_goal_llm",
    "classify_no_match_path_llm",
    "classify_study_scope_llm",
    "classify_unclear_intent_llm",
    "classify_user_own_sample_llm",
    "classify_wizard_action_llm",
    "classify_wizard_command_llm",
    "classify_write_scope_llm",
    "extract_collect_element_name_llm",
    "extract_write_subject_title_llm",
    "infer_element_payload_llm",
    "pick_blueprint_id_llm",
    "resolve_wizard_action",
    "resolve_wizard_command",
]
