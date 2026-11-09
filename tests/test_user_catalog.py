"""登录用户 MySQL 主存。"""

from __future__ import annotations

from pathlib import Path

from report_writing.domain.user import UserProfile
from report_writing.services.user_catalog import StoredUser
from report_writing.services.users import (
    get_token_usage,
    get_user,
    login_user,
    record_token_usage,
    register_user,
    sync_users_from_disk,
    users_root,
)


def test_register_login_writes_mysql(tmp_path: Path, monkeypatch):
    home = tmp_path / "data"
    store: dict[str, StoredUser] = {}

    monkeypatch.setattr("report_writing.services.users._user_store_ok", lambda: True)
    monkeypatch.setattr("report_writing.services.users.sync_users_from_disk", lambda home: 0)
    monkeypatch.setattr(
        "report_writing.services.user_catalog.load_user_by_id",
        lambda uid: store.get(uid),
    )
    monkeypatch.setattr(
        "report_writing.services.user_catalog.load_user_by_username",
        lambda name: next((item for item in store.values() if item.profile.username == name), None),
    )
    monkeypatch.setattr(
        "report_writing.services.user_catalog.list_stored_users",
        lambda: list(store.values()),
    )

    def fake_save(profile: UserProfile, password_hash: str) -> None:
        store[profile.id] = StoredUser(profile=profile, password_hash=password_hash)

    monkeypatch.setattr("report_writing.services.user_catalog.save_user_record", fake_save)

    auth = register_user(home, "alice_01", "secret1", "Alice")
    assert auth.user_id in store
    assert store[auth.user_id].password_hash
    assert login_user(home, "alice_01", "secret1").user_id == auth.user_id
    assert get_user(home, auth.user_id).username == "alice_01"


def test_sync_users_from_disk_upserts_mysql(tmp_path: Path, monkeypatch):
    home = tmp_path / "data"
    uid = "ude780247c4"
    folder = users_root(home) / uid
    folder.mkdir(parents=True)
    (folder / "profile.json").write_text(
        '{"id": "ude780247c4", "username": "ude780247c4", "display_name": "本地用户", "created_at": "2026-11-06"}',
        encoding="utf-8",
    )
    (users_root(home) / "index.json").write_text(
        '{"ude780247c4": "ude780247c4"}',
        encoding="utf-8",
    )
    store: dict[str, StoredUser] = {}

    monkeypatch.setattr("report_writing.services.users._user_store_ok", lambda: True)
    monkeypatch.setattr(
        "report_writing.services.user_catalog.load_user_by_id",
        lambda user_id: store.get(user_id),
    )
    monkeypatch.setattr(
        "report_writing.services.user_catalog.load_user_by_username",
        lambda name: next((item for item in store.values() if item.profile.username == name), None),
    )

    def fake_save(profile: UserProfile, password_hash: str) -> None:
        store[profile.id] = StoredUser(profile=profile, password_hash=password_hash)

    monkeypatch.setattr("report_writing.services.user_catalog.save_user_record", fake_save)
    monkeypatch.setattr("report_writing.services.user_catalog.load_token_usage_doc", lambda uid: None)
    monkeypatch.setattr("report_writing.services.user_catalog.save_token_usage_doc", lambda uid, doc: None)

    assert sync_users_from_disk(home) >= 1
    assert store[uid].profile.username == "ude780247c4"


def test_token_usage_prefers_mysql(tmp_path: Path, monkeypatch):
    home = tmp_path / "data"
    uid = "uabc"
    saved: dict = {}

    monkeypatch.setattr("report_writing.services.users._user_store_ok", lambda: True)
    monkeypatch.setattr(
        "report_writing.services.user_catalog.load_token_usage_doc",
        lambda user_id: saved or None,
    )

    def fake_save(user_id: str, document: dict) -> None:
        saved.clear()
        saved.update(document)

    monkeypatch.setattr("report_writing.services.user_catalog.save_token_usage_doc", fake_save)
    record_token_usage(home, uid, prompt_tokens=3, completion_tokens=1, label="test")
    summary = get_token_usage(home, uid)
    assert summary.total_tokens == 4
    assert saved.get("total_tokens") == 4
