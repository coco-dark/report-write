"""用户目录、登录与模型配置、Token 统计。"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import secrets
import time
import uuid
from contextvars import ContextVar
from pathlib import Path
from typing import Any

from report_writing.domain.models import BailianSettings
from report_writing.domain.user import (
    AuthResponse,
    TokenDayBucket,
    TokenUsageSummary,
    UserLlmConfig,
    UserLlmConfigPublic,
    UserProfile,
)

_current_user_id: ContextVar[str] = ContextVar("current_user_id", default="default")

_USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{3,32}$")
_ID_SAFE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

_log = logging.getLogger(__name__)


def current_user_id() -> str:
    return (_current_user_id.get() or "default").strip() or "default"


def set_current_user_id(user_id: str) -> object:
    uid = (user_id or "default").strip() or "default"
    return _current_user_id.set(uid)


def reset_current_user_id(token: object) -> None:
    _current_user_id.reset(token)


def users_root(home: Path) -> Path:
    root = Path(home) / "users"
    root.mkdir(parents=True, exist_ok=True)
    return root


def normalize_owner_id(user_id: str) -> str:
    return (user_id or "").strip() or "default"


def owns_user_resource(current_id: str, owner_id: str) -> bool:
    return normalize_owner_id(current_id) == normalize_owner_id(owner_id)


def user_workspace(home: Path, user_id: str | None = None) -> Path:
    """单用户工作区：对话/蓝本/机器人/渲染/样式/知识库均在此目录下。"""
    uid = normalize_owner_id(user_id or current_user_id())
    ws = users_root(home) / uid / "workspace"
    for name in (
        "tasks",
        "robots",
        "render_templates",
        "word_styles",
        "chat_sessions",
        "knowledge",
        "element_library",
    ):
        (ws / name).mkdir(parents=True, exist_ok=True)
    (ws / "knowledge" / "files").mkdir(parents=True, exist_ok=True)
    _migrate_legacy_shared_data(home, uid, ws)
    try:
        from report_writing.services.render import ensure_default_templates
        from report_writing.services.word_styles import ensure_default_word_styles

        ensure_default_word_styles(ws, owner_id=uid)
        ensure_default_templates(ws, owner_id=uid)
    except Exception:
        pass
    return ws


def _migrate_legacy_shared_data(home: Path, user_id: str, workspace: Path) -> None:
    """旧版 data/ 顶层目录仅迁入 default 用户（workspace 对应子目录为空时）。"""
    if normalize_owner_id(user_id) != "default":
        return
    import shutil

    pairs = (
        ("tasks", home / "tasks"),
        ("robots", home / "robots"),
        ("render_templates", home / "render_templates"),
        ("word_styles", home / "word_styles"),
        ("chat_sessions", home / "chat_sessions"),
        ("knowledge", home / "knowledge"),
    )
    for name, src in pairs:
        if not src.exists():
            continue
        dest = workspace / name
        if dest.exists() and any(dest.iterdir()):
            continue
        if src.is_dir():
            if not any(src.iterdir()):
                continue
            shutil.copytree(src, dest, dirs_exist_ok=True)
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)


def ensure_default_user(home: Path) -> UserProfile:
    root = users_root(home)
    index = _load_index(root)
    if "default" not in index.values() and "default" not in index:
        profile = UserProfile(
            id="default",
            username="default",
            display_name="默认用户",
            created_at=_now(),
        )
        _save_profile(root, profile, password_hash="")
        index["default"] = "default"
        _save_index(root, index)
        return profile
    uid = index.get("default", "default")
    found = get_user(home, uid)
    if found:
        return found
    profile = UserProfile(id="default", username="default", display_name="默认用户", created_at=_now())
    _save_profile(root, profile, password_hash="")
    index["default"] = "default"
    _save_index(root, index)
    return profile


def list_users(home: Path) -> list[UserProfile]:
    ensure_default_user(home)
    items: list[UserProfile] = []
    for folder in sorted(users_root(home).iterdir()):
        if not folder.is_dir():
            continue
        profile = _read_profile(folder)
        if profile:
            items.append(profile)
    return items


def get_user(home: Path, user_id: str) -> UserProfile | None:
    uid = _require_id(user_id)
    folder = users_root(home) / uid
    return _read_profile(folder)


def update_user_profile(home: Path, user_id: str, display_name: str) -> UserProfile:
    uid = _require_id(user_id)
    folder = users_root(home) / uid
    profile = _read_profile(folder)
    if profile is None:
        raise KeyError(user_id)
    name = (display_name or "").strip() or profile.display_name or profile.username
    profile = UserProfile(
        id=profile.id,
        username=profile.username,
        display_name=name,
        created_at=profile.created_at,
    )
    _save_profile(root=users_root(home), profile=profile, password_hash=_read_password_hash(folder) or "")
    return profile


def register_user(home: Path, username: str, password: str, display_name: str = "") -> AuthResponse:
    name = (username or "").strip().lower()
    if not _USERNAME_RE.match(name):
        raise ValueError("用户名须为 3–32 位字母、数字或下划线")
    if len(password or "") < 6:
        raise ValueError("密码至少 6 位")
    root = users_root(home)
    index = _load_index(root)
    if name in index:
        raise ValueError("用户名已存在")
    uid = f"u{uuid.uuid4().hex[:10]}"
    profile = UserProfile(
        id=uid,
        username=name,
        display_name=(display_name or name).strip(),
        created_at=_now(),
    )
    _save_profile(root, profile, password_hash=_hash_password(password))
    index[name] = uid
    _save_index(root, index)
    user_workspace(home, uid)
    return AuthResponse(user_id=uid, username=profile.username, display_name=profile.display_name)


def login_user(home: Path, username: str, password: str) -> AuthResponse:
    name = (username or "").strip().lower()
    root = users_root(home)
    index = _load_index(root)
    uid = index.get(name)
    if not uid:
        raise ValueError("用户名或密码错误")
    folder = root / uid
    stored = _read_password_hash(folder)
    if not stored or not _verify_password(password, stored):
        raise ValueError("用户名或密码错误")
    profile = _read_profile(folder)
    if not profile:
        raise ValueError("用户数据损坏")
    return AuthResponse(user_id=profile.id, username=profile.username, display_name=profile.display_name)


def get_llm_config(home: Path, user_id: str) -> UserLlmConfig:
    ensure_default_user(home)
    uid = _require_id(user_id)
    from report_writing.services.user_llm_catalog import (
        UserLlmConfigStoreError,
        llm_config_has_data,
        llm_config_store_available,
        load_user_llm_config,
        save_user_llm_config,
    )

    if llm_config_store_available():
        try:
            stored = load_user_llm_config(uid)
        except UserLlmConfigStoreError:
            stored = None
        if stored is not None:
            return stored
        legacy = _read_llm_config_file(home, uid)
        if llm_config_has_data(legacy):
            try:
                save_user_llm_config(uid, legacy)
                _remove_llm_config_file(home, uid)
            except Exception as error:
                _log.warning("迁移 llm.json → MySQL 失败 user=%s: %s", uid, error)
                return legacy
            return legacy
        return UserLlmConfig()
    return UserLlmConfig()


def save_llm_config(home: Path, user_id: str, payload: dict[str, Any] | UserLlmConfig) -> UserLlmConfigPublic:
    uid = _require_id(user_id)
    ensure_default_user(home)
    from report_writing.services.user_llm_catalog import (
        UserLlmConfigStoreError,
        llm_config_store_available,
        save_user_llm_config,
    )

    if not llm_config_store_available():
        raise UserLlmConfigStoreError(
            "个人模型配置已改为仅存 MySQL，请启动数据库并配置 REPORT_WRITING_DATABASE（勿使用 file 模式）。"
        )
    existing = get_llm_config(home, uid)
    data = payload.to_dict() if isinstance(payload, UserLlmConfig) else dict(payload or {})
    merged = UserLlmConfig.from_dict({**existing.to_dict(), **data})
    if "api_key" in data and not str(data.get("api_key") or "").strip():
        merged.api_key = existing.api_key
    save_user_llm_config(uid, merged)
    _remove_llm_config_file(home, uid)
    return public_llm_config(home, uid)


def public_llm_config(home: Path, user_id: str) -> UserLlmConfigPublic:
    cfg = get_llm_config(home, user_id)
    key = (cfg.api_key or "").strip()
    using_user = bool(cfg.enabled and key and (cfg.model_name or "").strip())
    hint = ""
    if key:
        hint = key[:4] + "…" + key[-4:] if len(key) > 10 else "****"
    from report_writing.infra.bailian import load_bailian_settings, normalize_model_name

    system = load_bailian_settings()
    system_model = normalize_model_name(system.model_name or "") or system.model_name
    active_model = ""
    active_source = "user" if using_user else "system"
    if using_user:
        active_model = normalize_model_name((cfg.model_name or "").strip()) or (cfg.model_name or "").strip()
    else:
        active_model = system_model
    return UserLlmConfigPublic(
        provider=cfg.provider,
        base_url=cfg.base_url or cfg.effective_base_url(),
        api_key_set=bool(key),
        api_key_hint=hint,
        model_name=cfg.model_name,
        completions_path=cfg.completions_path,
        timeout=cfg.timeout,
        temperature=cfg.temperature,
        max_tokens=cfg.max_tokens,
        intent_model_name=str(cfg.intent_model_name or "").strip(),
        enabled=cfg.enabled,
        using_system_default=not using_user,
        active_source=active_source,
        active_model_name=active_model or "",
        system_model_name=system_model or "",
    )


def resolve_bailian_settings(home: Path, user_id: str) -> BailianSettings | None:
    """用户启用自配模型时返回等价 BailianSettings；否则 None 表示走系统百炼。"""
    cfg = get_llm_config(home, user_id)
    if not cfg.enabled:
        return None
    key = (cfg.api_key or "").strip()
    from report_writing.infra.bailian import normalize_model_name

    model = normalize_model_name((cfg.model_name or "").strip())
    if not key or not model:
        return None
    base = cfg.effective_base_url()
    path = (cfg.completions_path or "/chat/completions").strip() or "/chat/completions"
    if not path.startswith("/"):
        path = f"/{path}"
    return BailianSettings(
        api_key=key,
        base_url=base,
        completions_path=path,
        model_name=model,
        timeout=int(cfg.timeout or 120),
        temperature=float(cfg.temperature if cfg.temperature is not None else 0),
        max_tokens=int(cfg.max_tokens or 4096),
        thinking_level="NONE",
        chat_template_kwargs_enabled=False,
    )


def record_token_usage(
    home: Path,
    user_id: str,
    *,
    prompt_tokens: int,
    completion_tokens: int,
    label: str = "",
) -> None:
    uid = _require_id(user_id)
    prompt = max(0, int(prompt_tokens or 0))
    completion = max(0, int(completion_tokens or 0))
    total = prompt + completion
    if total <= 0:
        return
    folder = users_root(home) / uid
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "tokens.json"
    raw: dict[str, Any] = {}
    if path.is_file():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            raw = {}
    raw["total_prompt_tokens"] = int(raw.get("total_prompt_tokens") or 0) + prompt
    raw["total_completion_tokens"] = int(raw.get("total_completion_tokens") or 0) + completion
    raw["total_tokens"] = int(raw.get("total_tokens") or 0) + total
    raw["total_calls"] = int(raw.get("total_calls") or 0) + 1
    day = time.strftime("%Y-%m-%d")
    by_day = raw.get("by_day") if isinstance(raw.get("by_day"), dict) else {}
    bucket = dict(by_day.get(day) or {})
    bucket["prompt_tokens"] = int(bucket.get("prompt_tokens") or 0) + prompt
    bucket["completion_tokens"] = int(bucket.get("completion_tokens") or 0) + completion
    bucket["total_tokens"] = int(bucket.get("total_tokens") or 0) + total
    bucket["calls"] = int(bucket.get("calls") or 0) + 1
    by_day[day] = bucket
    raw["by_day"] = by_day
    recent = raw.get("recent") if isinstance(raw.get("recent"), list) else []
    recent.insert(
        0,
        {
            "at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": total,
            "label": (label or "").strip()[:64],
        },
    )
    raw["recent"] = recent[:80]
    path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")


def get_token_usage(home: Path, user_id: str) -> TokenUsageSummary:
    uid = _require_id(user_id)
    path = users_root(home) / uid / "tokens.json"
    if not path.is_file():
        return TokenUsageSummary(user_id=uid)
    raw = json.loads(path.read_text(encoding="utf-8"))
    by_day: dict[str, TokenDayBucket] = {}
    for key, value in (raw.get("by_day") or {}).items():
        if not isinstance(value, dict):
            continue
        by_day[str(key)] = TokenDayBucket.from_dict(value)
    return TokenUsageSummary(
        user_id=uid,
        total_prompt_tokens=int(raw.get("total_prompt_tokens") or 0),
        total_completion_tokens=int(raw.get("total_completion_tokens") or 0),
        total_tokens=int(raw.get("total_tokens") or 0),
        total_calls=int(raw.get("total_calls") or 0),
        by_day=by_day,
        recent=list(raw.get("recent") or [])[:40],
    )


def repair_user_data_layout(home: Path, *, quarantine_legacy: bool = False) -> dict[str, Any]:
    """修正误写在 workspace/users 下的账号目录，并可选隔离顶层旧版全局 data/。"""
    import shutil

    home = Path(home)
    out: dict[str, Any] = {"moved_users": [], "merged_index": [], "quarantined": [], "errors": []}
    root = users_root(home)
    index = _load_index(root)

    def merge_user_dir(uid: str, src: Path) -> None:
        dest = root / uid
        if not src.is_dir() or dest.resolve() == src.resolve():
            return
        if not dest.exists():
            shutil.move(str(src), str(dest))
            out["moved_users"].append({"uid": uid, "action": "move", "from": str(src)})
            return
        src_ws = src / "workspace"
        dest_ws = dest / "workspace"
        dest_ws.mkdir(parents=True, exist_ok=True)
        if src_ws.is_dir():
            for child in src_ws.iterdir():
                target = dest_ws / child.name
                if target.exists():
                    continue
                try:
                    shutil.move(str(child), str(target))
                except OSError as error:
                    out["errors"].append(f"{child}: {error}")
        out["moved_users"].append({"uid": uid, "action": "merge_workspace", "from": str(src)})

    for profile_path in home.glob("users/*/workspace/users/*/profile.json"):
        merge_user_dir(profile_path.parent.name, profile_path.parent)

    for profile_path in home.glob("users/**/profile.json"):
        folder = profile_path.parent
        profile = _read_profile(folder)
        if profile is None or not profile.id:
            continue
        uid = _require_id(profile.id)
        dest = root / uid
        dest.mkdir(parents=True, exist_ok=True)
        if not (dest / "profile.json").is_file():
            shutil.copy2(profile_path, dest / "profile.json")
        auth_src = folder / "auth.json"
        if auth_src.is_file() and not (dest / "auth.json").is_file():
            shutil.copy2(auth_src, dest / "auth.json")
        if profile.username:
            index[profile.username] = uid

    for folder in sorted(root.iterdir()):
        if not folder.is_dir():
            continue
        profile = _read_profile(folder)
        if profile and profile.username:
            index[profile.username] = profile.id
    _save_index(root, index)
    out["merged_index"] = sorted(index.keys())

    if quarantine_legacy:
        quarantine = home / "_legacy_global"
        for name in ("tasks", "robots", "render_templates", "word_styles", "chat_sessions", "knowledge"):
            src = home / name
            if not src.is_dir() or not any(src.iterdir()):
                continue
            dest = quarantine / name
            try:
                dest.parent.mkdir(parents=True, exist_ok=True)
                if dest.exists():
                    continue
                shutil.move(str(src), str(dest))
                out["quarantined"].append(name)
            except OSError as error:
                out["errors"].append(f"quarantine {name}: {error}")

    return out


def _read_profile(folder: Path) -> UserProfile | None:
    path = folder / "profile.json"
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return None
    return UserProfile.from_dict(data)


def _save_profile(root: Path, profile: UserProfile, *, password_hash: str) -> None:
    folder = root / profile.id
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "profile.json").write_text(
        json.dumps(profile.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if password_hash:
        (folder / "auth.json").write_text(
            json.dumps({"password_hash": password_hash}, ensure_ascii=False),
            encoding="utf-8",
        )


def _read_password_hash(folder: Path) -> str:
    path = folder / "auth.json"
    if not path.is_file():
        return ""
    data = json.loads(path.read_text(encoding="utf-8"))
    return str((data or {}).get("password_hash") or "")


def _load_index(root: Path) -> dict[str, str]:
    path = root / "index.json"
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return dict(data) if isinstance(data, dict) else {}


def _save_index(root: Path, index: dict[str, str]) -> None:
    (root / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")


def _hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 120_000)
    return f"{salt}${digest.hex()}"


def _verify_password(password: str, stored: str) -> bool:
    if "$" not in stored:
        return False
    salt, digest = stored.split("$", 1)
    check = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 120_000)
    return check.hex() == digest


def _require_id(value: str) -> str:
    key = (value or "").strip()
    if not key or not _ID_SAFE.match(key):
        raise ValueError("用户 id 不合法")
    return key


def _read_llm_config_file(home: Path, user_id: str) -> UserLlmConfig:
    path = users_root(home) / _require_id(user_id) / "llm.json"
    if not path.is_file():
        return UserLlmConfig()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return UserLlmConfig()
    return UserLlmConfig.from_dict(data if isinstance(data, dict) else {})


def _remove_llm_config_file(home: Path, user_id: str) -> None:
    path = users_root(home) / _require_id(user_id) / "llm.json"
    if path.is_file():
        try:
            path.unlink()
        except OSError as error:
            _log.debug("删除已迁移的 llm.json 失败: %s", error)


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M")
