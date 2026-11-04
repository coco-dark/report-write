from report_writing.chat.intent_action_chain import (
    WIZARD_POLICY,
    order_action_ids,
)


def test_wizard_order_confirm_before_study():
    out = order_action_ids(["go_study", "confirm_outline"], WIZARD_POLICY)
    assert out.index("confirm_outline") < out.index("go_study")


def test_wizard_does_not_inject_confirm_for_go_study_alone():
    # 未确认目录时只说「开始学习」应由 go_study 对话提示，勿静默注入 confirm_outline
    out = order_action_ids(["go_study"], WIZARD_POLICY)
    assert out == ["go_study"]


def test_chat_plan_order_start_after_select_chapters():
    from report_writing.chat.intent_action_chain import CHAT_CONTROL_PLAN_POLICY, order_action_ids

    out = order_action_ids(["start_writing", "select_write_chapters", "select_blueprint"], CHAT_CONTROL_PLAN_POLICY)
    assert out.index("select_blueprint") < out.index("select_write_chapters") < out.index("start_writing")

