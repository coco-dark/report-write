"""同会话多文档：resolve_task_id / 登记表 / 待办过滤。"""

from __future__ import annotations

from pathlib import Path

import pytest

from report_writing.domain.models import ChatSession
from report_writing.chat.session_documents import (
    bind_session_focus_to_task,
    filter_pending_by_task,
    message_explicitly_creates_blueprint,
    message_wants_new_document_strong,
    pop_pending_entry,
    quick_title_from_write_message,
    resolve_task_id,
    session_documents_map,
    stash_pending_entry,
    upsert_session_document,
)


def test_quick_title_and_pending_entry_for_new_write():
    assert quick_title_from_write_message("在帮我写一份述职报告") == "述职报告"
    assert not message_wants_new_document_strong("在帮我写一份述职报告")
    assert not message_explicitly_creates_blueprint("帮我写一份述职报告")
    assert message_explicitly_creates_blueprint("创建蓝本")
    session = ChatSession(id="pending1")
    stash_pending_entry(session, message="帮我写一份述职报告")
    pending = pop_pending_entry(session)
    assert pending["title"] == "述职报告"
    assert "述职报告" in pending["message"]
    assert pop_pending_entry(session) == {}


def test_multi_action_sentence_is_not_document_title():
    from report_writing.chat.freeform_intent import lexical_freeform_intent
    from report_writing.chat.freeform_outline import outline_top_chapter_count
    from report_writing.chat.clarify_flow import split_doc_name_answer
    from report_writing.chat.session_documents import (
        preferred_path_from_write_message,
        sanitize_candidate_document_title,
        title_contains_action_clause,
    )

    msg = "作lzb述职报告 自动生成章节目录 三个大章节就可以"
    assert quick_title_from_write_message(msg) == "lzb述职报告"
    assert sanitize_candidate_document_title(msg, message=msg) == "lzb述职报告"
    assert title_contains_action_clause(msg)
    assert not title_contains_action_clause("lzb述职报告")
    assert outline_top_chapter_count(msg) == 3
    assert lexical_freeform_intent(msg) == "generate_outline"
    assert preferred_path_from_write_message(msg) == "freeform"
    parsed = split_doc_name_answer(msg)
    assert parsed is not None
    assert parsed[0] == "lzb述职报告"

    msg2 = "再给我写一份述职报告 三个章节 名字叫做lzb述职报告"
    assert quick_title_from_write_message(msg2) == "lzb述职报告"
    assert outline_top_chapter_count(msg2) == 3
    assert preferred_path_from_write_message(msg2) == "freeform"

    msg3 = "帮我写一份安全报告 用现在有的蓝本"
    assert quick_title_from_write_message(msg3) == "安全报告"
    assert preferred_path_from_write_message(msg3) == "match"
    assert title_contains_action_clause(msg3)
    assert not title_contains_action_clause("安全报告")


def test_resolve_task_id_prefers_payload_over_session_focus():
    session = ChatSession(id="s1", task_id="w_old", blueprint_id="bp_old")
    assert resolve_task_id(session, {"task_id": "bp_new"}) == "bp_new"
    assert resolve_task_id(session, {"blueprint_id": "bp_b"}) == "bp_b"
    assert resolve_task_id(session, {}) == "w_old"
    session.task_id = ""
    assert resolve_task_id(session, {}) == "bp_old"


def test_resolve_task_id_require_raises():
    session = ChatSession(id="s2")
    with pytest.raises(ValueError):
        resolve_task_id(session, {}, require=True)


def test_upsert_session_documents_keeps_multiple_blueprints():
    session = ChatSession(id="s3")
    upsert_session_document(
        session, kind="blueprint", task_id="bpA", blueprint_id="bpA", title="蓝本A"
    )
    upsert_session_document(
        session, kind="blueprint", task_id="bpB", blueprint_id="bpB", title="蓝本B"
    )
    docs = session_documents_map(session)
    assert "bp:bpA" in docs
    assert "bp:bpB" in docs
    assert docs["bp:bpA"]["title"] == "蓝本A"
    assert docs["bp:bpB"]["title"] == "蓝本B"


