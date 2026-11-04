"""已改为 LLM 路由；保留占位避免旧 import 断裂。"""

from report_writing.chat.intent_llm import WRITING_CONTROL_TOOLS


def test_writing_control_tools_include_add_element():
    assert "add_chapter_element" in WRITING_CONTROL_TOOLS
