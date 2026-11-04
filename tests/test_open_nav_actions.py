from report_writing.chat.cards import _nav_action_card
from report_writing.chat.gates import _ensure_analysis_choice_cards
from report_writing.chat.writing_control_route import apply_writing_control_classification
from report_writing.controller import ensure_task
from report_writing.domain.models import (
    ChatPhase,
    ChatSession,
    ElementLibraryItem,
    OutlineNode,
    OutlineTree,
)
from report_writing.services.element_library import save_library_items
from report_writing.services.store import TaskStore


def test_try_writing_control_open_nav_lists_elements(tmp_path):
    save_library_items(
        tmp_path,
        [
            ElementLibraryItem(id="lib-el-1", name="工程名称", kind="kv"),
            ElementLibraryItem(id="lib-el-2", name="站址检索", kind="rag_search"),
        ],
        sync_tasks=False,
    )
    session = ChatSession(id="s1", phase="intent")

    reply, cards, _ = apply_writing_control_classification(
        tmp_path,
        session,
        "查看要素",
        {"tool": "open_element_library", "confidence": 0.9, "payload": {}},
    )
    assert "个人库" in reply or "要素" in reply
    assert cards and cards[0].type == "nav_action"
    assert cards[0].payload["target"] == "elements"
    sections = cards[0].payload.get("sections") or []
    assert any(s.get("key") == "library" for s in sections)
    lib_names = []
    for sec in sections:
        if sec.get("key") == "library":
            lib_names = [x.get("name") for x in sec.get("items") or []]
    assert "工程名称" in lib_names
    assert "站址检索" in lib_names

    reply2, cards2, _ = apply_writing_control_classification(
        tmp_path,
        session,
        "查看知识库",
        {"tool": "open_knowledge", "confidence": 0.9, "payload": {}},
    )
    assert "资料库" in reply2
    assert cards2 and cards2[0].type == "nav_action"
    assert cards2[0].payload["target"] == "materials"


def test_nav_action_with_items_skips_chapter_picker(tmp_path):
    """查看要素回合不得夹带「选择要写的章节」。"""
    tid = "bp1"
    path = tmp_path / "tasks" / tid
    ensure_task(path)
    task = TaskStore(path).load()
    task.title = "安全预评价"
    task.outline = OutlineTree(
        chapters=[
            OutlineNode(key="c1", sequence="1", title="概况"),
            OutlineNode(key="c2", sequence="2", title="未写"),
        ]
    )
    # 仅一章有要点 → 另一章未分析，会触发 analysis gate
    task.outline.chapters[0].points = ["写概况"]
    TaskStore(path).save(task)
    session = ChatSession(
        id="s1",
        phase=ChatPhase.CREATE_HANDOFF.value,
        blueprint_id=tid,
        task_id=tid,
    )
    nav = _nav_action_card(
        target="elements",
        title="查看要素",
        hint="x",
        items=[{"id": "el1", "name": "A", "kind": "kv", "source": "library"}],
        total=1,
    )
    out = _ensure_analysis_choice_cards(tmp_path, session, [nav])
    assert len(out) == 1
    assert out[0].type == "nav_action"
    assert not any(c.type == "chapter_picker" for c in out)


def test_nav_action_card_shape():
    card = _nav_action_card(
        target="elements",
        title="查看要素",
        hint="x",
        items=[{"id": "1", "name": "n", "kind": "kv"}],
        total=1,
    )
    assert card.type == "nav_action"
    assert card.payload["action"] == "open_element_library"
    assert card.payload["items"][0]["name"] == "n"
    card2 = _nav_action_card(target="knowledge", title="查看知识库")
    assert card2.payload["target"] == "materials"
    assert card2.payload["action"] == "open_knowledge"
    card3 = _nav_action_card(target="materials", title="查看资料库")
    assert card3.payload["action"] == "open_materials_library"
