from report_writing.chat.intent_chapter import (
    message_has_chapter_sequence,
    pick_awaiting_confirm_chapter_key,
    pick_focus_chapter_key,
    pick_focus_chapter_key_extended,
    resolve_chapter_key_by_sequence,
    resolve_chapter_key_from_message,
    writing_phase_allows_chapter_tools,
)
from report_writing.domain.models import (
    ChapterState,
    ChapterStatus,
    ChatPhase,
    ChatSession,
    OutlineNode,
    OutlineTree,
    WritingTask,
)


def _task_with_outline() -> WritingTask:
    return WritingTask(
        id="w",
        outline=OutlineTree(
            chapters=[
                OutlineNode(key="k156", sequence="1.5.6", title="工程投资"),
                OutlineNode(key="k11", sequence="1.1", title="背景"),
                OutlineNode(key="k22", sequence="2.2", title="建设规模"),
                OutlineNode(key="k221", sequence="2.2.1", title="站址"),
            ]
        ),
        chapters=[
            ChapterState(key="k156", title="工程投资", status=ChapterStatus.AWAITING_CONFIRM),
        ],
    )


def test_resolve_chapter_key_by_sequence():
    task = WritingTask(
        id="w",
        outline=OutlineTree(
            chapters=[
                OutlineNode(key="k11", sequence="1.1", title="背景"),
                OutlineNode(key="k12", sequence="1.2", title="其他"),
            ]
        ),
    )
    assert resolve_chapter_key_from_message(task, "帮我改写 1.1") == "k11"


def test_resolve_dotted_sequence_exact():
    task = _task_with_outline()
    assert resolve_chapter_key_by_sequence(task, "2.2") == "k22"
    assert resolve_chapter_key_from_message(task, "给2.2章节关联要素") == "k22"
    assert resolve_chapter_key_from_message(task, "为2.2章添加知识库要素，要准确提取关键信息") == "k22"


def test_explicit_sequence_does_not_fallback_to_awaiting_or_prev():
    task = _task_with_outline()
    session = ChatSession(
        id="s",
        phase=ChatPhase.WRITING.value,
        task_id="w",
        freeform={
            "last_turn_snapshot": {
                "writing_task": {"focus_chapter_key": "k156"},
            }
        },
    )
    # 有待确认的 1.5.6，但用户点名 2.2 → 必须是 2.2
    assert pick_focus_chapter_key(task, "给2.2章关联要素") == "k22"
    assert pick_focus_chapter_key_extended(session, task, "给2.2章关联要素，要准确提取关键信息") == "k22"
    # 点名不存在的序号：空，而不是 1.5.6
    assert message_has_chapter_sequence("给9.9章关联要素")
    assert pick_focus_chapter_key_extended(session, task, "给9.9章关联要素") == ""


def test_allow_outline_default_off_skips_first_chapter_fallback():
    """加要素未点名时：禁止落到大纲第一章（不再靠「关联要素」关键词）。"""
    task = WritingTask(
        id="w",
        outline=OutlineTree(
            chapters=[
                OutlineNode(key="k11", sequence="1.1", title="背景"),
                OutlineNode(key="k22", sequence="2.2", title="建设规模"),
            ]
        ),
        chapters=[
            ChapterState(key="k11", title="背景", status=ChapterStatus.CONFIRMED),
            ChapterState(key="k22", title="建设规模", status=ChapterStatus.CONFIRMED),
        ],
    )
    session = ChatSession(id="s-el", phase=ChatPhase.WRITING.value, task_id="w")
    assert pick_focus_chapter_key_extended(session, task, "帮我加一个要素") == "k11"
    assert (
        pick_focus_chapter_key_extended(
            session, task, "帮我加一个要素", allow_outline_default=False
        )
        == ""
    )


def test_pick_awaiting_confirm():
    task = WritingTask(
        id="w",
        chapters=[
            ChapterState(key="c1", title="A", status=ChapterStatus.CONFIRMED),
            ChapterState(key="c2", title="B", status=ChapterStatus.AWAITING_CONFIRM),
        ],
    )
    assert pick_awaiting_confirm_chapter_key(task) == "c2"
    assert pick_focus_chapter_key(task, "好的") == "c2"


def test_writing_phase_allows_tools():
    s = ChatSession(id="s", phase=ChatPhase.WRITING.value, task_id="t1")
    assert writing_phase_allows_chapter_tools(s)
    s2 = ChatSession(id="s2", phase=ChatPhase.INTENT.value, task_id="t1")
    assert not writing_phase_allows_chapter_tools(s2)
