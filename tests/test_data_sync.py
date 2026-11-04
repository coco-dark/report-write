"""data 同步在无库时为空操作。"""

from __future__ import annotations

from pathlib import Path

from report_writing.services.data_sync import sync_local_data


def test_sync_local_data_skips_without_database(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("REPORT_WRITING_DATABASE", "file")
    assert sync_local_data(tmp_path) == {}
