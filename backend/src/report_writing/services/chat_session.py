"""对话写报告会话：Redis 热存 + 异步/同步 MySQL；库都不可用时才回落本地 JSON。"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from pathlib import Path

from report_writing.domain.models import ChatMessage, ChatPhase, ChatSession, ChatSessionSummary
from report_writing.infra.postgres import _connect, load_database_settings, mysql_available

_log = logging.getLogger(__name__)
_ID_SAFE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_schema_ready = False

# 用户可见的关键向导步：必须同步落 MySQL
_DURABLE_WIZARD_STEPS = frozenset(
    {
        "meta",
        "upload_done",
        "outline_done",
        "outline_confirm",
        "study_done",
        "done",
    }
)
_DURABLE_ACTIONS = frozenset(
    {
        "select_blueprint",
        "skip_blueprint",
        "start_writing",
        "confirm_chapter",
        "confirm_all_chapters",
        "pause_writing",
        "save_blueprint",
    }
)

_CHAT_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS writing_chat_session (
    id varchar(64) NOT NULL PRIMARY KEY,
    user_id varchar(64) NOT NULL DEFAULT 'default',
    robot_id varchar(64) NOT NULL DEFAULT '',
    phase varchar(64) NOT NULL DEFAULT 'intent',
    document json NOT NULL,
    title varchar(80) NOT NULL DEFAULT '',
    preview varchar(160) NOT NULL DEFAULT '',
    message_count int NOT NULL DEFAULT 0,
    created_at varchar(32) NOT NULL,
    updated_at varchar(32) NOT NULL,
    KEY writing_chat_session_user_idx (user_id),
    KEY writing_chat_session_robot_idx (robot_id),
    KEY writing_chat_session_updated_idx (updated_at)
)
"""


def _ensure_chat_schema(connection) -> None:
    global _schema_ready
    if _schema_ready:
        return
    from report_writing.infra.json_tables import table_columns

    connection.execute(_CHAT_TABLE_SQL)
    cols = table_columns(connection, "writing_chat_session")
    if cols and "user_id" not in cols:
        try:
            connection.execute(
                "ALTER TABLE writing_chat_session ADD COLUMN user_id varchar(64) NOT NULL DEFAULT 'default'"
            )
            connection.execute(
                "ALTER TABLE writing_chat_session ADD KEY writing_chat_session_user_idx (user_id)"
            )
        except Exception:
            pass
    # 列表摘要列：历史侧栏只查这些，绝不拉整份 document
    alters = [
        ("title", "ALTER TABLE writing_chat_session ADD COLUMN title varchar(80) NOT NULL DEFAULT ''"),
        ("preview", "ALTER TABLE writing_chat_session ADD COLUMN preview varchar(160) NOT NULL DEFAULT ''"),
        (
            "message_count",
            "ALTER TABLE writing_chat_session ADD COLUMN message_count int NOT NULL DEFAULT 0",
        ),
    ]
    for name, sql in alters:
        if cols and name not in cols:
            try:
                connection.execute(sql)
            except Exception:
                pass
    try:
        connection.execute(
            """
            UPDATE writing_chat_session
            SET user_id = COALESCE(
                NULLIF(JSON_UNQUOTE(JSON_EXTRACT(document, '$.user_id')), ''),
                'default'
            )
            WHERE user_id = 'default' OR user_id = '' OR user_id IS NULL
            """
        )
    except Exception:
        pass
    _schema_ready = True


def sessions_root(home: Path) -> Path:
    root = Path(home) / "chat_sessions"
    root.mkdir(parents=True, exist_ok=True)
    return root


def create_session(home: Path, *, robot_id: str = "") -> ChatSession:
    from report_writing.services.users import current_user_id

    session_id = f"cs{uuid.uuid4().hex[:12]}"
    now = _now()
    session = ChatSession(
        id=session_id,
        phase=ChatPhase.INTENT.value,
        robot_id=(robot_id or "").strip(),
        user_id=current_user_id(),
        created_at=now,
        updated_at=now,
    )
    save_session(home, session, durable=True)
    return session


def _session_owner_id(session: ChatSession, *, workspace_user: str | None = None) -> str:
    """库内无 user_id 的会话视为 default 用户；工作区文件可视为 workspace_user。"""
    from report_writing.services.users import normalize_owner_id

    raw = (getattr(session, "user_id", "") or "").strip()
    if raw:
        return normalize_owner_id(raw)
    if workspace_user:
        return normalize_owner_id(workspace_user)
    return "default"


def _sanitize_session_bindings(home: Path, session: ChatSession) -> ChatSession:
    """去掉不属于当前用户工作区的 robot / 蓝本 / 写作任务引用。"""
    from report_writing.services.robots import get_robot

    if session.robot_id and get_robot(home, session.robot_id) is None:
        session.robot_id = ""
    if session.task_id:
        from report_writing.services.task_ids import is_blueprint_task_id

        tid = (session.task_id or "").strip()
        if is_blueprint_task_id(tid):
            from report_writing.services.blueprint_catalog import load_blueprint_task

            if load_blueprint_task(tid) is None:
                session.task_id = ""
        else:
            task_path = Path(home) / "tasks" / tid / "task.json"
            from report_writing.services.writing_task_catalog import load_writing_task

            if not task_path.is_file() and load_writing_task(tid) is None:
                session.task_id = ""
    wiz_bid = str(((session.freeform or {}).get("blueprint_wizard") or {}).get("blueprint_id") or "").strip()
    if wiz_bid and not str(session.blueprint_id or "").strip():
        session.blueprint_id = wiz_bid
    if session.blueprint_id:
        bid = str(session.blueprint_id).strip()
        task_dir = Path(home) / "tasks" / bid
        if not task_dir.is_dir():
            from report_writing.controller import list_blueprints

            allowed = {item.id for item in list_blueprints(Path(home) / "tasks")}
            # 目录库空/不可用时勿清空刚创建的蓝本；仅在明确不在列表中时才摘掉
            if allowed and bid not in allowed:
                session.blueprint_id = ""
    return session


