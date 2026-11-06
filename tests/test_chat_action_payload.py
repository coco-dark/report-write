"""对话入参实体：已填字段可当 Mapping，未填字段不当作出现过。"""

from report_writing.chat.session_goal import get_session_goal, merge_session_goal
from report_writing.domain.models import ChatActionPayload, ChatRequest, ChatSession, SessionGoal, WritingPace


def test_parse_json_and_dict():
    payload = ChatActionPayload.parse('{"blueprint_id":"bp1","chapter_keys":["c1"]}')
    assert payload.blueprint_id == "bp1"
    assert payload.chapter_keys == ["c1"]
    assert ChatActionPayload.parse(payload) is payload
    assert ChatActionPayload.parse(None).blueprint_id == ""


def test_contains_only_set_fields():
    payload = ChatActionPayload.parse({"points": ["a"], "describes": "d"})
    assert "points" in payload
    assert "describes" in payload
    assert "aux_prompt" not in payload
    assert payload.get("aux_prompt") is None
    assert payload.get("skip_empty", True) is True


def test_as_dict_and_mapping():
    payload = ChatActionPayload.parse({"task_id": "w1", "confirm": True})
    dumped = dict(payload)
    assert dumped["task_id"] == "w1"
    assert dumped["confirm"] is True
    assert "q" not in dumped
    merged = payload.with_updates(chapter_keys=["k1"])
    assert merged.chapter_keys == ["k1"]
    assert merged.task_id == "w1"


def test_chat_request_coerces_payload_dict():
    req = ChatRequest(action="select_blueprint", action_payload={"blueprint_id": "bp9"})
    assert isinstance(req.action_payload, ChatActionPayload)
    assert req.action_payload.blueprint_id == "bp9"


def test_parse_wizard_and_pack_fields():
    payload = ChatActionPayload.parse(
        {"wizard_command": "go_study", "step": "study", "pack_id": "p1", "slot_no": 2}
    )
    assert payload.wizard_command == "go_study"
    assert payload.step == "study"
    assert payload.pack_id == "p1"
    assert payload.slot_no == 2
    dumped = payload.as_dict()
    assert dumped["wizard_command"] == "go_study"
    assert "confirm" not in dumped


def test_session_goal_entity_roundtrip():
    session = ChatSession(id="s1")
    merge_session_goal(session, SessionGoal(doc_title="年报", doc_category="报告"))
    goal = get_session_goal(session)
    assert goal["doc_title"] == "年报"
    assert dict(goal)["doc_category"] == "报告"
    assert WritingPace.BATCH.value == "batch"
