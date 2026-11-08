from __future__ import annotations

from report_writing.chat.entry_sample import guess_report_title_from_message, user_brings_own_sample_document


def test_user_brings_own_sample_via_phrase():
    text = "我有样本 帮我写一份安全预评价报告"
    assert user_brings_own_sample_document(text)
    assert not user_brings_own_sample_document("帮我写一份安全预评价报告")


def test_guess_report_title_from_write_message():
    title = guess_report_title_from_message("我有样本 帮我写一份安全预评价报告")
    assert "安全预评价" in title