def test_bind_writing_clone_does_not_overwrite_other_blueprint_writing_id():
    session = ChatSession(id="s4", blueprint_id="bpA")
    upsert_session_document(
        session, kind="blueprint", task_id="bpA", blueprint_id="bpA", title="A"
    )
    upsert_session_document(
        session, kind="blueprint", task_id="bpB", blueprint_id="bpB", title="B"
    )
    bind_session_focus_to_task(
        session,
        "wCloneA",
        blueprint_id="bpA",
        writing_task_id="wCloneA",
        title="A写作",
    )
    docs = session_documents_map(session)
    assert docs["bp:bpA"]["writing_task_id"] == "wCloneA"
    assert docs["bp:bpB"].get("writing_task_id") in ("", None)


def test_bind_blueprint_focus_preserves_writing_clone_for_materials_pack():
    """回落蓝本焦点时保留写作副本，避免文档窗资料包（挂在 clone 上）消失。"""
    session = ChatSession(id="s_pack", blueprint_id="bpPack", task_id="wPackClone")
    upsert_session_document(
        session,
        kind="blueprint",
        task_id="bpPack",
        blueprint_id="bpPack",
        writing_task_id="wPackClone",
        title="1103安全",
    )
    bind_session_focus_to_task(
        session,
        "bpPack",
        blueprint_id="bpPack",
        title="1103安全",
    )
    docs = session_documents_map(session)
    assert docs["bp:bpPack"]["writing_task_id"] == "wPackClone"
    # 显式清空仍可用
    bind_session_focus_to_task(
        session,
        "bpPack",
        blueprint_id="bpPack",
        writing_task_id="",
        title="1103安全",
    )
    docs = session_documents_map(session)
    assert docs["bp:bpPack"].get("writing_task_id") in ("", None)


def test_filter_pending_by_task():
    pending = [
        {"pending_id": "1", "task_id": "bpA", "element": {"id": "e1"}},
        {"pending_id": "2", "task_id": "bpB", "element": {"id": "e2"}},
        {"pending_id": "3", "task_id": "wCloneA", "element": {"id": "e3"}},
    ]
    only_a = filter_pending_by_task(pending, "bpA", blueprint_id="bpA")
    assert [p["pending_id"] for p in only_a] == ["1"]
    with_clone = filter_pending_by_task(pending, "wCloneA", blueprint_id="bpA")
    assert {p["pending_id"] for p in with_clone} == {"1", "3"}


def test_match_session_document_by_title_in_message():
    session = ChatSession(id="s_match")
    upsert_session_document(
        session, kind="blueprint", task_id="bpA", blueprint_id="bpA", title="安全预评价-A"
    )
    upsert_session_document(
        session, kind="blueprint", task_id="bpB", blueprint_id="bpB", title="职业病危害评价"
    )
    from report_writing.chat.session_documents import (
        enrich_payload_with_message_document,
        match_session_document,
    )

    hit = match_session_document(session, "帮我分析一下安全预评价那份的第2章")
    assert hit is not None
    assert hit.get("blueprint_id") == "bpA"
    body = enrich_payload_with_message_document(
        session, "把职业病危害评价文档的2.1章配置一下", {}
    )
    assert body.get("blueprint_id") == "bpB"


def test_match_session_document_switch_to_quarterly_report():
    session = ChatSession(id="s_q")
    upsert_session_document(
        session,
        kind="writing",
        task_id="w_overview",
        title="依据样本-概述",
        clear_blueprint_id=True,
    )
    upsert_session_document(
        session,
        kind="writing",
        task_id="w_quarter",
        title="我的季度报告",
        clear_blueprint_id=True,
    )
    from report_writing.chat.session_documents import match_session_document

    hit = match_session_document(session, "切换到我的季度报告")
    assert hit is not None
    assert hit.get("task_id") == "w_quarter"


def test_message_wants_new_document_strong():
    # 启发式仅作无模型窄兜底；口语变化走 resolve_document_scope + LLM
    assert message_wants_new_document_strong("再写一份安全预评价")
    assert message_wants_new_document_strong("正在写A，帮我再写一份安全预评价")
    assert message_wants_new_document_strong("重写帮我写一份安全报告")
    assert message_wants_new_document_strong("重新写一份述职报告")
    assert message_wants_new_document_strong("新写一份述职报告")
    assert message_wants_new_document_strong(
        "再根据这个蓝本帮我写一份新的安全报告 名字叫新的安全报告"
    )
    assert not message_wants_new_document_strong("帮我写一份职业病危害评价")
    assert not message_wants_new_document_strong("继续分析第2章")
    # 裸「重写」留给退回重写本章，不算新开文档
    assert not message_wants_new_document_strong("这一章退回重写")
    assert quick_title_from_write_message("重写帮我写一份安全报告") == "安全报告"


