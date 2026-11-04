from report_writing.chat.study_chapter_resolve import (
    keys_for_top_sequences,
    parse_study_sequences_from_message,
    resolve_study_chapter_keys,
)
from report_writing.domain.models import OutlineNode, OutlineTree, WritingTask


def test_parse_range_sequences():
    assert parse_study_sequences_from_message("1到2章重新学习") == ["1", "2"]
    assert parse_study_sequences_from_message("把第3章再学一遍") == ["3"]
    assert parse_study_sequences_from_message("对于第二章节。电力系统部分进行重新提取") == ["2"]
    assert parse_study_sequences_from_message("学习第二章下面的所有子章节") == ["2"]
    assert parse_study_sequences_from_message("学习第二章整章") == ["2"]
    assert parse_study_sequences_from_message("学习2.2章") == ["2.2"]
    assert parse_study_sequences_from_message("学习第2.2章") == ["2.2"]
    assert parse_study_sequences_from_message("学一下2.2") == ["2.2"]


def _sample_task() -> WritingTask:
    return WritingTask(
        id="bp1",
        outline=OutlineTree(
            chapters=[
                OutlineNode(key="c1", sequence="1", title="概述"),
                OutlineNode(key="c2", sequence="2", title="工程概况"),
                OutlineNode(key="c21", sequence="2.1", title="站址"),
                OutlineNode(key="c22", sequence="2.2", title="规模"),
                OutlineNode(key="c221", sequence="2.2.1", title="明细"),
                OutlineNode(key="c3", sequence="3", title="结论"),
            ]
        ),
    )


def test_keys_for_top_sequences_includes_subtree():
    task = _sample_task()
    # 「学习第1到2章」：整棵子树（含顶层）
    keys = keys_for_top_sequences(task, ["1", "2"])
    assert keys == ["c1", "c2", "c21", "c22", "c221"]


def test_keys_for_children_only_excludes_root():
    task = _sample_task()
    keys = keys_for_top_sequences(task, ["2"], children_only=True)
    assert keys == ["c21", "c22", "c221"]


def test_keys_for_self_excludes_children():
    task = _sample_task()
    keys = keys_for_top_sequences(task, ["2"], scope="self")
    assert keys == ["c2"]


def test_keys_for_dotted_sequence():
    task = _sample_task()
    assert keys_for_top_sequences(task, ["2.2"], scope="whole") == ["c22", "c221"]
    assert keys_for_top_sequences(task, ["2.2"], scope="self") == ["c22"]
    assert keys_for_top_sequences(task, ["2.2"], scope="children") == ["c221"]
    # LLM 直接给出 2.2 时也能解析
    assert resolve_study_chapter_keys(task, "学习2.2章", llm_sequences=["2.2"]) == [
        "c22",
        "c221",
    ]


def test_resolve_subchapters_phrase():
    task = _sample_task()
    keys = resolve_study_chapter_keys(
        task, "学习第二章下面的所有子章节", scope="children"
    )
    assert keys == ["c21", "c22", "c221"]


def test_resolve_whole_chapter_phrase():
    task = _sample_task()
    keys = resolve_study_chapter_keys(task, "学习第二章整章")
    assert keys == ["c2", "c21", "c22", "c221"]
    # 「学习第2章」默认也是整章子树
    assert resolve_study_chapter_keys(task, "学习第2章") == ["c2", "c21", "c22", "c221"]
    assert resolve_study_chapter_keys(task, "学习第二章所有") == ["c2", "c21", "c22", "c221"]
    assert resolve_study_chapter_keys(task, "学习2.2章") == ["c22", "c221"]
