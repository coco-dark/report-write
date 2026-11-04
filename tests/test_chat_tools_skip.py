"""无蓝本路径：ReAct 工具列表不应暴露 match_blueprints。"""

from report_writing.agents.chat_control import ChatControlState, build_chat_tools
from report_writing.domain.models import ChatSession


def test_skip_path_hides_match_blueprints_tool():
    session = ChatSession(id="s", freeform={"plan": {"goal": "skip_blueprint"}})
    state = ChatControlState(home=None, session=session, robot=None, invoke=None, emit=None)
    names = {getattr(t, "name", None) for t in build_chat_tools(state)}
    assert "match_blueprints" not in names
    assert "list_blueprints" in names