def test_resolve_document_scope_heuristic_fallback():
    from report_writing.chat.session_documents import resolve_document_scope

    session = ChatSession(id="scope1")
    session.freeform = {"title": "lzb安全报告", "meta_confirmed": True, "writing_doc_named": True}
    out = resolve_document_scope(session, "重写帮我写一份安全报告", invoke=None)
    assert out["force_new"] is True
    assert out["action"] == "force_new"
    assert out["source"] in {"heuristic", "heuristic_strong"}
    assert "安全" in str(out.get("title") or "")


def test_clear_writing_document_named_clears_meta_confirmed():
    from report_writing.chat.session_documents import (
        clear_writing_document_named,
        is_writing_document_named,
    )

    session = ChatSession(id="named_clear")
    session.freeform = {
        "title": "lzb安全报告",
        "meta_confirmed": True,
        "writing_doc_named": True,
    }
    assert is_writing_document_named(session)
    clear_writing_document_named(session)
    assert not is_writing_document_named(session)


def test_focus_document_brief_and_dialogue_tags():
    from report_writing.chat.dialogue import format_full_dialogue
    from report_writing.chat.session_documents import focus_document_brief, set_focus_document_key
    from report_writing.domain.models import ChatMessage

    session = ChatSession(id="s_focus", blueprint_id="bpA", task_id="")
    upsert_session_document(
        session, kind="blueprint", task_id="bpA", blueprint_id="bpA", title="安全预评价-A"
    )
    upsert_session_document(
        session, kind="blueprint", task_id="bpB", blueprint_id="bpB", title="职业病危害评价"
    )
    set_focus_document_key(session, "bp:bpA")
    brief = focus_document_brief(session)
    assert "安全预评价" in brief
    assert "bp:bpA" in brief

    session.messages = [
        ChatMessage(role="user", text="继续写第1章", document_key="bp:bpA"),
        ChatMessage(role="assistant", text="好的", document_key="bp:bpA"),
        ChatMessage(role="user", text="帮我分析职业病那份第2章", document_key="bp:bpB"),
        ChatMessage(role="assistant", text="已切换", document_key="bp:bpB"),
    ]
    rows = format_full_dialogue(session, max_turns=10)
    texts = [r["text"] for r in rows]
    assert any("文档切换" in t and "职业病" in t for t in texts)
    assert any("doc_id=bp:bpA" in t for t in texts)
    assert any("doc_id=bp:bpB" in t for t in texts)
    assert any(r.get("document_id") == "bp:bpA" for r in rows)
    assert any(r.get("document_id") == "bp:bpB" for r in rows)


def test_begin_force_new_keeps_other_documents(tmp_path: Path):
    from report_writing.chat.resource_actions import _begin_blueprint_wizard

    session = ChatSession(id="s_new", blueprint_id="bpA", task_id="wA")
    upsert_session_document(
        session, kind="blueprint", task_id="bpA", blueprint_id="bpA", title="安全预评价-A"
    )
    reply, cards = _begin_blueprint_wizard(
        session,
        title_hint="职业病危害评价",
        home=tmp_path,
        force_new=True,
        user_message="帮我另写一份职业病危害评价",
    )
    docs = session_documents_map(session)
    assert "bp:bpA" in docs
    assert docs["bp:bpA"]["title"] == "安全预评价-A"
    assert not session.blueprint_id
    assert any(getattr(c, "type", "") == "blueprint_wizard" for c in cards)
    assert "蓝本" in reply or "名称" in reply


def test_ensure_named_freeform_document_registers_title(tmp_path: Path):
    from report_writing.chat.session_documents import (
        ensure_named_freeform_document,
        focus_document_key,
        session_documents_map,
    )

    home = tmp_path
    (home / "tasks").mkdir(parents=True)
    session = ChatSession(id="s_name")
    tid = ensure_named_freeform_document(
        session, home, title="2026个人述职", category="述职报告"
    )
    assert tid
    assert session.task_id == tid
    docs = session_documents_map(session)
    assert f"task:{tid}" in docs
    assert docs[f"task:{tid}"]["title"] == "2026个人述职"
    assert focus_document_key(session) == f"task:{tid}"
    assert (session.freeform or {}).get("title") == "2026个人述职"


