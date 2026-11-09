"""查看章节信息 tool：列出配置，不改写。"""

from pathlib import Path

from report_writing.chat.actions import _action_get_chapter_info, _format_chapter_info_reply
from report_writing.domain.models import (
    ChapterState,
    ChapterStatus,
    ChatSession,
    OutlineNode,
    OutlineTree,
    WritingTask,
)
from report_writing.services.store import TaskStore


def test_format_chapter_info_lists_points_and_strategy():
    info = {
        "title": "评价单元划分",
        "sequence": "7.1",
        "structure_only": False,
        "compose": "hybrid",
        "compose_label": "混合 (HYBRID)",
        "presence": "always",
        "points": ["写清划分原则", "列出单元表"],
        "point_items": [],
        "describes": "1. 先原则后表格",
        "aux_prompt": "",
        "reference_text": "<p>样板</p>",
        "element_ids": [],
        "element_names": [],
        "status": "confirmed",
        "has_body": True,
    }
    reply = _format_chapter_info_reply(WritingTask(id="t"), "c1", info)
    assert "7.1" in reply
    assert "混合" in reply
    assert "写清划分原则" in reply
    assert "章节说明" in reply
    assert "补充提示词：未配置" in reply
    assert "正文：已有" in reply


def test_action_get_chapter_info_dumps_config(tmp_path: Path):
    tid = "w-info"
    task = WritingTask(
        id=tid,
        title="环评",
        outline=OutlineTree(
            chapters=[
                OutlineNode(
                    key="c1",
                    sequence="7.1",
                    title="评价单元划分",
                    points=["写清划分原则"],
                    point_items=[{"id": "p0", "text": "写清划分原则"}],
                    describes="1. 分点写格式",
                    aux_prompt="",
                    compose="hybrid",
                )
            ]
        ),
        chapters=[
            ChapterState(key="c1", title="评价单元划分", status=ChapterStatus.CONFIRMED)
        ],
    )
    store = TaskStore(tmp_path / "tasks" / tid)
    store.save(task)
    session = ChatSession(id="s-info", task_id=tid)
    reply, cards = _action_get_chapter_info(
        tmp_path, session, {"task_id": tid, "chapter_key": "c1"}
    )
    assert "评价单元划分" in reply
    assert "写清划分原则" in reply
    assert "1. 分点写格式" in reply
    types = [c.type for c in cards]
    assert "doc_window_hint" in types
    hint = next(c for c in cards if c.type == "doc_window_hint")
    assert str(hint.payload.get("chapter_key") or "") == "c1"
    assert str(hint.payload.get("tab") or "") == "config"
