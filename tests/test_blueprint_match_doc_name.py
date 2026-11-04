"""蓝本匹配与文档定名顺序。"""

from __future__ import annotations

from report_writing.chat.blueprint_actions import finish_blueprint_match
from report_writing.chat.intent_clarify import get_pending
from report_writing.chat.session_documents import (
    is_writing_document_named,
    user_wants_existing_blueprint_match,
)
from report_writing.domain.models import ChatPhase, ChatSession


class _Bp:
    def __init__(self, pid: str, title: str = ""):
        self.id = pid
        self.title = title or pid
        self.category = ""
        self.tags = []
        self.chapter_count = 0
        self.outline_confirmed = False
        self.description = ""
        self.enabled = True


def test_user_wants_existing_blueprint_match_phrase():
    assert user_wants_existing_blueprint_match("给我写份安全报告 用现在有的蓝本")
    assert not user_wants_existing_blueprint_match("帮我写一份述职报告")


def test_finish_match_appends_doc_name_clarify(tmp_path):
    session = ChatSession(id="s_bp", phase=ChatPhase.INTENT.value)
    msg = "给我写份安全报告 用现在有的蓝本"
    matches = [_Bp("bp1", "1030安全报告"), _Bp("bp2", "1031-安全报告")]
    reply, cards = finish_blueprint_match(
        tmp_path,
        session,
        msg,
        "我按关键词找了 2 份可能相关的。",
        matches,
    )
    assert not is_writing_document_named(session)
    pending = get_pending(session) or {}
    assert pending.get("source") != "doc_name"
    assert not any(
        str((getattr(c, "payload", {}) or {}).get("source") or "") == "doc_name"
        for c in cards
    )
    assert "先给这份文档起个名字" not in (reply or "")