def test_ensure_named_freeform_creates_second_doc_on_title_conflict(tmp_path: Path):
    from report_writing.chat.session_documents import (
        ensure_named_freeform_document,
        session_documents_map,
    )

    home = tmp_path
    (home / "tasks").mkdir(parents=True)
    session = ChatSession(id="s_multi")
    tid1 = ensure_named_freeform_document(
        session, home, title="安全预评价报告", category="评价"
    )
    tid2 = ensure_named_freeform_document(
        session, home, title="职业病危害评价", category="评价"
    )
    assert tid1 and tid2 and tid1 != tid2
    docs = session_documents_map(session)
    assert docs[f"task:{tid1}"]["title"] == "安全预评价报告"
    assert docs[f"task:{tid2}"]["title"] == "职业病危害评价"
    assert session.task_id == tid2


def test_confirm_second_doc_name_does_not_rename_first(tmp_path: Path):
    """文档2定名不得改写 task:tid1 的 title（先 confirm 再 ensure 的旧序回归）。"""
    from report_writing.chat.session_documents import (
        confirm_writing_document_name,
        ensure_named_freeform_document,
        session_documents_map,
    )
    from report_writing.services.store import TaskStore

    home = tmp_path
    (home / "tasks").mkdir(parents=True)
    session = ChatSession(id="s_confirm_iso")
    tid1 = ensure_named_freeform_document(
        session, home, title="2026工程安全报告", category="安全报告"
    )
    assert tid1
    # 模拟旧 bug：在 ensure 新文档前对仍指向 tid1 的会话 confirm 新名
    confirm_writing_document_name(
        session,
        "2026季度述职报告",
        home=home,
        category="述职报告",
        message="帮我写季度述职",
    )
    docs_mid = session_documents_map(session)
    assert docs_mid[f"task:{tid1}"]["title"] == "2026工程安全报告"
    t1 = TaskStore(home / "tasks" / tid1).load()
    assert str(t1.title or "") == "2026工程安全报告"

    tid2 = ensure_named_freeform_document(
        session, home, title="2026季度述职报告", category="述职报告"
    )
    assert tid2 and tid2 != tid1
    docs = session_documents_map(session)
    assert docs[f"task:{tid1}"]["title"] == "2026工程安全报告"
    assert docs[f"task:{tid2}"]["title"] == "2026季度述职报告"


def test_apply_focused_document_title_updates_registry(tmp_path: Path):
    from report_writing.chat.session_documents import (
        apply_focused_document_title,
        ensure_named_freeform_document,
        session_documents_map,
    )

    home = tmp_path
    (home / "tasks").mkdir(parents=True)
    session = ChatSession(id="s_rename")
    tid = ensure_named_freeform_document(session, home, title="旧标题", category="报告")
    apply_focused_document_title(session, "新标题", home=home, category="报告")
    docs = session_documents_map(session)
    assert docs[f"task:{tid}"]["title"] == "新标题"


def test_select_same_blueprint_new_document_isolates_writing_copy(tmp_path: Path):
    """另一份文档选用同一库蓝本时，不得继承上一文档的 writing_task_id。"""
    from report_writing.chat import actions as actions_mod
    from report_writing.controller import create_blueprint, ensure_task
    from report_writing.domain.models import OutlineNode, OutlineTree
    from report_writing.services.store import TaskStore

    home = tmp_path
    (home / "tasks").mkdir(parents=True)
    bid = "bpSharedLib"
    path = home / "tasks" / bid
    ensure_task(path)
    create_blueprint(path, title="库蓝本", category="", description="t")
    task = TaskStore(path).load()
    task.outline = OutlineTree(
        chapters=[OutlineNode(key="c1", title="章一", sequence="1", points=["p"])]
    )
    task.outline_confirmed = True
    task.sample_ready = True
    TaskStore(path).save(task)

    session = ChatSession(
        id="s_iso",
        blueprint_id=bid,
        freeform={"title": "文档A", "writing_doc_named": True},
    )
    upsert_session_document(
        session,
        kind="blueprint",
        task_id=bid,
        blueprint_id=bid,
        writing_task_id="wDocAClone",
        title="文档A",
    )
    session.freeform = {
        **dict(session.freeform or {}),
        "title": "文档B",
        "writing_doc_named": True,
    }
    actions_mod._action_select_blueprint(home, session, {"blueprint_id": bid})
    docs = session_documents_map(session)
    assert docs.get(f"bp:{bid}", {}).get("writing_task_id") == "wDocAClone"
    doc_slots = [v for k, v in docs.items() if str(k).startswith("doc:")]
    assert len(doc_slots) == 1
    # 另一文档选用同一库蓝本：新开副本，不继承文档A的 w*
    new_wid = str(doc_slots[0].get("writing_task_id") or "").strip()
    assert new_wid and new_wid != "wDocAClone"
    assert doc_slots[0].get("title") == "文档B"


