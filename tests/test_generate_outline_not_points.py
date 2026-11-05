"""「生成目录」不得误成生成要点 / 「任务大纲不存在」。"""

from __future__ import annotations

import json
from pathlib import Path

from report_writing.chat.actions import _collect_freeform_points
from report_writing.chat.freeform_intent import (
    lexical_freeform_intent,
    utterance_mentions_generate_outline,
    wants_generate_outline,
    wants_generate_points,
)
from report_writing.domain.models import ChatPhase, ChatSession


def test_lexical_generate_outline_beats_points():
    assert lexical_freeform_intent("生成目录") == "generate_outline"
    assert lexical_freeform_intent("帮我生成目录大纲") == "generate_outline"
    assert wants_generate_outline("生成目录", invoke=None) is True
    assert wants_generate_points("生成目录", invoke=None) is False
    assert utterance_mentions_generate_outline("帮我生成目录") is True
    assert utterance_mentions_generate_outline("帮我写一份安全报告") is False


def test_generate_points_without_outline_drafts_outline(tmp_path: Path):
    session = ChatSession(
        id="s-outline-misroute",
        phase=ChatPhase.CONFIRM_EXPORT.value,
        task_id="",
        freeform={
            "meta_confirmed": True,
            "category": "述职报告",
            "title": "默认大纲",
        },
    )

    def invoke(system: str, user: str) -> str:
        if "动作分类" in system or "无蓝本写作流程" in system:
            # 故意误判成要点，验证被纠正/回退
            return json.dumps({"intent": "generate_points", "confidence": 0.99}, ensure_ascii=False)
        return "1 履职综述\n1.1 岗位职责\n2 重点工作\n2.1 推进情况"

    # 无 task / 无大纲：应起草目录，而不是抛「任务大纲不存在」
    reply, cards = _collect_freeform_points(
        tmp_path,
        session,
        "生成目录",
        generate=True,
        invoke=invoke,
    )
    assert "任务大纲不存在" not in reply
    assert (
        "目录" in reply
        or "大纲" in reply
        or "草案" in reply
        or any(getattr(c, "type", "") == "outline_preview" for c in cards)
    )


def test_collect_points_without_task_does_not_skip_blueprint(tmp_path: Path):
    session = ChatSession(
        id="s-pts-notask",
        phase=ChatPhase.FREEFORM_POINTS.value,
        task_id="",
        freeform={"meta_confirmed": True, "title": "述职"},
    )
    reply, cards = _collect_freeform_points(
        tmp_path, session, "先随便写点", invoke=None
    )
    assert "任务大纲不存在" not in reply
    assert "还没有目录" in reply or "大纲" in reply
    assert not cards
    assert not (session.freeform or {}).get("skip_blueprint")
