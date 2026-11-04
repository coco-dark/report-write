"""读取本仓库根目录 config/ 下的 YAML（及 *.local.yml 覆盖）。"""

from __future__ import annotations

from pathlib import Path

import yaml

# backend/src/report_writing/infra → 项目根 report-writing/
_PROJECT_ROOT = Path(__file__).resolve().parents[4]
_CONFIG_DIR = _PROJECT_ROOT / "config"


def project_root() -> Path:
    return _PROJECT_ROOT


def data_home_path() -> Path:
    rw = _merged("application").get("report_writing") or {}
    raw = str((rw.get("data_home") if isinstance(rw, dict) else None) or "data").strip() or "data"
    path = Path(raw)
    if path.is_absolute():
        return path
    return _PROJECT_ROOT / raw


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return loaded if isinstance(loaded, dict) else {}


def _merged(name: str) -> dict:
    merged: dict = {}
    for suffix in (".yml", ".yaml", ".local.yml", ".local.yaml"):
        path = _CONFIG_DIR / f"{name}{suffix}"
        if path.exists():
            _deep_merge(merged, _load_yaml(path))
    return merged


def bailian_deepseek_section() -> dict:
    doc = _merged("bailian")
    section = (doc.get("bailian") or {}).get("deepseek") or {}
    return dict(section) if isinstance(section, dict) else {}


def spring_datasource_master() -> dict:
    doc = _merged("application")
    master = (((doc.get("spring") or {}).get("datasource") or {}).get("druid") or {}).get("master") or {}
    return {k: v for k, v in master.items() if v not in (None, "")}


def suiyue_section(name: str) -> dict:
    doc = _merged("application")
    block = (doc.get("suiyue") or {}).get(name) or {}
    return dict(block) if isinstance(block, dict) else {}


def _deep_merge(base: dict, patch: dict) -> None:
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
