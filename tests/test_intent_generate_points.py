import json

from report_writing.chat.freeform_points import wants_generate_points


def _invoke(system: str, user: str) -> str:
    if "无蓝本写作流程的动作分类器" in system:
        return json.dumps({"intent": "generate_points", "confidence": 0.9}, ensure_ascii=False)
    return "{}"


def test_wants_generate_points_phrases():
    assert wants_generate_points("你帮我生成写作要点吧", invoke=_invoke)
    assert wants_generate_points("我也不知道怎么写 你帮我写吧", invoke=_invoke)
