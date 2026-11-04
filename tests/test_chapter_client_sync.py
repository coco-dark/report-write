from report_writing.chat.chapter_client_sync import (
    client_chapter_draft_text,
    clear_client_chapter_edit,
    merge_client_chapter_edits,
)
from report_writing.domain.models import ChatSession, ChatUiStateRequest
from report_writing.services.chat_session import apply_chat_ui_state


def test_client_draft_only_when_touched():
    session = ChatSession(id="s")
    merge_client_chapter_edits(
        session,
        {"w1:c1": {"text": "<p>改过的</p>", "touched": True}},
    )
    assert client_chapter_draft_text(session, "w1", "c1") == "<p>改过的</p>"


def test_ignore_untouched_sync():
    session = ChatSession(id="s")
    merge_client_chapter_edits(session, {"w1:c1": {"text": "<p>x</p>", "touched": False}})
    assert client_chapter_draft_text(session, "w1", "c1") == ""


def test_ui_state_request_merges_edits():
    session = ChatSession(id="s")
    apply_chat_ui_state(
        session,
        ChatUiStateRequest(chapter_edits={"w1:c1": {"text": "<p>本地</p>", "touched": True}}),
    )
    assert client_chapter_draft_text(session, "w1", "c1") == "<p>本地</p>"


def test_clear_after_confirm():
    session = ChatSession(id="s")
    merge_client_chapter_edits(session, {"w1:c1": {"text": "a", "touched": True}})
    clear_client_chapter_edit(session, "w1", "c1")
    assert client_chapter_draft_text(session, "w1", "c1") == ""