def get_session(home: Path, session_id: str) -> ChatSession | None:
    from report_writing.services.session_redis_store import get_session_hot, put_session_hot
    from report_writing.services.users import current_user_id, owns_user_resource

    sid = _require_id(session_id)
    uid = current_user_id()
    found: ChatSession | None = None
    from_redis = False

    hot = get_session_hot(sid)
    if isinstance(hot, dict):
        try:
            found = ChatSession.from_dict(hot)
            from_redis = True
        except Exception:
            found = None

    if found is None and _use_mysql():
        found = _db_get(sid)
        if found is not None:
            # 读路径回填 Redis
            try:
                put_session_hot(found.to_dict())
            except Exception:
                pass

    if found is None:
        # 仅作迁移兜底：历史本地 JSON
        local = _file_get(home, sid)
        if local is not None:
            found = local
            if _use_mysql():
                try:
                    _db_save(found)
                    put_session_hot(found.to_dict())
                except Exception as error:
                    _log.warning("本地会话迁移失败 %s: %s", sid, error)

    if found is None:
        return None
    owner = _session_owner_id(found, workspace_user=None if from_redis or _use_mysql() else uid)
    if not owns_user_resource(uid, owner):
        return None
    found.user_id = owner
    return _sanitize_session_bindings(home, found)


def _mark_card_used(session: ChatSession, card_key: str) -> None:
    """card_key 形如 messageIndex:cardIndex。"""
    raw = (card_key or "").strip()
    if ":" not in raw:
        return
    left, right = raw.split(":", 1)
    try:
        mi = int(left)
        ci = int(right)
    except ValueError:
        return
    keys = [str(item) for item in (session.freeform or {}).get("used_card_keys") or [] if str(item).strip()]
    if raw not in keys:
        keys.append(raw)
    session.freeform = {**(session.freeform or {}), "used_card_keys": keys}
    messages = list(session.messages or [])
    if not (0 <= mi < len(messages)):
        return
    msg = messages[mi]
    cards = list(msg.cards or [])
    if not (0 <= ci < len(cards)):
        return
    card = cards[ci]
    cards[ci] = card.model_copy(update={"used": True}) if hasattr(card, "model_copy") else card
    msg.cards = cards
    messages[mi] = msg
    session.messages = messages


def _merge_blueprint_wizard_ui_state(server: dict, client: dict) -> dict:
    """前端 ui-state 不得用空 blueprint_id / 落后步数冲掉服务端向导真值。"""
    from report_writing.chat.wizard_loop_guard import normalized_wizard_step
    from report_writing.chat.wizard_state import wizard_step_index

    server_bp = dict(server or {})
    client_bp = dict(client or {})
    merged_bp = {**server_bp, **client_bp}
    server_bid = str(server_bp.get("blueprint_id") or "").strip()
    client_bid = str(client_bp.get("blueprint_id") or "").strip()
    merged_bp["blueprint_id"] = client_bid or server_bid
    if not merged_bp.get("blueprint_id"):
        merged_bp.pop("blueprint_id", None)
    si = wizard_step_index(normalized_wizard_step(str(server_bp.get("step") or "")))
    ci = wizard_step_index(normalized_wizard_step(str(merged_bp.get("step") or "")))
    if si >= 0 and (ci < 0 or ci < si):
        merged_bp["step"] = server_bp.get("step")
        ci = si
    # 已有蓝本 id 后名称步已完成，严禁回落到 meta（否则卡片又回到「填写名称」）
    bid = str(merged_bp.get("blueprint_id") or "").strip()
    meta_i = wizard_step_index("meta")
    upload_i = wizard_step_index("upload")
    if bid and (ci < 0 or ci <= meta_i):
        merged_bp["step"] = "upload"
        ci = upload_i
    confirm_i = wizard_step_index("outline_confirm")
    chapters = merged_bp.get("chapters")
    has_chapters = isinstance(chapters, list) and len(chapters) > 0
    # 客户端已推进到确认目录或显式关掉 trigger：本轮重新提取结束
    if client_bp.get("trigger_outline_extract") is False or (
        ci >= confirm_i >= 0 and "step" in client_bp
    ):
        if str(merged_bp.get("pending_wizard_command") or "").strip() == "re_extract_outline":
            merged_bp.pop("pending_wizard_command", None)
    re_extract = str(merged_bp.get("pending_wizard_command") or "").strip() == "re_extract_outline"
    if client_bp.get("trigger_outline_extract") is False:
        merged_bp.pop("trigger_outline_extract", None)
    elif ci >= confirm_i >= 0 or (has_chapters and not re_extract):
        merged_bp.pop("trigger_outline_extract", None)
        if has_chapters and (ci < 0 or ci < confirm_i) and not re_extract:
            merged_bp["step"] = "outline_confirm"
            ci = confirm_i
    elif server_bp.get("trigger_outline_extract") and "trigger_outline_extract" not in client_bp:
        # 仅当客户端完全未提及该字段、且仍停在提取前时，才保留服务端 leftover trigger
        if ci < confirm_i and not has_chapters:
            merged_bp["trigger_outline_extract"] = True
    # 客户端显式关掉，或已到确认目录 / 已有章节：清掉「提取中」残留
    if client_bp.get("outline_extract_active") is False or (
        not re_extract and (ci >= confirm_i >= 0 or has_chapters)
    ):
        merged_bp.pop("outline_extract_active", None)
    # 一次性分析自驱：前端落库会 strip 掉这些字段。若合并时仍保留服务端残留，
    # 后续「保存章节配置」等非分析动作的 done 会再次点燃自动分析。
    if client_bp.get("trigger_begin_study") is True:
        merged_bp["trigger_begin_study"] = True
    else:
        merged_bp.pop("trigger_begin_study", None)
    client_study_keys = client_bp.get("trigger_chapter_study")
    client_chapter_keys = client_bp.get("chapter_study_keys")
    if isinstance(client_study_keys, list) and client_study_keys:
        merged_bp["trigger_chapter_study"] = [
            str(k).strip() for k in client_study_keys if str(k).strip()
        ]
    elif isinstance(client_chapter_keys, list) and client_chapter_keys:
        merged_bp["trigger_chapter_study"] = [
            str(k).strip() for k in client_chapter_keys if str(k).strip()
        ]
    else:
        # 客户端未带或显式空列表：清掉服务端 leftover，禁止非分析动作复活
        merged_bp.pop("trigger_chapter_study", None)
        merged_bp.pop("chapter_study_keys", None)
    if client_bp.get("study_stream_active") is True:
        merged_bp["study_stream_active"] = True
    else:
        merged_bp.pop("study_stream_active", None)
    from report_writing.chat.wizard_state import slim_wizard_chapters

    return slim_wizard_chapters(merged_bp)


