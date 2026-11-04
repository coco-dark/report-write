from report_writing.chat.wizard_loop_guard import (
    clamp_wizard_step_regression,
    normalized_wizard_step,
    should_skip_redundant_wizard_card,
)


def test_clamp_blocks_regress_to_upload_without_reupload_cmd():
    out = clamp_wizard_step_regression("outline", "upload")
    assert normalized_wizard_step(out) == "outline"


def test_clamp_blocks_regress_to_meta():
    out = clamp_wizard_step_regression("upload", "meta")
    assert normalized_wizard_step(out) == "upload"


def test_skip_redundant_wizard_card():
    wiz = {"step": "outline_confirm", "blueprint_id": "bp1"}
    payload = {"step": "outline", "blueprint_id": "bp1"}
    assert should_skip_redundant_wizard_card(wiz, payload) is True
    payload["trigger_outline_extract"] = True
    assert should_skip_redundant_wizard_card(wiz, payload) is False


def test_skip_redundant_allows_card_with_chapters():
    wiz = {"step": "outline_confirm", "blueprint_id": "bp1"}
    payload = {"step": "outline_confirm", "blueprint_id": "bp1", "chapters": [{"key": "a", "title": "x"}]}
    assert should_skip_redundant_wizard_card(wiz, payload) is False


def test_same_step_upload_card_not_skipped():
    wiz = {"step": "upload", "blueprint_id": "bp1"}
    payload = {"step": "upload", "blueprint_id": "bp1", "title": "工程安全预评价"}
    assert should_skip_redundant_wizard_card(wiz, payload) is False


def test_stale_meta_card_skipped_when_session_on_upload():
    wiz = {"step": "upload", "blueprint_id": "bp1"}
    payload = {"step": "meta", "blueprint_id": "bp1", "title": "x"}
    assert should_skip_redundant_wizard_card(wiz, payload) is True


def test_clear_stale_begin_study_when_continue():
    from report_writing.chat.wizard_loop_guard import clear_stale_begin_study_trigger

    wiz = {"trigger_begin_study": True, "trigger_chapter_study": ["c1"]}
    clear_stale_begin_study_trigger(wiz, {"study_mode": "continue", "study_analyzed": 3})
    assert "trigger_begin_study" not in wiz
    assert wiz.get("trigger_chapter_study") == ["c1"]


def test_clear_stale_begin_study_when_complete():
    from report_writing.chat.wizard_loop_guard import clear_stale_begin_study_trigger

    wiz = {"trigger_begin_study": True, "trigger_chapter_study": ["c1"]}
    clear_stale_begin_study_trigger(wiz, {"study_mode": "complete", "study_analyzed": 10})
    assert "trigger_begin_study" not in wiz
    # 指定章重学 trigger 必须保留，否则「再分析已学章」会只回文案不开跑
    assert wiz.get("trigger_chapter_study") == ["c1"]


def test_empty_bid_meta_payload_is_behind_upload_session():
    from report_writing.chat.wizard_loop_guard import normalized_wizard_step
    from report_writing.chat.wizard_state import wizard_step_index

    live_step = normalized_wizard_step("upload")
    base_step = normalized_wizard_step("meta")
    assert wizard_step_index(live_step) > wizard_step_index(base_step)