def test_select_same_blueprint_force_new_same_title_does_not_overwrite(tmp_path: Path):
    """新开一份即便沿用同一文档名，也不得覆盖上一份写作副本。"""
    from report_writing.chat import actions as actions_mod
    from report_writing.chat.session_documents import (
        begin_document_onboarding,
        focus_document_key,
        session_documents_map,
    )
    from report_writing.controller import create_blueprint, ensure_task
    from report_writing.domain.models import OutlineNode, OutlineTree
    from report_writing.services.store import TaskStore

    home = tmp_path
    (home / "tasks").mkdir(parents=True)
    bid = "bpSafetyLib"
    path = home / "tasks" / bid
    ensure_task(path)
    create_blueprint(path, title="我的安全报告", category="", description="t")
    task = TaskStore(path).load()
    task.outline = OutlineTree(
        chapters=[OutlineNode(key="c1", title="章一", sequence="1", points=["旧要点"])]
    )
    task.outline_confirmed = True
    task.sample_ready = True
    TaskStore(path).save(task)

    session = ChatSession(
        id="s_overwrite",
        freeform={"title": "lzb安全报告", "writing_doc_named": True},
    )
    actions_mod._action_select_blueprint(home, session, {"blueprint_id": bid})
    old_wid = str(session.task_id or "").strip()
    assert old_wid.startswith("w")
    old_task = TaskStore(home / "tasks" / old_wid).load()
    old_task.outline.chapters[0].points = ["上一份已写要点"]
    TaskStore(home / "tasks" / old_wid).save(old_task)

    begin_document_onboarding(
        session,
        home,
        message="新开一份安全报告",
        title_hint="lzb安全报告",
        force_new=True,
    )
    shell_tid = str(session.task_id or "").strip()
    assert shell_tid and shell_tid != old_wid
    assert str(focus_document_key(session) or "").startswith("task:")

    actions_mod._action_select_blueprint(home, session, {"blueprint_id": bid})
    new_wid = str(session.task_id or "").strip()
    assert new_wid != old_wid
    kept = TaskStore(home / "tasks" / old_wid).load()
    assert kept.outline.chapters[0].points == ["上一份已写要点"]
    docs = session_documents_map(session)
    old_still = [
        v
        for v in docs.values()
        if str(v.get("writing_task_id") or v.get("task_id") or "") == old_wid
    ]
    assert old_still
    assert docs.get(f"bp:{bid}", {}).get("writing_task_id") == old_wid


def test_apply_explicit_new_document_turn_mints_named_copy(tmp_path: Path):
    from report_writing.chat import actions as actions_mod
    from report_writing.chat.session_documents import (
        apply_explicit_new_document_turn,
        focus_document_key,
        session_documents_map,
    )
    from report_writing.controller import create_blueprint, ensure_task
    from report_writing.domain.models import OutlineNode, OutlineTree
    from report_writing.services.store import TaskStore

    home = tmp_path
    (home / "tasks").mkdir(parents=True)
    bid = "bpLzb"
    path = home / "tasks" / bid
    ensure_task(path)
    create_blueprint(path, title="lzb安全报告", category="", description="t")
    task = TaskStore(path).load()
    task.outline = OutlineTree(
        chapters=[OutlineNode(key="c1", title="章一", sequence="1", points=["p"])]
    )
    task.outline_confirmed = True
    TaskStore(path).save(task)
    session = ChatSession(
        id="s_new_named",
        freeform={"title": "lzb安全报告", "writing_doc_named": True, "meta_confirmed": True},
    )
    actions_mod._action_select_blueprint(home, session, {"blueprint_id": bid})
    old_wid = str(session.task_id or "")
    msg = "再根据这个蓝本帮我写一份新的安全报告 名字叫新的安全报告"
    assert apply_explicit_new_document_turn(session, home, msg)
    new_wid = str(session.task_id or "")
    assert new_wid and new_wid != old_wid
    assert "新的安全报告" in str((session.freeform or {}).get("title") or "")
    assert session.blueprint_id == bid
    docs = session_documents_map(session)
    assert any(
        str(v.get("writing_task_id") or v.get("task_id") or "") == old_wid
        for v in docs.values()
    )
    assert str(focus_document_key(session) or "") != f"bp:{bid}" or new_wid != old_wid


