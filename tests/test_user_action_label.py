from report_writing.chat.helpers import _user_action_label


def test_rewrite_chapter_instruction():
    label = _user_action_label(
        "retry_chapter",
        {
            "chapter_key": "c11",
            "sequence": "1.1",
            "title": "评价单元划分",
            "_write_mode": "rewrite",
        },
    )
    assert label == "重写章节[1.1 评价单元划分]"


def test_start_write_chapter_instruction():
    label = _user_action_label(
        "retry_chapter",
        {"sequence": "1.1", "chapter_title": "评价单元划分", "_write_mode": "start"},
    )
    assert label == "开始写作章节[1.1 评价单元划分]"


def test_associate_point_instruction():
    label = _user_action_label(
        "associate_elements",
        {
            "sequence": "1.1",
            "title": "评价单元划分",
            "pointSlot": "评价因子",
        },
    )
    assert label == "关联要素[1.1 评价单元划分]要点「评价因子」"


def test_save_config_instruction():
    label = _user_action_label(
        "configure_chapter",
        {"sequence": "2.2", "title": "建设规模"},
    )
    assert label == "保存章节配置[2.2 建设规模]"


def test_start_writing_bulk_instruction():
    label = _user_action_label(
        "start_writing",
        {"chapter_keys": ["a", "b", "c"]},
    )
    assert label == "开始写作（3章）"


def test_generate_points_single_chapter():
    label = _user_action_label(
        "generate_freeform_points",
        {"sequence": "1.1", "title": "评价单元划分", "chapter_keys": ["c11"]},
    )
    assert label == "生成章节[1.1 评价单元划分]的写作要点"


def test_skip_supplement_with_chapter():
    label = _user_action_label(
        "skip_writing_supplement_check",
        {"sequence": "1.1", "title": "评价单元划分", "chapter_keys": ["c11"]},
    )
    assert label == "先这样开写章节[1.1 评价单元划分]"


def test_dismiss_supplement_with_slot():
    label = _user_action_label(
        "dismiss_writing_supplement",
        {"sequence": "1.1", "title": "评价单元划分", "pointSlot": "评价因子"},
    )
    assert label == "忽略待补充项[1.1 评价单元划分]要点「评价因子」"


def test_materials_pack_use_with_name():
    label = _user_action_label("materials_pack_use", {"pack_name": "某某项目资料"})
    assert label == "使用资料包「某某项目资料」"


def test_re_study_with_chapter():
    label = _user_action_label(
        "blueprint_wizard",
        {
            "wizard_command": "re_study_chapters",
            "study_method": "source",
            "sequence": "1.1",
            "title": "评价单元划分",
        },
    )
    assert label == "根据原文分析章节[1.1 评价单元划分]"


def test_placeholder_chapter_title_not_used_as_tag():
    label = _user_action_label(
        "retry_chapter",
        {"chapter_key": "c11", "chapter_title": "本章", "_write_mode": "start"},
    )
    assert label == "开始写作本章"
    assert "[" not in label
