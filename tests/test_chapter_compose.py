from report_writing.agents.supervisor import execute_chapter_search
from report_writing.agents.write import run_write
from report_writing.domain.boilerplate import (
    BoilerplateResult,
    apply_boilerplate_to_node,
    extract_source_from_message,
    heuristic_boilerplate,
)
from report_writing.domain.chapter_compose import (
    MATERIALS_HANDOFF_NOTE,
    MATERIALS_OMIT_NOTE,
    fill_boilerplate_slots,
    normalize_compose,
    try_compose_draft,
)
from report_writing.domain.models import (
    ChapterRequirement,
    ChapterState,
    ChapterStatus,
    ExtractItem,
)


def test_strategy_prompt_blocks_cover_five_modes():
    from report_writing.domain.compose_analysis import (
        analysis_strategy_block,
        reference_usage_hint,
        write_strategy_block,
    )

    study = analysis_strategy_block()
    assert "fixed" in study
    assert "不要生成 aux_prompt" in study or "禁止生成 aux_prompt" in study
    assert "章节说明" in study
    assert "组成部分" in study
    assert "【组成部分】" in study
    from report_writing.domain.compose_analysis import chapter_anatomy_block

    anatomy = chapter_anatomy_block()
    assert "怎么写" in anatomy
    assert "版式" in anatomy
    from report_writing.domain.compose_analysis import write_craft_block

    craft = write_craft_block()
    assert "组成部分" in craft
    assert "把 points 原文当小标题" in craft
    write = write_strategy_block(compose="auto_generate")
    assert "成稿章法" in write
    from report_writing.domain.compose_analysis import format_describes_as_points, fallback_describes

    numbered = fallback_describes("岗位职责", [])
    assert numbered.startswith("1.")
    assert "\n2." in numbered
    assert format_describes_as_points("语气正式。条目清晰。").startswith("1.")
    assert "auto_generate" in write
    assert "严禁整段整句照抄" in write or "不依赖项目资料" in write
    assert reference_usage_hint("fixed", has_reference=True).startswith("verbatim_copy")
    assert "slot_fill" in reference_usage_hint("hybrid", has_reference=True)


def test_normalize_compose_aliases():
    assert normalize_compose("SOURCE_ONLY") == "source_only"
    assert normalize_compose("generate", presence="if_materials") == "source_only"
    assert normalize_compose("template") == "template_fill"
    assert normalize_compose("verbatim") == "fixed"
    assert normalize_compose("固定内容输出") == "fixed"
    assert normalize_compose("generate") == "hybrid"
    assert normalize_compose("HYBRID") == "hybrid"
    assert normalize_compose("AUTO_GENERATE") == "auto_generate"
    assert normalize_compose("不依赖资料") == "auto_generate"


def test_heuristic_boilerplate_replaces_slot_values():
    text = heuristic_boilerplate(
        "工程名称为襄阳连云变电站。电压等级为220千伏。",
        [{"name": "工程名称"}, {"name": "电压等级"}],
    )
    assert "{{工程名称}}" in text
    assert "{{电压等级}}" in text
    assert "襄阳连云" not in text


def test_apply_boilerplate_stores_html():
    class Node:
        extracts = []
        compose = "hybrid"
        fixed_output = False
        reference_text = ""

    node = Node()
    apply_boilerplate_to_node(
        node,
        BoilerplateResult(
            text="工程名称：{{工程名称}}。\n电压等级：{{电压等级}}。",
            slots=[{"name": "工程名称"}],
        ),
    )
    assert node.compose == "hybrid"
    assert "<p>" in node.reference_text
    assert "{{工程名称}}" in node.reference_text


def test_extract_source_from_message_keeps_pasted_body():
    assert extract_source_from_message("帮我生成套话") == ""
    pasted = extract_source_from_message("原文：本项目位于武汉市洪山区，总投资1200万元。")
    assert "武汉市洪山区" in pasted