def test_select_blueprint_clears_stale_writing_task(tmp_path: Path, monkeypatch):
    from report_writing.chat import actions as actions_mod
    from report_writing.controller import create_blueprint, ensure_task
    from report_writing.domain.models import OutlineNode, OutlineTree, WritingTask
    from report_writing.services.store import TaskStore
    from report_writing.services.task_ids import is_writing_task_id

    home = tmp_path
    (home / "tasks").mkdir(parents=True)
    bp_a = "bpSelectA"
    bp_b = "bpSelectB"
    for bid, title in ((bp_a, "A"), (bp_b, "B")):
        path = home / "tasks" / bid
        ensure_task(path)
        create_blueprint(path, title=title, category="", description="t")
        task = TaskStore(path).load()
        task.outline = OutlineTree(
            chapters=[
                OutlineNode(key="c1", title="章一", sequence="1", points=["p"]),
            ]
        )
        task.outline_confirmed = True
        task.sample_ready = True
        TaskStore(path).save(task)

    session = ChatSession(
        id="s_sel",
        blueprint_id=bp_a,
        task_id="wStaleClone",
        robot_id="",
    )
    reply, cards = actions_mod._action_select_blueprint(
        home, session, {"blueprint_id": bp_b}
    )
    assert bp_b in reply or "蓝本" in reply or cards
    assert session.blueprint_id == bp_b
    # 换蓝本：丢弃旧 clone，选用即建新副本
    assert is_writing_task_id(str(session.task_id or ""))
    assert session.task_id != "wStaleClone"
    docs = session_documents_map(session)
    assert f"bp:{bp_b}" in docs
    assert docs[f"bp:{bp_b}"].get("writing_task_id") == session.task_id


def test_doc_registry_key_never_mints_bp_for_writing_id():
    from report_writing.chat.session_documents import doc_registry_key

    wid = "w20261102175715b05f"
    assert doc_registry_key(blueprint_id=wid, task_id=wid, kind="blueprint") == f"task:{wid}"
    assert doc_registry_key(blueprint_id=wid, task_id="", kind="blueprint") == f"task:{wid}"
    assert doc_registry_key(blueprint_id=wid, task_id=wid, kind="outline") == f"task:{wid}"
    # 真库蓝本仍用 bp:
    assert doc_registry_key(blueprint_id="bpLibA", task_id="wClone1", kind="blueprint") == "bp:bpLibA"


def test_normalize_dirty_bp_w_registry_and_focus():
    from report_writing.chat.session_documents import (
        focus_document_key,
        normalize_document_registry_key,
        normalize_session_document_keys,
        session_documents_map,
        set_focus_document_key,
        upsert_session_document,
    )

    wid = "w20261102175715b05f"
    assert normalize_document_registry_key(f"bp:{wid}") == f"task:{wid}"
    session = ChatSession(id="s_bpw")
    session.freeform = {
        "session_documents": {
            f"bp:{wid}": {
                "kind": "blueprint",
                "title": "环境报告",
                "blueprint_id": wid,
                "task_id": wid,
                "writing_task_id": wid,
            }
        },
        "focus_document_key": f"bp:{wid}",
    }
    assert normalize_session_document_keys(session) is True
    docs = session_documents_map(session)
    assert f"task:{wid}" in docs
    assert f"bp:{wid}" not in docs
    assert docs[f"task:{wid}"]["title"] == "环境报告"
    assert focus_document_key(session) == f"task:{wid}"

    session2 = ChatSession(id="s_bpw2")
    set_focus_document_key(session2, f"bp:{wid}")
    assert focus_document_key(session2) == f"task:{wid}"

    session3 = ChatSession(id="s_sample_upsert")
    upsert_session_document(
        session3,
        kind="blueprint",
        blueprint_id=wid,
        task_id=wid,
        writing_task_id=wid,
        title="环境报告",
    )
    docs3 = session_documents_map(session3)
    assert f"task:{wid}" in docs3
    assert f"bp:{wid}" not in docs3


