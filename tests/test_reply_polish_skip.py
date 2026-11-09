from report_writing.chat.helpers import should_skip_reply_polish
from report_writing.domain.models import ChatCard, ChatPhase, ChatSession


def test_skip_polish_when_progress_card():
    session = ChatSession(id="s")
    cards = [ChatCard(type="progress", title="写作进度", payload={})]
    assert should_skip_reply_polish(session, cards)


def test_skip_polish_for_blueprint_list_card():
    session = ChatSession(id="s")
    cards = [ChatCard(type="blueprint_list", title="蓝本", payload={})]
    assert should_skip_reply_polish(session, cards)


def test_allow_polish_for_freeform_ask_without_cards():
    session = ChatSession(id="s", phase=ChatPhase.FREEFORM_ASK.value)
    assert should_skip_reply_polish(session, []) is False


def test_skip_polish_while_writing():
    session = ChatSession(id="s", phase=ChatPhase.WRITING.value)
    assert should_skip_reply_polish(session, []) is True