def apply_chat_ui_state(session: ChatSession, body) -> ChatSession:
    """合并前端同步的 UI 状态（刷新/切会话后恢复）。"""
    from report_writing.domain.models import ChatCard, ChatMessage

    used = [str(item).strip() for item in (getattr(body, "used_card_keys", None) or []) if str(item).strip()]
    if used:
        session.freeform = {**(session.freeform or {}), "used_card_keys": used}
        for key in used:
            _mark_card_used(session, key)

    # 文档小窗切换：对齐焦点指针
    focus_key = str(getattr(body, "focus_document_key", "") or "").strip()
    focus_tid = str(getattr(body, "focus_task_id", "") or "").strip()
    focus_bid = str(getattr(body, "focus_blueprint_id", "") or "").strip()
    patch_peek = getattr(body, "freeform_patch", None) or {}
    if isinstance(patch_peek, dict) and not focus_key:
        focus_key = str(patch_peek.get("focus_document_key") or "").strip()
    if focus_key or focus_tid or focus_bid:
        from report_writing.chat.session_documents import (
            rebind_session_to_focus_document,
            set_focus_document_key,
        )

        if focus_key:
            set_focus_document_key(session, focus_key)
        if focus_bid:
            session.blueprint_id = focus_bid
        if focus_tid:
            session.task_id = focus_tid
        elif focus_key.startswith("bp:"):
            docs = dict((session.freeform or {}).get("session_documents") or {})
            meta = docs.get(focus_key) if isinstance(docs.get(focus_key), dict) else {}
            session.task_id = str(meta.get("writing_task_id") or "").strip()
        rebind_session_to_focus_document(session, home=None)

    patch = getattr(body, "freeform_patch", None) or {}
    if isinstance(patch, dict) and patch:
        from report_writing.chat.wizard_state import is_freeform_sample_wizard_mode

        prev_wiz_for_sample = dict((session.freeform or {}).get("blueprint_wizard") or {})
        keep_sample_wizard = is_freeform_sample_wizard_mode(prev_wiz_for_sample)
        merged = dict(session.freeform or {})
        for key, value in patch.items():
            # 前端误判 freeform 时会塞空 blueprint_wizard；依据样本向导进行中禁止清空
            if key == "blueprint_wizard" and isinstance(value, dict) and not value and keep_sample_wizard:
                continue
            if key == "runtime" and isinstance(value, dict):
                base_rt = merged.get("runtime") if isinstance(merged.get("runtime"), dict) else {}
                wf = dict(base_rt.get("workflows") or {})
                incoming_wf = value.get("workflows") if isinstance(value.get("workflows"), dict) else {}
                for wf_name, wf_body in incoming_wf.items():
                    if wf_name == "blueprint_wizard" and isinstance(wf_body, dict):
                        existing = wf.get(wf_name) if isinstance(wf.get(wf_name), dict) else {}
                        server_bp = dict(merged.get("blueprint_wizard") or existing or {})
                        wf[wf_name] = _merge_blueprint_wizard_ui_state(server_bp, wf_body)
                    elif isinstance(wf_body, dict) and isinstance(wf.get(wf_name), dict):
                        wf[wf_name] = {**wf[wf_name], **wf_body}
                    elif isinstance(wf_body, dict):
                        wf[wf_name] = dict(wf_body)
                merged["runtime"] = {**base_rt, **value, "workflows": wf}
                bp = wf.get("blueprint_wizard")
                if isinstance(bp, dict):
                    server_bp = dict(merged.get("blueprint_wizard") or {})
                    merged["blueprint_wizard"] = _merge_blueprint_wizard_ui_state(server_bp, bp)
                tpl = wf.get("template_wizard")
                if isinstance(tpl, dict):
                    merged["template_wizard"] = {**(merged.get("template_wizard") or {}), **tpl}
                ex = wf.get("export")
                if isinstance(ex, dict):
                    merged["export_prefs"] = {**(merged.get("export_prefs") or {}), **ex}
                ff_wf = wf.get("freeform")
                if isinstance(ff_wf, dict):
                    for key in ("category", "title", "outline_text", "plan"):
                        if key in ff_wf and ff_wf[key] not in (None, ""):
                            merged[key] = ff_wf[key]
                continue
            if key == "blueprint_wizard" and isinstance(value, dict):
                merged[key] = _merge_blueprint_wizard_ui_state(dict(merged.get(key) or {}), value)
                continue
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = {**merged[key], **value}
            else:
                merged[key] = value
        session.freeform = merged
        from report_writing.chat.session_runtime import migrate_legacy_freeform

        migrate_legacy_freeform(session)
        from report_writing.chat.session_state import user_chose_skip_blueprint

        # 无蓝本路径：ui-state 不得用残留向导回填 blueprint_id / 复活上传样板流程
        # 依据样本/创建早期向导例外：清掉会导致上传后「提取目录」卡丢失
        wiz_keep = dict((session.freeform or {}).get("blueprint_wizard") or {})
        if keep_sample_wizard and not wiz_keep:
            wiz_keep = dict(prev_wiz_for_sample)
            merged = dict(session.freeform or {})
            merged["blueprint_wizard"] = wiz_keep
            session.freeform = merged
            wiz_keep = dict((session.freeform or {}).get("blueprint_wizard") or {})
        if user_chose_skip_blueprint(session) and not is_freeform_sample_wizard_mode(wiz_keep) and not keep_sample_wizard:
            ff = dict(session.freeform or {})
            ff.pop("blueprint_wizard", None)
            rt = dict(ff.get("runtime") or {})
            workflows = dict(rt.get("workflows") or {})
            if "blueprint_wizard" in workflows:
                workflows.pop("blueprint_wizard", None)
                rt["workflows"] = workflows
                ff["runtime"] = rt
            session.freeform = ff
            session.blueprint_id = ""
        else:
            from report_writing.services.task_ids import is_writing_task_id

            wiz_bid = str(((session.freeform or {}).get("blueprint_wizard") or {}).get("blueprint_id") or "").strip()
            # w* 样本任务不是库蓝本，勿写入 session.blueprint_id
            if (
                wiz_bid
                and not is_writing_task_id(wiz_bid)
                and not str(session.blueprint_id or "").strip()
            ):
                session.blueprint_id = wiz_bid

    chapter_edits = getattr(body, "chapter_edits", None)
    if isinstance(chapter_edits, dict) and chapter_edits:
        from report_writing.chat.chapter_client_sync import merge_client_chapter_edits

        merge_client_chapter_edits(session, chapter_edits)
    elif isinstance(patch, dict) and isinstance(patch.get("chapter_edits"), dict):
        from report_writing.chat.chapter_client_sync import merge_client_chapter_edits

        merge_client_chapter_edits(session, patch.get("chapter_edits"))

    for item in getattr(body, "message_patches", None) or []:
        if not isinstance(item, dict):
            continue
        try:
            index = int(item.get("index"))
        except (TypeError, ValueError):
            continue
        messages = list(session.messages or [])
        if index < 0:
            index = len(messages) + index
        if not (0 <= index < len(messages)):
            continue
        msg = messages[index]
        # 正文以本轮落库为准。按序号回写 text 会在刷新后把 AI 回复盖到用户气泡上。
        patch_role = str(item.get("role") or "").strip()
        if patch_role and patch_role != str(getattr(msg, "role", "") or "").strip():
            continue
        if "steps" in item and isinstance(item.get("steps"), list):
            msg.steps = list(item.get("steps") or [])
        if "cards" in item and isinstance(item.get("cards"), list):
            cards = _merge_message_cards_no_wizard_regression(
                list(getattr(msg, "cards", None) or []),
                list(item.get("cards") or []),
            )
            msg.cards = cards
        messages[index] = msg
        session.messages = messages
    from report_writing.chat.dialogue import drop_assistant_echo_user_messages

    drop_assistant_echo_user_messages(session)
    return session