def test_select_blueprint_copies_outline_onto_named_empty_shell(tmp_path: Path):
    """先定名再建的空壳，选用蓝本后目录要进这份文档，不能另留一个 0 章副本。"""
    from report_writing.chat import actions as actions_mod
    from report_writing.chat.session_documents import ensure_named_freeform_document
    from report_writing.controller import create_blueprint, ensure_task
    from report_writing.domain.models import OutlineNode, OutlineTree
    from report_writing.services.store import TaskStore

    home = tmp_path
    (home / "tasks").mkdir(parents=True)
    bid = "bpOutline"
    path = home / "tasks" / bid
    ensure_task(path)
    create_blueprint(path, title="安全预评价", category="", description="t")
    task = TaskStore(path).load()
    task.outline = OutlineTree(
        chapters=[
            OutlineNode(key="c1", title="概述", sequence="1", points=["p"]),
            OutlineNode(key="c2", title="范围", sequence="2"),
        ]
    )
    task.outline_confirmed = True
    task.sample_ready = True
    TaskStore(path).save(task)

    session = ChatSession(id="s_named_shell", phase="intent")
    shell = ensure_named_freeform_document(
        session, home, title="2026安全报告 工程类安全报告", category="工程类"
    )
    reply, cards = actions_mod._action_select_blueprint(home, session, {"blueprint_id": bid})
    assert session.task_id == shell
    copied = TaskStore(home / "tasks" / shell).load()
    assert copied.title == "2026安全报告 工程类安全报告"
    assert copied.source_blueprint_id == bid
    assert [n.title for n in copied.outline.chapters] == ["概述", "范围"]
    assert "已学 1 章" in reply
    wiz = (session.freeform or {}).get("blueprint_wizard") or {}
    assert int(wiz.get("chapter_count") or 0) == 2
    assert wiz.get("blueprint_id") == bid
    assert any(c.type == "blueprint_wizard" for c in cards)

    # 副本上已有目录时，再次选用不得用库蓝本盖掉用户改过的章名
    copied.outline.chapters[0].title = "概述（已改）"
    TaskStore(home / "tasks" / shell).save(copied)
    actions_mod._action_select_blueprint(home, session, {"blueprint_id": bid})
    again = TaskStore(home / "tasks" / shell).load()
    assert again.outline.chapters[0].title == "概述（已改）"
    assert len(again.outline.chapters) == 2


def test_onboarding_title_refine_keeps_same_task(tmp_path: Path):
    """定名阶段微调标题（述职→个人述职）不得另开 w*。"""
    from report_writing.chat.session_documents import (
        confirm_writing_document_name,
        ensure_named_freeform_document,
        patch_focus_onboarding,
        session_documents_map,
        set_focus_document_key,
    )

    home = tmp_path
    (home / "tasks").mkdir(parents=True)
    session = ChatSession(id="s_refine")
    tid = ensure_named_freeform_document(
        session, home, title="述职报告", confirm_name=False
    )
    patch_focus_onboarding(session, name_confirmed=False, entry_path="")
    set_focus_document_key(session, f"task:{tid}")
    confirm_writing_document_name(
        session,
        "个人述职报告",
        home=home,
        message="叫做个人述职报告",
    )
    docs = session_documents_map(session)
    task_keys = [k for k in docs if str(k).startswith("task:")]
    assert session.task_id == tid
    assert len(task_keys) == 1
    assert docs[f"task:{tid}"]["title"] == "个人述职报告"


