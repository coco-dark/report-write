from report_writing.chat.session_goal import (
    apply_session_goal_to_task,
    merge_session_goal,
    session_goal_writing_context,
)
from report_writing.domain.models import ChatSession, OutlineNode, OutlineTree, WritingTask


def test_session_goal_writing_context_from_notes():
    s = ChatSession(
        id="s",
        freeform={"session_goal": {"notes": "我叫李志，岗位后端开发"}},
    )
    assert "李志" in session_goal_writing_context(s)


def test_session_goal_writing_context_omits_project_metadata():
    s = ChatSession(
        id="s",
        freeform={
            "session_goal": {
                "notes": "语气正式、分条陈述",
                "project": "××±800kV 换流站工程",
                "doc_category": "可研报告",
                "doc_title": "站址选择",
            }
        },
    )
    ctx = session_goal_writing_context(s)
    assert "语气正式" in ctx
    assert "可研报告" in ctx
    assert "换流站" not in ctx
    assert "项目：" not in ctx


def test_apply_session_goal_to_task_aux_prompt():
    s = ChatSession(
        id="s",
        freeform={"session_goal": {"notes": "述职人李志，后端开发"}},
    )
    task = WritingTask(
        id="w1",
        outline=OutlineTree(
            chapters=[OutlineNode(key="c1", title="1.1", points=["p"], keywords=["k"])]
        ),
    )
    from report_writing.controller import _ensure_brief_ready

    task.outline_confirmed = True
    _ensure_brief_ready(task)
    assert apply_session_goal_to_task(s, task, force=True)
    req = task.brief.chapter("c1")
    assert "李志" in (req.aux_prompt or "")
    node = task.outline.chapters[0]
    assert "李志" in (node.aux_prompt or "")
    assert "项目：" not in (req.aux_prompt or "")


def test_apply_session_goal_skips_when_doc_title_mismatches_task():
    s = ChatSession(
        id="s2",
        freeform={
            "session_goal": {
                "doc_title": "2026个人述职",
                "doc_category": "述职报告",
                "notes": "述职人李志",
            }
        },
    )
    task = WritingTask(
        id="w_old",
        title="安全预评价报告",
        category="安全预评价",
        outline=OutlineTree(
            chapters=[OutlineNode(key="c1", title="1.1", points=["p"], keywords=["k"])]
        ),
    )
    task.outline.chapters[0].aux_prompt = "原有"
    assert apply_session_goal_to_task(s, task, force=True) is False
    assert "李志" not in (task.outline.chapters[0].aux_prompt or "")
