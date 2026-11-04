from report_writing.domain.element_draft_from_point import derive_element_draft_from_point


def test_fallback_file_kind_for_document_slot():
    draft, from_llm = derive_element_draft_from_point(
        chapter_title="岗位职责与工作范围",
        sequence="1.1",
        slot="年度目标责任书或绩效考核表",
        point_text="先说明年度目标完成情况",
    )
    assert from_llm is False
    assert draft.get("kind") == "file"
    assert "年度目标" in str(draft.get("name") or "")
    assert str(draft.get("query") or "").strip()


def test_fallback_kv_for_generic_slot():
    draft, _ = derive_element_draft_from_point(
        chapter_title="概述",
        slot="主角姓名",
        point_text="人物介绍",
    )
    assert draft.get("kind") == "kv"
    assert draft.get("kv_pairs")