def test_blueprint_bound_rename_does_not_mint_new_slot(tmp_path: Path):
    """选用蓝本后「名字叫…」与库展示名不同：同 task 改标题，勿 force_new。"""
    from report_writing.chat.session_documents import (
        confirm_writing_document_name,
        is_writing_document_named,
        patch_focus_onboarding,
        session_documents_map,
        set_focus_document_key,
        should_mint_new_document_slot,
        upsert_session_document,
    )
    from report_writing.controller import ensure_task, save_spec_meta

    home = tmp_path
    (home / "tasks").mkdir(parents=True)
    session = ChatSession(id="s_bp_rename")
    session.blueprint_id = "bpSafety"
    tid = "wBpRename01"
    session.task_id = tid
    ensure_task(home / "tasks" / tid)
    save_spec_meta(home / "tasks" / tid, title="安全报告", category="", tags=["chat"])
    upsert_session_document(
        session,
        kind="writing",
        task_id=tid,
        blueprint_id="bpSafety",
        writing_task_id=tid,
        title="安全报告",
        registry_key=f"task:{tid}",
    )
    ff = dict(session.freeform or {})
    docs = dict(ff.get("session_documents") or {})
    ent = dict(docs.get(f"task:{tid}") or {})
    ent["source_blueprint_id"] = "bpSafety"
    docs[f"task:{tid}"] = ent
    ff["session_documents"] = docs
    session.freeform = ff
    set_focus_document_key(session, f"task:{tid}")
    patch_focus_onboarding(session, name_confirmed=False, entry_path="match", entry_branch="with_blueprint")
    assert not should_mint_new_document_slot(
        session,
        "安全报告",
        "大晚上安全报告",
        message="名字叫大晚上安全报告",
    )
    confirm_writing_document_name(
        session,
        "大晚上安全报告",
        home=home,
        message="名字叫大晚上安全报告",
    )
    assert session.task_id == tid
    assert str((session.freeform or {}).get("title") or "") == "大晚上安全报告"
    from report_writing.services.store import TaskStore

    task = TaskStore(home / "tasks" / tid).load()
    assert task.title == "大晚上安全报告"
    assert is_writing_document_named(session)


def test_suggest_next_tools_skips_match_when_blueprint_bound():
    from report_writing.chat.session_documents import suggest_next_tools_from_gaps
    from report_writing.domain.models import ChatSession

    session = ChatSession(id="s_no_rematch")
    session.blueprint_id = "bp1"
    gaps = {
        "missing_name": False,
        "missing_branch": False,
        "missing_freeform_mode": False,
        "declared_entry_path": "match",
    }
    tools = suggest_next_tools_from_gaps(
        gaps,
        message="使用已有的蓝本帮我写一份安全报告",
        title="大晚上安全报告",
        session=session,
    )
    assert not any(str(t.get("tool") or "") == "match_blueprints" for t in tools)


def test_stamp_document_thread_messages():
    from report_writing.chat.session_documents import (
        begin_document_onboarding,
        open_document_thread,
        stamp_document_thread_messages,
    )
    from report_writing.domain.models import ChatMessage

    session = ChatSession(id="s_stamp")
    session.messages = [
        ChatMessage(role="user", text="帮我写述职"),
        ChatMessage(role="assistant", text="好"),
    ]
    begin_document_onboarding(session, None, message="帮我写述职", force_new=True)
    open_document_thread(session, "task:wStamp01", since_index=0)
    stamp_document_thread_messages(session, "task:wStamp01")
    assert all(str(m.document_key or "") == "task:wStamp01" for m in session.messages)


def test_force_new_thread_anchor_includes_triggering_user_message():
    from report_writing.chat.session_documents import (
        begin_document_onboarding,
        open_document_thread,
        record_new_document_thread_anchor,
        stamp_document_thread_messages,
    )
    from report_writing.domain.models import ChatMessage

    session = ChatSession(id="s_anchor")
    session.messages = [
        ChatMessage(role="user", text="old", document_key="task:old"),
        ChatMessage(role="assistant", text="ok", document_key="task:old"),
        ChatMessage(role="user", text="写环境报告", document_key="task:old"),
    ]
    record_new_document_thread_anchor(session)
    begin_document_onboarding(
        session,
        None,
        message="写环境报告",
        title_hint="环境报告",
        force_new=True,
    )
    assert int((session.freeform or {}).get("document_thread_since_index") or -1) == 2
    open_document_thread(session, "task:wNew01", since_index=2)
    stamp_document_thread_messages(session, "task:wNew01")
    assert str(session.messages[0].document_key or "") == "task:old"
    assert str(session.messages[1].document_key or "") == "task:old"
    assert str(session.messages[2].document_key or "") == "task:wNew01"


def test_chitchat_not_document_title():
    from report_writing.chat.session_documents import (
        message_is_chitchat_only,
        sanitize_candidate_document_title,
        title_rejects_as_document_name,
    )

    assert message_is_chitchat_only("你好")
    assert message_is_chitchat_only("  谢谢！ ")
    assert not message_is_chitchat_only("你好，帮我写述职报告")
    assert title_rejects_as_document_name("你好", message="你好")
    assert sanitize_candidate_document_title("你好", message="你好") == ""
