from report_writing.chat.dialogue import format_full_dialogue
from report_writing.domain.models import ChatMessage, ChatSession


def test_format_full_dialogue():
    session = ChatSession(
        id="s",
        messages=[
            ChatMessage(role="user", text="你好"),
            ChatMessage(role="assistant", text="在"),
        ],
    )
    rows = format_full_dialogue(session)
    assert len(rows) == 2
    assert rows[0]["role"] == "user"
