"""意图分类上下文：结合会话文档，而非只靠多轮澄清。"""

from __future__ import annotations

from report_writing.chat.classifier_context import classifier_context_user_block, pack_classifier_context
from report_writing.chat.session_documents import set_focus_document_key, upsert_session_document
from report_writing.domain.models import ChatMessage, ChatSession


def test_pack_classifier_context_includes_focus_and_dialogue():
    session = ChatSession(id="s_ctx", phase="writing", task_id="w1", blueprint_id="bpA")
    upsert_session_document(
        session, kind="blueprint", task_id="bpA", blueprint_id="bpA", title="安全预评价-A"
    )
    upsert_session_document(
        session, kind="blueprint", task_id="bpB", blueprint_id="bpB", title="职业病危害评价"
    )
    set_focus_document_key(session, "bp:bpA")
    session.messages = [
        ChatMessage(
            role="user",
            text="继续写第1章",
            document_key="bp:bpA",
            document_id="bp:bpA",
        ),
        ChatMessage(
            role="assistant",
            text="好的",
            document_key="bp:bpA",
            document_id="bp:bpA",
        ),
    ]
    ctx = pack_classifier_context(session)
    assert "bp:bpA" in (ctx.get("focus_document_brief") or "")
    assert "bp:bpB" in (ctx.get("session_documents_brief") or "")
    assert any("doc_id=bp:bpA" in str(r.get("text") or "") for r in ctx.get("dialogue_tail") or [])

    block = classifier_context_user_block(text="把刚才那章再分析一遍", ctx=ctx)
    assert "当前焦点文档" in block
    assert "本会话已登记文档" in block
    assert "近期对话" in block
    assert "把刚才那章再分析一遍" in block
