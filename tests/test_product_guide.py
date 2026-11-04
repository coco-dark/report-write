from report_writing.chat.product_guide import (
    build_usage_guide_reply,
    message_requests_usage_guide,
)


def test_usage_guide_fallback_without_invoke():
    text = build_usage_guide_reply(None)
    assert "蓝本" in text
    assert len(text) > 80


def test_usage_guide_uses_invoke():
    def fake_invoke(_system: str, user: str) -> str:
        assert "事实稿" in user
        return "这是润色后的使用说明，包含蓝本与无蓝本两条路径。"

    text = build_usage_guide_reply(fake_invoke)
    assert "润色" in text or "蓝本" in text


def test_usage_guide_falls_back_on_unconfigured_invoke():
    def stub_invoke(_system: str, _user: str) -> str:
        return "未配置大模型，接入模型后再试。"

    text = build_usage_guide_reply(stub_invoke)
    assert "润色" not in text
    assert "蓝本" in text


def test_message_requests_usage_guide():
    assert message_requests_usage_guide("使用说明")
    assert message_requests_usage_guide("怎么用？")
    assert not message_requests_usage_guide("帮我写述职报告")
