"""兼容 re-export：会话状态见 session_state；入口/向导口语意图见 entry_intent / intent_llm。"""

from __future__ import annotations

from report_writing.chat.entry_intent import (
    allows_agent_blueprint_select,
    extract_wizard_title_hint,
    infer_freeform_meta_from_messages,
    is_create_blueprint_intent,
    is_save_freeform_blueprint_intent,
    is_skip_blueprint_intent,
    message_explicitly_requests_blueprint,
    message_requests_blueprint_match,
    pick_auto_blueprint_id,
    user_wants_path_correction_to_freeform,
)
from report_writing.chat.session_state import (
    WRITING_PATH_BLUEPRINT,
    WRITING_PATH_FREEFORM,
    active_blueprint_wizard,
    dialogue_recently_skipped_blueprint,
    get_writing_path,
    lock_writing_path,
    phase_allows_blueprint_routing,
    touch_session_plan,
    user_chose_skip_blueprint,
)

__all__ = [
    "WRITING_PATH_BLUEPRINT",
    "WRITING_PATH_FREEFORM",
    "active_blueprint_wizard",
    "allows_agent_blueprint_select",
    "dialogue_recently_skipped_blueprint",
    "extract_wizard_title_hint",
    "get_writing_path",
    "infer_freeform_meta_from_messages",
    "is_create_blueprint_intent",
    "is_save_freeform_blueprint_intent",
    "is_skip_blueprint_intent",
    "lock_writing_path",
    "message_explicitly_requests_blueprint",
    "message_requests_blueprint_match",
    "phase_allows_blueprint_routing",
    "pick_auto_blueprint_id",
    "touch_session_plan",
    "user_chose_skip_blueprint",
    "user_wants_path_correction_to_freeform",
]