def _merge_message_cards_no_wizard_regression(server_cards: list, client_cards: list) -> list:
    """ui-state 回写卡片时，蓝本向导卡不得步骤回退；有 blueprint_id 时不得停在 meta。"""
    from report_writing.domain.models import ChatCard
    from report_writing.chat.wizard_loop_guard import normalized_wizard_step
    from report_writing.chat.wizard_state import wizard_step_index

    def _as_card(raw):
        if isinstance(raw, ChatCard):
            return raw
        if isinstance(raw, dict):
            return ChatCard.model_validate(raw)
        return None

    if not client_cards:
        return list(server_cards or [])
    if not server_cards:
        out = []
        for raw in client_cards:
            card = _as_card(raw)
            if card is not None:
                out.append(_floor_wizard_card(card))
        return out

    merged: list[ChatCard] = []
    n = max(len(server_cards), len(client_cards))
    for i in range(n):
        prev = server_cards[i] if i < len(server_cards) else None
        incoming = _as_card(client_cards[i]) if i < len(client_cards) else None
        if incoming is None:
            if prev is not None:
                merged.append(prev)
            continue
        if prev is None:
            merged.append(_floor_wizard_card(incoming))
            continue
        if getattr(prev, "type", "") == "blueprint_wizard" or getattr(incoming, "type", "") == "blueprint_wizard":
            prev_p = dict(getattr(prev, "payload", None) or {})
            new_p = dict(getattr(incoming, "payload", None) or {})
            bid = str(new_p.get("blueprint_id") or prev_p.get("blueprint_id") or "").strip()
            prev_s = normalized_wizard_step(str(prev_p.get("step") or ""))
            new_s = normalized_wizard_step(str(new_p.get("step") or ""))
            pi = wizard_step_index(prev_s)
            ni = wizard_step_index(new_s)
            step = new_s if ni >= pi else prev_s
            if bid and wizard_step_index(step) <= wizard_step_index("meta"):
                step = "upload"
            from report_writing.chat.wizard_state import slim_wizard_chapters

            payload = slim_wizard_chapters({**prev_p, **new_p, "step": step})
            if bid:
                payload["blueprint_id"] = bid
            title = str(getattr(incoming, "title", "") or getattr(prev, "title", "") or "")
            if step != "meta" and ("填写名称" in title or title.endswith("· 名称")):
                from report_writing.chat.wizard_state import _wizard_card_title

                title = _wizard_card_title(step)
            merged.append(
                prev.model_copy(
                    update={
                        "type": "blueprint_wizard",
                        "title": title or getattr(prev, "title", ""),
                        "payload": payload,
                        "used": bool(getattr(incoming, "used", None) or getattr(prev, "used", False)),
                    }
                )
            )
        else:
            merged.append(incoming)
    return merged


