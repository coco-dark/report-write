"""传入 model 时不再向 live_* 取模型。"""

from __future__ import annotations

from types import SimpleNamespace


class _FakeChatModel:
    def __init__(self, text: str = "ok") -> None:
        self.text = text
        self.invoked = []

    def invoke(self, messages):
        self.invoked.append(messages)
        return SimpleNamespace(content=self.text)


def test_invoke_intent_llm_uses_passed_model(monkeypatch):
    from report_writing.chat.intent_invoke import invoke_intent_llm

    fake = _FakeChatModel('{"ok":true}')
    monkeypatch.setattr(
        "report_writing.infra.llm.live_intent_model",
        lambda **_k: (_ for _ in ()).throw(AssertionError("should not fetch intent model")),
    )
    out = invoke_intent_llm("sys", "user", model=fake)
    assert out == '{"ok":true}'
    assert fake.invoked


def test_chat_invoke_uses_passed_model(monkeypatch):
    from report_writing.api.deps import chat_invoke

    fake = _FakeChatModel("from-param")
    monkeypatch.setattr(
        "report_writing.infra.llm.live_model",
        lambda **_k: (_ for _ in ()).throw(AssertionError("should not fetch live_model")),
    )
    assert chat_invoke("s", "u", model=fake) == "from-param"


def test_invoke_points_batch_uses_passed_model(monkeypatch):
    from report_writing.chat.freeform_points import _invoke_points_batch

    fake = _FakeChatModel("points-json")
    monkeypatch.setattr(
        "report_writing.infra.llm.live_model",
        lambda **_k: (_ for _ in ()).throw(AssertionError("should not fetch live_model")),
    )
    out = _invoke_points_batch(lambda *_a: "fallback", "sys", "user", model=fake)
    assert out == "points-json"
    assert fake.invoked


def test_or_live_model_prefers_argument(monkeypatch):
    from report_writing.infra.llm import or_live_model

    fake = object()
    monkeypatch.setattr(
        "report_writing.infra.llm.live_model",
        lambda **_k: (_ for _ in ()).throw(AssertionError("should not fetch live_model")),
    )
    assert or_live_model(fake) is fake
