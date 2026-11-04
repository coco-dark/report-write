import json

from report_writing.chat.intent_router import (
    WRITING_PATH_FREEFORM,
    get_writing_path,
    lock_writing_path,
    user_chose_skip_blueprint,
    user_wants_path_correction_to_freeform,
)


def _invoke(system: str, user: str) -> str:
    if "入口意图" in system:
        return json.dumps({"intent": "path_correction", "confidence": 0.9}, ensure_ascii=False)
    return "{}"
from report_writing.domain.models import ChatSession


def test_lock_writing_path_makes_skip_persistent():
    session = ChatSession(id="s1", phase="intent")
    lock_writing_path(session, WRITING_PATH_FREEFORM)
    assert get_writing_path(session) == WRITING_PATH_FREEFORM
    assert user_chose_skip_blueprint(session)


def test_lock_freeform_clears_stale_blueprint_wizard():
    from report_writing.chat.intent_router import active_blueprint_wizard

    session = ChatSession(
        id="s_ff",
        phase="freeform_outline",
        blueprint_id="bp_stale",
        freeform={
            "blueprint_wizard": {
                "blueprint_id": "bp_stale",
                "step": "study",
                "chapter_count": 33,
            }
        },
    )
    lock_writing_path(session, WRITING_PATH_FREEFORM)
    assert get_writing_path(session) == WRITING_PATH_FREEFORM
    assert not (session.freeform or {}).get("blueprint_wizard")
    assert not session.blueprint_id
    assert active_blueprint_wizard(session) is None


def test_path_correction_phrase():
    assert user_wants_path_correction_to_freeform("不是蓝本，我说直接写", invoke=_invoke)