def _floor_wizard_card(card):
    from report_writing.chat.wizard_loop_guard import normalized_wizard_step
    from report_writing.chat.wizard_state import wizard_step_index

    if getattr(card, "type", "") != "blueprint_wizard":
        return card
    from report_writing.chat.wizard_state import slim_wizard_chapters

    payload = slim_wizard_chapters(dict(getattr(card, "payload", None) or {}))
    bid = str(payload.get("blueprint_id") or "").strip()
    step = normalized_wizard_step(str(payload.get("step") or ""))
    if bid and wizard_step_index(step) <= wizard_step_index("meta"):
        payload["step"] = "upload"
        payload["blueprint_id"] = bid
        from report_writing.chat.wizard_state import _wizard_card_title

        title = _wizard_card_title("upload")
        return card.model_copy(update={"payload": payload, "title": title})
    return card


def session_save_should_be_durable(
    *,
    action: str = "",
    wizard_step: str = "",
    durable: bool | None = None,
) -> bool:
    """关键用户可见步骤同步落 MySQL；其余可异步。"""
    if durable is not None:
        return bool(durable)
    act = (action or "").strip()
    step = (wizard_step or "").strip()
    if act in _DURABLE_ACTIONS:
        return True
    if act == "blueprint_wizard" and step in _DURABLE_WIZARD_STEPS:
        return True
    if act == "blueprint_wizard" and step in {"outline", "upload", "study"}:
        # 开始提取/学习可异步；完成步已在 _DURABLE_WIZARD_STEPS
        return False
    # 普通用户发话默认 durable，避免只剩 Redis
    if not act:
        return True
    return False


def save_session(
    home: Path,
    session: ChatSession,
    *,
    durable: bool | None = None,
    action: str = "",
    wizard_step: str = "",
) -> ChatSession:
    """写 Redis 热存并入刷库队列；durable 时同步 MySQL。"""
    from report_writing.infra.redis_cache import redis_enabled
    from report_writing.services.session_redis_store import (
        enqueue_session_flush,
        ensure_session_flush_worker,
        put_session_hot,
    )
    from report_writing.services.users import current_user_id

    session.updated_at = _now()
    _require_id(session.id)
    if not (session.user_id or "").strip():
        session.user_id = current_user_id()
    session.revision = int(getattr(session, "revision", 0) or 0) + 1
    stored = session.model_copy(deep=True)
    if getattr(stored, "runtime", None) is not None:
        stored.runtime = None

    must_durable = session_save_should_be_durable(
        action=action, wizard_step=wizard_step, durable=durable
    )
    mysql_ok = _use_mysql()
    redis_ok = bool(redis_enabled())
    hot_ok = False
    if redis_ok:
        try:
            payload = stored.to_dict()
            hot_ok = put_session_hot(payload)
            if not hot_ok:
                # revision 落后：以热存为准合并后再写一次，避免丢「已建好」对话
                from report_writing.services.session_redis_store import (
                    get_session_hot,
                    _merge_hot_session_preserve_progress,
                )

                hot = get_session_hot(stored.id)
                if isinstance(hot, dict):
                    merged = _merge_hot_session_preserve_progress(hot, payload)
                    hot_rev = int(hot.get("revision") or 0)
                    merged["revision"] = max(hot_rev, int(merged.get("revision") or 0)) + 1
                    hot_ok = put_session_hot(merged)
                    if hot_ok:
                        stored.revision = int(merged["revision"])
                        try:
                            restored = ChatSession.from_dict(merged)
                            session.messages = list(restored.messages or [])
                            session.freeform = dict(restored.freeform or {})
                            if restored.blueprint_id:
                                session.blueprint_id = restored.blueprint_id
                        except Exception:
                            pass
                if not hot_ok:
                    _log.warning(
                        "会话 Redis 热写被更新修订挡住 %s@%s",
                        session.id,
                        stored.revision,
                    )
        except Exception as error:
            _log.warning("会话 Redis 热写失败 %s: %s", session.id, error)
            hot_ok = False

    if hot_ok:
        from report_writing.services.session_redis_store import register_flush_handler

        register_flush_handler(flush_session_revision_to_mysql)
        enqueue_session_flush(stored.id, int(stored.revision or 0))
        ensure_session_flush_worker()

    if mysql_ok and (must_durable or not hot_ok):
        try:
            _db_save_if_newer(stored)
        except Exception as error:
            _log.error("会话同步 MySQL 失败 %s@%s: %s", session.id, stored.revision, error)
            if not hot_ok:
                # 双失败才落本地兜底
                try:
                    _file_save(home, stored)
                except Exception as file_error:
                    _log.error("会话本地兜底也失败 %s: %s", session.id, file_error)
                raise
    elif not mysql_ok:
        # 无 MySQL 时即使 Redis 热写成功也落本地，供历史侧栏扫摘要（不依赖热存枚举）
        try:
            _file_save(home, stored)
        except Exception as file_error:
            if not hot_ok:
                _log.error("会话本地落盘失败 %s: %s", session.id, file_error)
                raise
            _log.warning("会话本地落盘失败（热存已写入）%s: %s", session.id, file_error)

    # 内存对象与落盘 revision 对齐
    session.revision = stored.revision
    session.updated_at = stored.updated_at
    return session


