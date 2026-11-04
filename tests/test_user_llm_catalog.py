"""用户模型配置 MySQL 存储。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from report_writing.domain.user import UserLlmConfig
from report_writing.services.user_llm_catalog import llm_config_has_data
from report_writing.services.users import _read_llm_config_file, get_llm_config, save_llm_config, users_root


def test_llm_config_has_data():
    assert not llm_config_has_data(UserLlmConfig())
    assert llm_config_has_data(UserLlmConfig(enabled=True))
    assert llm_config_has_data(UserLlmConfig(api_key="sk-test"))


def test_get_llm_config_migrates_file_to_mysql(tmp_path: Path, monkeypatch):
    home = tmp_path / "data"
    uid = "u1234567890"
    folder = users_root(home) / uid
    folder.mkdir(parents=True)
    legacy = {
        "provider": "bailian",
        "api_key": "sk-legacy-key",
        "model_name": "qwen-plus",
        "enabled": True,
    }
    (folder / "llm.json").write_text(json.dumps(legacy), encoding="utf-8")

    stored: dict = {}

    def fake_available():
        return True

    def fake_load(user_id: str):
        return UserLlmConfig.from_dict(stored) if stored else None

    def fake_save(user_id: str, cfg: UserLlmConfig):
        stored.update(cfg.to_dict())

    monkeypatch.setattr(
        "report_writing.services.user_llm_catalog.llm_config_store_available",
        fake_available,
    )
    monkeypatch.setattr(
        "report_writing.services.user_llm_catalog.load_user_llm_config",
        fake_load,
    )
    monkeypatch.setattr(
        "report_writing.services.user_llm_catalog.save_user_llm_config",
        fake_save,
    )

    cfg = get_llm_config(home, uid)
    assert cfg.enabled is True
    assert cfg.model_name == "qwen-plus"
    assert stored.get("api_key") == "sk-legacy-key"
    assert not (folder / "llm.json").is_file()


def test_save_llm_config_requires_mysql(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        "report_writing.services.user_llm_catalog.llm_config_store_available",
        lambda: False,
    )
    from report_writing.services.user_llm_catalog import UserLlmConfigStoreError

    with pytest.raises(UserLlmConfigStoreError):
        save_llm_config(tmp_path, "default", {"enabled": False})
