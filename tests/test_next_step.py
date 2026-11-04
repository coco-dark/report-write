from report_writing.chat.next_step import infer_suggested_next_step
from report_writing.domain.models import ChatPhase, ChatSession


def test_intent_suggests_match_or_goal():
    session = ChatSession(id="s1", phase=ChatPhase.INTENT.value)
    step = infer_suggested_next_step(session)
    assert step.get("label")
    tools = step.get("priority_tools") or []
    assert "ask_entry_path" in tools
    assert "match_blueprints" not in tools or tools.index("ask_entry_path") < tools.index(
        "match_blueprints"
    )


def test_freeform_ask_suggests_meta():
    session = ChatSession(id="s1", phase=ChatPhase.FREEFORM_ASK.value)
    step = infer_suggested_next_step(session)
    assert "写什么" in step.get("label") or "叫什么" in step.get("label") or "标题" in step.get("label")
    assert "submit_freeform_meta" in (step.get("priority_tools") or [])


def test_ready_to_write_suggests_start():
    session = ChatSession(id="s1", phase=ChatPhase.READY_TO_WRITE.value, task_id="t1")
    step = infer_suggested_next_step(session)
    assert "写作" in step.get("label", "")
    assert "start_writing" in (step.get("priority_tools") or [])