def flush_session_revision_to_mysql(session_id: str, revision: int) -> None:
    """刷库 worker：以 Redis 热快照为准，CAS 写入 MySQL。"""
    from report_writing.services.session_redis_store import get_session_hot

    sid = (session_id or "").strip()
    if not sid or not _use_mysql():
        return
    hot = get_session_hot(sid)
    if not isinstance(hot, dict):
        # 热数据已过期：若库已有更新 revision 则跳过
        return
    try:
        snap = ChatSession.from_dict(hot)
    except Exception:
        return
    snap_rev = int(getattr(snap, "revision", 0) or 0)
    # 队列里的 revision 可能已过期；始终刷 Redis 上最新快照（仍做 CAS）
    if snap_rev < int(revision or 0):
        return
    if getattr(snap, "runtime", None) is not None:
        snap.runtime = None
    _db_save_if_newer(snap)


def _db_save_if_newer(session: ChatSession) -> bool:
    """仅当 document.revision 不低于库内时写入；返回是否写入。"""
    existing = _db_get(session.id)
    if existing is not None:
        old_rev = int(getattr(existing, "revision", 0) or 0)
        new_rev = int(getattr(session, "revision", 0) or 0)
        if old_rev > new_rev:
            return False
    _db_save(session)
    return True


def load_or_create(home: Path, session_id: str = "", *, robot_id: str = "") -> ChatSession:
    from report_writing.services.robots import get_robot

    sid = (session_id or "").strip()
    if sid:
        existing = get_session(home, sid)
        if existing is None:
            raise KeyError(sid)
        incoming = (robot_id or "").strip()
        if incoming:
            existing.robot_id = incoming if get_robot(home, incoming) is not None else ""
        _sanitize_session_bindings(home, existing)
        save_session(home, existing)
        return existing
    rid = (robot_id or "").strip()
    if rid and get_robot(home, rid) is None:
        rid = ""
    return create_session(home, robot_id=rid)


def delete_session(home: Path, session_id: str) -> bool:
    """删除当前用户的会话（MySQL / Redis / 本地 JSON 一并清掉）。"""
    found = get_session(home, session_id)
    if found is None:
        return False
    sid = found.id
    from report_writing.infra.redis_cache import invalidate_session_cache
    from report_writing.services.session_redis_store import drop_session_hot

    drop_session_hot(sid)
    invalidate_session_cache(sid)
    if _use_mysql():
        _db_delete(sid)
    path = sessions_root(home) / f"{sid}.json"
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
    return True


def list_sessions(home: Path, *, limit: int = 50, robot_id: str = "") -> list[ChatSessionSummary]:
    """当前用户的会话摘要；只返回轻量字段，不加载 messages/document。"""
    from report_writing.services.users import current_user_id

    cap = max(1, min(int(limit or 50), 200))
    rid = (robot_id or "").strip()
    uid = current_user_id()
    if _use_mysql():
        # MySQL 可用时只查摘要列，不再每次扫本地全量 JSON（迁移走 sync_chat_sessions_from_disk）
        return _db_list(limit=cap, robot_id=rid, user_id=uid)
    return _file_list(home, limit=cap, robot_id=rid, user_id=uid)


def session_summary(session: ChatSession) -> ChatSessionSummary:
    title, preview = _session_title_preview(session)
    return ChatSessionSummary(
        id=session.id,
        title=title,
        preview=preview,
        robot_id=session.robot_id or "",
        phase=session.phase or ChatPhase.INTENT.value,
        message_count=len(session.messages or []),
        updated_at=session.updated_at or "",
        created_at=session.created_at or "",
    )


def sync_chat_sessions_from_disk(home: Path) -> int:
    """将 data/chat_sessions/*.json upsert 到 MySQL。"""
    if not _use_mysql():
        return 0
    saved = 0
    for path in sessions_root(home).glob("*.json"):
        try:
            session = ChatSession.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            continue
        try:
            _require_id(session.id)
            _db_save(session)
            saved += 1
        except Exception:
            continue
    return saved


def _use_mysql() -> bool:
    if not mysql_available():
        return False
    if _schema_ready:
        return True
    settings = load_database_settings()
    if settings is None:
        return False
    try:
        with _connect(settings) as connection:
            _ensure_chat_schema(connection)
        return True
    except Exception as error:
        _log.warning("聊天会话改用本地 JSON 存储：%s", error)
        return False


def _db_get(session_id: str) -> ChatSession | None:
    settings = load_database_settings()
    if settings is None:
        return None
    with _connect(settings) as connection:
        _ensure_chat_schema(connection)
        row = connection.execute(
            "SELECT document FROM writing_chat_session WHERE id = %s",
            (session_id,),
        ).fetchone()
    if row is None:
        return None
    return ChatSession.from_dict(_as_dict(row[0]))