def test_fill_boilerplate_slots_leaves_unfilled_empty():
    text = "本项目位于{{项目地址}}。本工程无此项评价。事故类型为{{事故类型}}。"
    filled, missing = fill_boilerplate_slots(
        text,
        {"项目地址": "武汉市"},
        required_names={"事故类型"},
    )
    assert "武汉市" in filled
    assert "事故类型" in missing
    assert "无此项评价" in filled
    assert "{{事故类型}}" not in filled
    assert "待补充" not in filled


def test_template_compose_fills_without_llm():
    req = ChapterRequirement(
        key="c1",
        title="概况",
        compose="template_fill",
        reference_text="工程名称：{{工程名称}}。电压等级：{{电压等级}}。",
        extracts=[
            ExtractItem(name="工程名称", required=True),
            ExtractItem(name="电压等级", required=False),
        ],
        element_results=[{"id": "e1", "name": "工程名称", "value": "连云变电站工程"}],
    )
    draft = try_compose_draft(req)
    assert draft is not None
    body = "".join(p.text for p in draft.paragraphs)
    assert "连云变电站工程" in body
    assert "待补充" not in body
    assert "{{电压等级}}" not in body


def test_source_only_handoff_when_required_slot_missing():
    chapter = ChapterState(key="c1", title="事故专篇")
    requirement = ChapterRequirement(
        key="c1",
        title="事故专篇",
        presence="if_materials",
        compose="generate",
        points=["写事故经过"],
        extracts=[ExtractItem(name="事故案例", required=True)],
        element_results=[],
    )
    events = []

    def emit(step, status, ch="", title="", payload=None):
        events.append((step, status, payload or {}))

    result = execute_chapter_search(chapter, requirement, None, None, None, emit)
    assert result.get("skipped") is True
    assert result.get("handoff") is True
    assert MATERIALS_HANDOFF_NOTE in (chapter.human_note or "")
    assert chapter.status.value == "needs_human"


def test_auto_generate_writes_without_materials():
    chapter = ChapterState(key="c1", title="编制说明")
    requirement = ChapterRequirement(
        key="c1",
        title="编制说明",
        compose="auto_generate",
        points=["说明编制目的与适用范围"],
        element_results=[],
    )
    events = []

    def emit(step, status, ch="", title="", payload=None):
        events.append((step, status, payload or {}))

    result = execute_chapter_search(chapter, requirement, None, None, None, emit)
    assert result.get("skipped") is False
    assert result.get("fallback_write") is True
    assert chapter.status.value == "writing"
    draft = run_write(requirement, "", [], knowledge_fallback=True)
    assert draft.paragraphs
    body = "".join(p.text for p in draft.paragraphs)
    assert "编制" in body or "说明" in body


def test_template_fill_writes_when_slot_filled():
    chapter = ChapterState(key="c1", title="概况")
    requirement = ChapterRequirement(
        key="c1",
        title="概况",
        compose="template_fill",
        reference_text="项目为{{工程名称}}。",
        extracts=[ExtractItem(name="工程名称", required=True)],
        element_results=[{"id": "e1", "name": "工程名称", "value": "示范工程"}],
    )
    events = []

    def emit(step, status, ch="", title="", payload=None):
        events.append((step, status, payload or {}))

    result = execute_chapter_search(chapter, requirement, None, None, None, emit)
    assert result.get("skipped") is False
    assert chapter.status.value == "writing"
    draft = run_write(requirement, "", [])
    assert "示范工程" in "".join(p.text for p in draft.paragraphs)


def test_child_omitted_when_parent_omitted():
    parent = ChapterState(key="p", title="专篇", status=ChapterStatus.SKIPPED, human_note=MATERIALS_OMIT_NOTE)
    child = ChapterState(key="c", title="专篇子节")
    requirement = ChapterRequirement(key="c", title="专篇子节", parent="p", points=["写一点"])
    events = []

    def emit(step, status, ch="", title="", payload=None):
        events.append((step, status, payload or {}))

    result = execute_chapter_search(
        child,
        requirement,
        None,
        None,
        None,
        emit,
        chapters=[parent, child],
    )
    assert result.get("skipped") is True
    assert result.get("omitted") is True
