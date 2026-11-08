from report_writing.controller.points import _merge_describes_and_style, _polish_keywords


def test_merge_writing_style_into_describes():
    out = _merge_describes_and_style("说明评价依据与适用范围。", ["客观陈述", "先列规范再分条"])
    assert "1." in out
    assert "2." in out
    assert "客观陈述" in out


def test_polish_keywords_strips_long_sentences():
    kws = _polish_keywords(
        "编制依据",
        ["先列出法律法规与导则清单，再说明适用条款。"],
        ["先列出法律法规与导则清单，再说明适用条款。", "环评导则"],
    )
    assert "环评导则" in kws
    assert not any(len(k) > 18 for k in kws)