def _db_save(session: ChatSession) -> None:
    from report_writing.services.users import current_user_id, normalize_owner_id

    settings = load_database_settings()
    if settings is None:
        raise RuntimeError("未配置数据库")
    if not (session.user_id or "").strip():
        session.user_id = current_user_id()
    doc = session.to_dict()
    doc["user_id"] = normalize_owner_id(session.user_id)
    payload = json.dumps(doc, ensure_ascii=False)
    uid = normalize_owner_id(session.user_id)
    summary = session_summary(session)
    with _connect(settings) as connection:
        _ensure_chat_schema(connection)
        connection.execute(
            """
            INSERT INTO writing_chat_session (
                id, user_id, robot_id, phase, document, title, preview, message_count, created_at, updated_at
            )
            VALUES (%s, %s, %s, %s, CAST(%s AS JSON), %s, %s, %s, %s, %s) AS incoming
            ON DUPLICATE KEY UPDATE
                user_id = incoming.user_id,
                robot_id = incoming.robot_id,
                phase = incoming.phase,
                document = incoming.document,
                title = incoming.title,
                preview = incoming.preview,
                message_count = incoming.message_count,
                updated_at = incoming.updated_at
            """,
            (
                session.id,
                uid,
                session.robot_id or "",
                session.phase or ChatPhase.INTENT.value,
                payload,
                summary.title or "",
                summary.preview or "",
                int(summary.message_count or 0),
                session.created_at or session.updated_at or _now(),
                session.updated_at or _now(),
            ),
        )


def _file_get(home: Path, session_id: str) -> ChatSession | None:
    path = sessions_root(home) / f"{session_id}.json"
    if not path.is_file():
        return None
    return ChatSession.from_dict(json.loads(path.read_text(encoding="utf-8")))


def _db_delete(session_id: str) -> None:
    settings = load_database_settings()
    if settings is None:
        return
    with _connect(settings) as connection:
        _ensure_chat_schema(connection)
        connection.execute("DELETE FROM writing_chat_session WHERE id = %s", (session_id,))


