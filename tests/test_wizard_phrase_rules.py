"""向导短语规则已弃用意图短切，恒不抢路由。"""

from report_writing.chat.wizard_phrase_rules import (
    message_means_outline_extract_done,
    rule_wizard_action_from_message,
)


def test_outline_done_no_longer_regex_short_circuit():
    assert message_means_outline_extract_done("完成目录提取") is False
    assert (
        rule_wizard_action_from_message(
            "完成目录提取",
            {"step": "outline", "blueprint_id": "bp1"},
        )
        is None
    )
