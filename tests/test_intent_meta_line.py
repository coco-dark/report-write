from report_writing.chat.session_snapshot import looks_like_freeform_meta_line
from report_writing.domain.models import ChatMessage, ChatSession


def test_meta_line_shape():
    assert looks_like_freeform_meta_line("述职报告 | 2026个人述职")
    session = ChatSession(
        id="s1",
        messages=[
            ChatMessage(role="assistant", text="发一行类型｜标题"),
        ],
    )
    assert looks_like_freeform_meta_line("工作说明｜某某项目")
