from __future__ import annotations

import json

from report_writing.chat.entry_sample import guess_report_title_from_message, user_brings_own_sample_document
from report_writing.chat.clarify_llm import classify_entry_routing_llm


def _sample_invoke(system: str, user: str) -> str:
    msg = user.split("用户：", 1)[-1].strip()
    if "入口意图" in system:
        return json.dumps(
            {"intent": "match_blueprints", "confidence": 0.9, "is_vague_write": False},
            ensure_ascii=False,
        )
    if "样本文件" in system and "入口意图" not in system:
        if "样本" in msg or "样板" in msg:
            return json.dumps({"intent": "owns_sample", "confidence": 0.9}, ensure_ascii=False)
        return json.dumps({"intent": "none", "confidence": 0.9}, ensure_ascii=False)
    if "doc_title" in system and "入口意图" not in system:
        if "安全预评价" in msg:
            return json.dumps({"doc_title": "安全预评价报告", "confidence": 0.9}, ensure_ascii=False)
        return json.dumps({"doc_title": "", "confidence": 0.0}, ensure_ascii=False)
    return "{}"


def test_user_brings_own_sample_via_intent():
    text = "我有样本 帮我写一份安全预评价报告"
    assert user_brings_own_sample_document(text, invoke=_sample_invoke)
    assert not user_brings_own_sample_document("帮我写一份安全预评价报告", invoke=_sample_invoke)


def test_guess_report_title_via_intent():
    title = guess_report_title_from_message("我有样本 帮我写一份安全预评价报告", invoke=_sample_invoke)
    assert "安全预评价" in title


def test_entry_routing_upgrades_match_when_sample():
    hit = classify_entry_routing_llm(
        "我有样本 帮我写一份安全预评价报告",
        invoke=_sample_invoke,
    )
    assert hit.get("intent") == "create_blueprint"
    assert hit.get("blueprint_title")