def _file_save(home: Path, session: ChatSession) -> ChatSession:
    path = sessions_root(home) / f"{session.id}.json"
    path.write_text(json.dumps(session.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    return session


def _summary_from_raw_dict(data: dict) -> ChatSessionSummary | None:
    """从原始 JSON dict 抽摘要，避免 ChatSession.from_dict 解析整棵卡片树。"""
    if not isinstance(data, dict):
        return None
    messages = data.get("messages")
    if not isinstance(messages, list) or not messages:
        return None
    sid = str(data.get("id") or "").strip()
    if not sid:
        return None
    # 只扫到首条 user + assistant 即可；不解析 cards
    light_msgs: list[ChatMessage] = []
    saw_user = False
    saw_assistant = False
    for m in messages:
        if not isinstance(m, dict):
            continue
        role = str(m.get("role") or "").strip()
        text = str(m.get("text") or "")
        if not text.strip():
            continue
        if role == "user" and not saw_user:
            light_msgs.append(ChatMessage(role="user", text=text))
            saw_user = True
        elif role == "assistant" and not saw_assistant:
            light_msgs.append(ChatMessage(role="assistant", text=text))
            saw_assistant = True
        if saw_user and saw_assistant:
            break
    if not light_msgs:
        return None
    light = ChatSession(
        id=sid,
        robot_id=str(data.get("robot_id") or ""),
        phase=str(data.get("phase") or ChatPhase.INTENT.value),
        user_id=str(data.get("user_id") or ""),
        created_at=str(data.get("created_at") or ""),
        updated_at=str(data.get("updated_at") or ""),
        messages=light_msgs,
    )
    summary = session_summary(light)
    # message_count 用真实条数，不是轻量截断后的 1～2
    summary.message_count = len(messages)
    return summary


def _file_list(home: Path, *, limit: int, robot_id: str = "", user_id: str = "default") -> list[ChatSessionSummary]:
    from report_writing.services.users import owns_user_resource

    root = sessions_root(home)
    rows: list[ChatSessionSummary] = []
    for path in root.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        owner = str(data.get("user_id") or user_id or "default").strip() or "default"
        if not owns_user_resource(user_id, owner):
            continue
        if robot_id and str(data.get("robot_id") or "") != robot_id:
            continue
        summary = _summary_from_raw_dict(data)
        if summary is None:
            continue
        rows.append(summary)
    rows.sort(key=lambda s: s.updated_at or s.created_at or "", reverse=True)
    return rows[:limit]


def _db_list(*, limit: int, robot_id: str = "", user_id: str = "default") -> list[ChatSessionSummary]:
    """只查摘要列；缺 title 的旧行再按需补一次（仍避免每次拉全量 document）。"""
    from report_writing.services.users import owns_user_resource

    settings = load_database_settings()
    if settings is None:
        return []
    cap = max(1, min(int(limit or 50), 200))
    with _connect(settings) as connection:
        _ensure_chat_schema(connection)
        params: list = [user_id]
        where_sql = "user_id = %s"
        if robot_id:
            where_sql += " AND robot_id = %s"
            params.append(robot_id)
        # 多取一些再过滤空会话；绝不 SELECT document
        fetch_n = min(cap * 3, 200)
        sql = f"""
            SELECT id, user_id, robot_id, phase, title, preview, message_count, created_at, updated_at
            FROM writing_chat_session
            WHERE {where_sql}
            ORDER BY updated_at DESC
            LIMIT %s
        """
        params.append(fetch_n)
        try:
            rows = connection.execute(sql, tuple(params)).fetchall()
        except Exception:
            return _db_list_legacy_documents(connection, limit=cap, robot_id=robot_id, user_id=user_id)

    # row: id, user_id, robot_id, phase, title, preview, message_count, created_at, updated_at
    out: list[ChatSessionSummary] = []
    need_backfill: list[str] = []
    for row in rows or []:
        if not row:
            continue
        sid = str(row[0] or "").strip()
        owner = str(row[1] or "default").strip() or "default"
        if not sid or not owns_user_resource(user_id, owner):
            continue
        rid = str(row[2] or "")
        if robot_id and rid != robot_id:
            continue
        title = str(row[4] or "").strip()
        preview = str(row[5] or "").strip()
        try:
            msg_n = int(row[6] or 0)
        except (TypeError, ValueError):
            msg_n = 0
        if msg_n <= 0 and not title:
            # 旧行尚未回填摘要：稍后补，不在列表 SQL 里碰 document
            need_backfill.append(sid)
            continue
        if msg_n <= 0:
            continue
        out.append(
            ChatSessionSummary(
                id=sid,
                title=title or "历史对话",
                preview=preview,
                robot_id=rid,
                phase=str(row[3] or ChatPhase.INTENT.value),
                message_count=msg_n,
                created_at=str(row[7] or ""),
                updated_at=str(row[8] or ""),
            )
        )
        if len(out) >= cap:
            break
    if need_backfill and len(out) < cap:
        _db_backfill_summaries(need_backfill[: max(20, cap - len(out))])
        filled = _db_list_by_ids(need_backfill[: max(20, cap - len(out))])
        for item in filled:
            if item.message_count <= 0:
                continue
            if any(x.id == item.id for x in out):
                continue
            out.append(item)
            if len(out) >= cap:
                break
        out.sort(key=lambda s: s.updated_at or s.created_at or "", reverse=True)
    return out[:cap]


def _db_list_by_ids(ids: list[str]) -> list[ChatSessionSummary]:
    settings = load_database_settings()
    if settings is None or not ids:
        return []
    out: list[ChatSessionSummary] = []
    with _connect(settings) as connection:
        for sid in ids:
            row = connection.execute(
                """
                SELECT id, robot_id, phase, title, preview, message_count, created_at, updated_at
                FROM writing_chat_session WHERE id = %s
                """,
                (sid,),
            ).fetchone()
            if not row:
                continue
            out.append(
                ChatSessionSummary(
                    id=str(row[0]),
                    robot_id=str(row[1] or ""),
                    phase=str(row[2] or ""),
                    title=str(row[3] or "") or "历史对话",
                    preview=str(row[4] or ""),
                    message_count=int(row[5] or 0),
                    created_at=str(row[6] or ""),
                    updated_at=str(row[7] or ""),
                )
            )
    return out


def _db_backfill_summaries(ids: list[str]) -> None:
    """旧行缺 title/preview 时，只为这几条读 document 回填摘要列。"""
    settings = load_database_settings()
    if settings is None or not ids:
        return
    with _connect(settings) as connection:
        for sid in ids:
            row = connection.execute(
                "SELECT document FROM writing_chat_session WHERE id = %s",
                (sid,),
            ).fetchone()
            if not row:
                continue
            try:
                data = _as_dict(row[0])
                summary = _summary_from_raw_dict(data)
            except Exception:
                continue
            if summary is None:
                continue
            try:
                connection.execute(
                    """
                    UPDATE writing_chat_session
                    SET title = %s, preview = %s, message_count = %s
                    WHERE id = %s
                    """,
                    (summary.title, summary.preview, int(summary.message_count or 0), sid),
                )
            except Exception:
                continue


def _db_list_legacy_documents(
    connection,
    *,
    limit: int,
    robot_id: str,
    user_id: str,
) -> list[ChatSessionSummary]:
    from report_writing.services.users import owns_user_resource

    where = "user_id = %s"
    params: list = [user_id]
    if robot_id:
        where += " AND robot_id = %s"
        params.append(robot_id)
    id_rows = connection.execute(
        f"SELECT id FROM writing_chat_session WHERE {where} ORDER BY updated_at DESC LIMIT %s",
        tuple(params) + (limit,),
    ).fetchall()
    out: list[ChatSessionSummary] = []
    for id_row in id_rows or []:
        sid = str(id_row[0] or "").strip()
        if not sid:
            continue
        row = connection.execute(
            "SELECT document FROM writing_chat_session WHERE id = %s",
            (sid,),
        ).fetchone()
        if not row:
            continue
        try:
            data = _as_dict(row[0])
        except Exception:
            continue
        owner = str(data.get("user_id") or "default")
        if not owns_user_resource(user_id, owner):
            continue
        summary = _summary_from_raw_dict(data)
        if summary is not None:
            out.append(summary)
    return out


def _session_title_preview(session: ChatSession) -> tuple[str, str]:
    def plain(text: str) -> str:
        value = re.sub(r"<[^>]+>", " ", text or "")
        value = re.sub(r"\s+", " ", value).strip()
        return value

    first_user = ""
    first_assistant = ""
    for msg in session.messages or []:
        role = (msg.role or "").strip()
        text = plain(msg.text or "")
        if not text:
            continue
        if role == "user" and not first_user:
            first_user = text
        elif role == "assistant" and not first_assistant:
            first_assistant = text
        if first_user and first_assistant:
            break
    title = first_user or first_assistant or "新对话"
    preview = first_assistant or first_user or ""
    if len(title) > 36:
        title = title[:36] + "…"
    if len(preview) > 72:
        preview = preview[:72] + "…"
    return title, preview


def _as_dict(payload) -> dict:
    if isinstance(payload, (bytes, bytearray)):
        payload = payload.decode("utf-8")
    if isinstance(payload, str):
        payload = json.loads(payload)
    if not isinstance(payload, dict):
        raise ValueError("会话 document 格式无效")
    return payload


def _require_id(session_id: str) -> str:
    value = str(session_id or "").strip()
    if not _ID_SAFE.match(value):
        raise ValueError("会话编号不合法")
    return value


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")
