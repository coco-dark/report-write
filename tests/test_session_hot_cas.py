"""Redis 热存并发：不得用更短消息列表冲掉已落库对话。"""

from __future__ import annotations

from report_writing.services.session_redis_store import _merge_hot_session_preserve_progress


def test_merge_hot_never_shrinks_messages():
    existing = {
        "revision": 11,
        "blueprint_id": "bp1",
        "messages": [
            {"role": "assistant", "text": "begin", "cards": []},
            {"role": "user", "text": "创建蓝本「T」", "cards": []},
            {
                "role": "assistant",
                "text": "蓝本「T」已建好（bp1）",
                "cards": [
                    {
                        "type": "blueprint_wizard",
                        "title": "创建蓝本 · 上传样板",
                        "payload": {"step": "upload", "blueprint_id": "bp1"},
                    }
                ],
            },
        ],
        "freeform": {"blueprint_wizard": {"step": "upload", "blueprint_id": "bp1", "title": "T"}},
    }
    # 滞后的 ui-state：revision 更高但消息更少（只有 begin）
    incoming = {
        "revision": 12,
        "blueprint_id": "",
        "messages": [
            {"role": "assistant", "text": "begin", "cards": []},
        ],
        "freeform": {"blueprint_wizard": {"step": "meta", "title": "T"}},
    }
    merged = _merge_hot_session_preserve_progress(existing, incoming)
    assert len(merged["messages"]) == 3
    assert "已建好" in merged["messages"][-1]["text"]
    wiz = (merged.get("freeform") or {}).get("blueprint_wizard") or {}
    assert wiz.get("blueprint_id") == "bp1"
    assert wiz.get("step") == "upload"
    assert merged.get("blueprint_id") == "bp1"


def test_merge_hot_abandon_does_not_restore_blueprint():
    existing = {
        "revision": 4,
        "blueprint_id": "bp_wiz",
        "messages": [{"role": "assistant", "text": "向导", "cards": []}],
        "freeform": {"blueprint_wizard": {"step": "outline_confirm", "blueprint_id": "bp_wiz", "title": "安全报告"}},
    }
    incoming = {
        "revision": 5,
        "blueprint_id": "",
        "messages": [
            {"role": "assistant", "text": "向导", "cards": []},
            {"role": "user", "text": "不用这个蓝本", "cards": []},
            {"role": "assistant", "text": "先不用了", "cards": []},
        ],
        "freeform": {
            "awaiting_path_after_no_match": {"topic": "安全报告", "asked": True},
        },
    }
    merged = _merge_hot_session_preserve_progress(existing, incoming)
    assert not (merged.get("blueprint_id") or "").strip()
    assert not ((merged.get("freeform") or {}).get("blueprint_wizard"))


def test_merge_hot_allows_longer_incoming_messages():
    existing = {
        "revision": 5,
        "messages": [{"role": "assistant", "text": "hi", "cards": []}],
        "freeform": {},
    }
    incoming = {
        "revision": 6,
        "messages": [
            {"role": "assistant", "text": "hi", "cards": []},
            {"role": "user", "text": "新建", "cards": []},
            {"role": "assistant", "text": "向导", "cards": []},
        ],
        "freeform": {"blueprint_wizard": {"step": "meta"}},
    }
    merged = _merge_hot_session_preserve_progress(existing, incoming)
    assert len(merged["messages"]) == 3


def test_merge_hot_focus_change_does_not_restore_old_wizard():
    existing = {
        "revision": 8,
        "blueprint_id": "bp202611022054023656",
        "messages": [{"role": "assistant", "text": "分析", "cards": []}],
        "freeform": {
            "focus_document_key": "bp:bp202611022054023656",
            "writing_path": "blueprint",
            "blueprint_wizard": {
                "mode": "selected",
                "step": "study",
                "blueprint_id": "bp202611022054023656",
                "working_task_id": "w_old",
                "chapter_count": 12,
            },
        },
    }
    incoming = {
        "revision": 9,
        "blueprint_id": "",
        "messages": [
            {"role": "assistant", "text": "分析", "cards": []},
            {"role": "user", "text": "再写一份", "cards": []},
            {"role": "assistant", "text": "向导", "cards": []},
        ],
        "freeform": {
            "focus_document_key": "task:w_new",
            "blueprint_wizard": {
                "mode": "create",
                "step": "meta",
                "fresh_meta": True,
                "working_task_id": "w_new",
                "title": "环境报告",
            },
        },
    }
    merged = _merge_hot_session_preserve_progress(existing, incoming)
    wiz = dict((merged.get("freeform") or {}).get("blueprint_wizard") or {})
    assert str(wiz.get("step") or "") == "meta"
    assert str(wiz.get("working_task_id") or "") == "w_new"
    assert int(wiz.get("chapter_count") or 0) != 12
    assert not (merged.get("blueprint_id") or "").strip()
    assert str((merged.get("freeform") or {}).get("writing_path") or "") != "blueprint"
