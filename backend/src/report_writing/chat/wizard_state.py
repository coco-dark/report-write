"""蓝本对话向导：以蓝本任务状态（MySQL / 目录库）推导 step，与会话 freeform 对齐。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from report_writing.domain.models import ChatActionPayload, ChatPayload, ChatSession, WritingTask

# 向导止于学习分析；历史 step=done 由 alias 归一到 study
_WIZARD_STEP_ORDER = ("meta", "upload", "outline", "outline_confirm", "study")
# 会话/卡片只带预览；全量目录走 /doc-outline，避免 200+ 章反复写入 ui-state
WIZARD_CHAPTER_PREVIEW_LIMIT = 8

# create = 兼容旧称；freeform_sample = 无蓝本·依据样本（原创建向导，不入库）
# selected = 选用已有库蓝本（无 meta/upload/extract）
WIZARD_MODE_CREATE = "create"
WIZARD_MODE_FREEFORM_SAMPLE = "freeform_sample"
WIZARD_MODE_SELECTED = "selected"

SELECTED_WIZARD_STEPS: list[dict[str, str]] = [
    {"id": "outline_confirm", "label": "确认目录"},
    {"id": "study", "label": "分析要点"},
]

CREATE_ONLY_WIZARD_COMMANDS = frozenset({"re_upload_sample", "re_extract_outline"})
CREATE_ONLY_WIZARD_STEPS = frozenset({"meta", "upload", "outline", "upload_done"})


def wizard_mode(wizard: dict[str, Any] | None) -> str:
    return str((wizard or {}).get("mode") or "").strip()


def is_selected_wizard_mode(wizard: dict[str, Any] | None) -> bool:
    return wizard_mode(wizard) == WIZARD_MODE_SELECTED


def is_freeform_sample_wizard_mode(wizard: dict[str, Any] | None) -> bool:
    mode = wizard_mode(wizard)
    return mode in {WIZARD_MODE_FREEFORM_SAMPLE, WIZARD_MODE_CREATE}


def is_selected_blueprint_session(session: ChatSession) -> bool:
    """已选用（或创建完成后绑定）现成蓝本：对话可确认/学习/开写，不走创建向导。"""
    from report_writing.chat.session_state import user_chose_skip_blueprint

    if user_chose_skip_blueprint(session):
        return False
    wizard = dict((session.freeform or {}).get("blueprint_wizard") or {})
    if is_selected_wizard_mode(wizard):
        return True
    return False


def seed_selected_blueprint_wizard(
    home: Path,
    session: ChatSession,
    blueprint_id: str,
) -> dict[str, Any]:
    """选用已有蓝本后：按任务进度落到确认/学习/完成，标记 mode=selected，不开创建向导。"""
    bid = (blueprint_id or "").strip()
    if not bid:
        return {}
    # 先写入 mode，再 sync，避免 sync 丢掉选用态
    prev = dict((session.freeform or {}).get("blueprint_wizard") or {})
    prev["blueprint_id"] = bid
    prev["mode"] = WIZARD_MODE_SELECTED
    # 清掉创建向导一次性触发，避免选用后误跑上传/提取
    for key in (
        "trigger_outline_extract",
        "outline_extract_active",
        "pending_wizard_command",
        "trigger_begin_study",
        "trigger_chapter_study",
        "chapter_study_keys",
    ):
        prev.pop(key, None)
    session.blueprint_id = bid
    session.freeform = {**(session.freeform or {}), "blueprint_wizard": prev}
    wizard = sync_blueprint_wizard_session(home, session, blueprint_id=bid) or dict(
        (session.freeform or {}).get("blueprint_wizard") or {}
    )
    wizard["mode"] = WIZARD_MODE_SELECTED
    wizard["blueprint_id"] = bid
    step = str(wizard.get("step") or "").strip()
    # 选用路径不得停在创建步；历史 done 归一到 study
    if step in CREATE_ONLY_WIZARD_STEPS or not step:
        wizard["step"] = "outline_confirm"
    elif step == "done":
        wizard["step"] = "study"
    session.freeform = {**(session.freeform or {}), "blueprint_wizard": wizard}
    from report_writing.chat.session_runtime import WORKFLOW_BLUEPRINT, workflow_set

    workflow_set(session, WORKFLOW_BLUEPRINT, dict(wizard))
    return wizard


def _chapter_preview(task: WritingTask | None, limit: int | None = WIZARD_CHAPTER_PREVIEW_LIMIT) -> list[dict[str, Any]]:
    """向导卡片用的短预览。全量目录由文档窗 /doc-outline 加载。"""
    if task is None or task.outline is None:
        return []
    nodes = list(task.outline.chapters or [])
    if limit is not None and limit > 0:
        nodes = nodes[:limit]
    return [
        {
            "key": n.key,
            "sequence": n.sequence,
            "title": n.title,
            "parent": getattr(n, "parent", "") or "",
            "parent_sequence": getattr(n, "parent_sequence", "") or "",
            "level": getattr(n, "level", 1) or 1,
            "structure_only": bool(getattr(n, "structure_only", False)),
        }
        for n in nodes
    ]


def slim_chapter_dicts(chapters: list[Any] | None, *, limit: int = WIZARD_CHAPTER_PREVIEW_LIMIT) -> tuple[list[dict[str, Any]], int]:
    raw = [item for item in (chapters or []) if isinstance(item, dict) and str(item.get("key") or "").strip()]
    total = len(raw)
    keep = raw[: max(1, int(limit or WIZARD_CHAPTER_PREVIEW_LIMIT))] if total > limit else raw
    preview: list[dict[str, Any]] = []
    for item in keep:
        preview.append(
            {
                "key": str(item.get("key") or "").strip(),
                "sequence": item.get("sequence") or "",
                "title": item.get("title") or item.get("key") or "",
                "parent": item.get("parent") or "",
                "parent_sequence": item.get("parent_sequence") or "",
                "level": item.get("level") or 1,
                "structure_only": bool(item.get("structure_only")),
            }
        )
    return preview, total


def slim_wizard_chapters(wizard: dict[str, Any] | None) -> dict[str, Any]:
    """会话/卡片里的目录只留短预览，全量以 chapter_count 表示。"""
    data = dict(wizard or {})
    raw = data.get("chapters") if isinstance(data.get("chapters"), list) else []
    preview, total = slim_chapter_dicts(raw)
    try:
        count = int(data.get("chapter_count") or 0)
    except (TypeError, ValueError):
        count = 0
    if total or count:
        data["chapters"] = preview
        data["chapter_count"] = max(count, total)
    return data


def partition_wizard_study(task: WritingTask | None) -> dict[str, list]:
    """向导「学习/完成」判定：与 is_chapter_analyzed 对齐（要点/检索词/简述等）。"""
    from report_writing.chat.gates import is_chapter_analyzed

    chapters = list(task.outline.chapters or []) if task and task.outline else []
    analyzed: list = []
    unanalyzed: list = []
    structure: list = []
    for node in chapters:
        if bool(getattr(node, "structure_only", False)):
            structure.append(node)
        elif is_chapter_analyzed(node):
            analyzed.append(node)
        else:
            unanalyzed.append(node)
    return {"analyzed": analyzed, "unanalyzed": unanalyzed, "structure": structure}


def mark_wizard_past_outline_confirm(session: ChatSession) -> None:
    """开写/重写后：会话向导不得再停在确认目录，避免回合收尾把确认卡贴回对话。"""
    wiz = dict((session.freeform or {}).get("blueprint_wizard") or {})
    if not wiz:
        return
    from report_writing.chat.wizard_loop_guard import normalized_wizard_step

    step = normalized_wizard_step(str(wiz.get("step") or "").strip())
    wiz["outline_confirmed"] = True
    if wizard_step_index(step) < wizard_step_index("study"):
        wiz["step"] = "study"
        wiz.pop("trigger_outline_extract", None)
        pending = str(wiz.get("pending_wizard_command") or "").strip()
        if pending in {"go_outline_confirm", "re_extract_outline"}:
            wiz.pop("pending_wizard_command", None)
    session.freeform = {**(session.freeform or {}), "blueprint_wizard": wiz}


def derive_blueprint_wizard_step(home: Path, blueprint_id: str) -> tuple[str, dict[str, Any]]:
    """根据蓝本任务实际进度推导向导步（单一事实来源）。"""
    from report_writing.chat.helpers import _load_task

    bid = (blueprint_id or "").strip()
    if not bid:
        return "meta", {}
    extra: dict[str, Any] = {"blueprint_id": bid}
    task = _load_task(home, bid)
    if task is None:
        # 已有 blueprint_id 即名称步已完成；勿因任务尚未可读而倒回 meta（会刷回「填写名称」卡片）
        return "upload", extra
    extra["title"] = (task.title or "").strip()
    if not getattr(task, "sample_ready", False):
        return "upload", extra
    full_n = len(list(task.outline.chapters or []) if task.outline else [])
    if not full_n:
        return "outline", extra
    extra["chapter_count"] = full_n
    extra["chapters"] = _chapter_preview(task)
    pending_study_early = list(getattr(task, "pending_study", None) or [])
    parts_probe = partition_wizard_study(task)
    # 已有学习要点/草案/成稿时，即使确认位丢失也不得打回「确认目录」
    writing_already = False
    try:
        from report_writing.domain.models import ChapterStatus

        chapters = list(getattr(task, "chapters", None) or [])
        done_statuses = {
            ChapterStatus.AWAITING_CONFIRM,
            ChapterStatus.CONFIRMED,
            ChapterStatus.WRITING,
            ChapterStatus.CHECKING,
        }
        writing_already = any(
            bool(str(getattr(c, "draft_text", "") or "").strip())
            or getattr(c, "status", None) in done_statuses
            for c in chapters
        )
    except Exception:
        writing_already = False
    study_already = bool(pending_study_early) or bool(parts_probe["analyzed"]) or writing_already
    if not task.outline_confirmed and not study_already:
        return "outline_confirm", extra
    if not task.outline_confirmed and study_already:
        # 进度真值已越过确认；同步会话时一并视为已确认
        extra["outline_confirmed"] = True
    parts = partition_wizard_study(task)
    analyzed_n = len(parts["analyzed"])
    unanalyzed_n = len(parts["unanalyzed"])
    writable_total = analyzed_n + unanalyzed_n
    pending_study = list(getattr(task, "pending_study", None) or [])
    extra["study_analyzed"] = analyzed_n
    extra["study_unanalyzed"] = unanalyzed_n
    extra["study_total"] = writable_total or full_n
    extra["study_pending_confirm"] = len(pending_study) > 0
    if pending_study:
        extra["study_mode"] = "confirm_pending"
    elif analyzed_n > 0 and unanalyzed_n > 0:
        extra["study_mode"] = "continue"
    elif analyzed_n > 0 and unanalyzed_n == 0:
        extra["study_mode"] = "complete"
    else:
        extra["study_mode"] = "start"
    # 学完也不再进独立「完成」步，停在学习分析（study_mode=complete 表示已齐）
    return "study", extra


def sync_blueprint_wizard_session(
    home: Path,
    session: ChatSession,
    *,
    blueprint_id: str | None = None,
) -> dict[str, Any] | None:
    """将会话 blueprint_wizard 与蓝本任务对齐；返回更新后的 wizard 或 None。"""
    from report_writing.chat.session_state import user_chose_skip_blueprint

    wizard = dict((session.freeform or {}).get("blueprint_wizard") or {})
    # 新开名称步：空壳 w* 只为切焦点，不代表名称已确认，禁止 derive 成 upload
    if bool(wizard.get("fresh_meta")) and str(wizard.get("step") or "meta").strip() in {"", "meta"}:
        session.freeform = {**(session.freeform or {}), "blueprint_wizard": wizard}
        return wizard
    # 无蓝本写作：清掉残留向导；依据样本（freeform_sample/create）仍要同步上传/提取
    if user_chose_skip_blueprint(session) and not is_freeform_sample_wizard_mode(wizard):
        ff = dict(session.freeform or {})
        if ff.get("blueprint_wizard"):
            ff.pop("blueprint_wizard", None)
            session.freeform = ff
        return None
    bid = (blueprint_id or wizard.get("blueprint_id") or session.blueprint_id or "").strip()
    if not bid:
        return None
    prev_mode = str(wizard.get("mode") or "").strip()
    pending_outline_extract = bool(wizard.get("trigger_outline_extract"))
    pending_cmd = str(wizard.get("pending_wizard_command") or "").strip()
    pending_study = list(
        wizard.get("trigger_chapter_study")
        or wizard.get("chapter_study_keys")
        or []
    )
    prev_step = str(wizard.get("step") or "").strip()
    # 选用已有蓝本：禁止因残留命令倒回上传/提取
    if prev_mode == WIZARD_MODE_SELECTED:
        pending_outline_extract = False
        if pending_cmd in CREATE_ONLY_WIZARD_COMMANDS:
            wizard.pop("pending_wizard_command", None)
            pending_cmd = ""
        wizard.pop("trigger_outline_extract", None)

    def normalized_step_from_wizard(s: str) -> int:
        from report_writing.chat.wizard_loop_guard import normalized_wizard_step

        return wizard_step_index(normalized_wizard_step(s))

    # 进度/落盘看工作副本（选用即 clone / 无蓝本样本）；库 id 仍作蓝本身份
    work_id = str(wizard.get("working_task_id") or "").strip()
    if not work_id and prev_mode == WIZARD_MODE_SELECTED:
        from report_writing.chat.session_documents import _writing_copy_id_for_blueprint

        work_id = _writing_copy_id_for_blueprint(session, bid)
    progress_id = work_id or bid

    step, extra = derive_blueprint_wizard_step(home, progress_id)
    from report_writing.chat.wizard_loop_guard import clamp_wizard_step_regression, normalized_wizard_step

    # 学习已发生但确认位/目录要点未落库：补齐，避免刷新回确认目录、文档窗仍待学习
    try:
        from report_writing.chat.helpers import _load_task
        from report_writing.controller.study_flow import _apply_study_drafts_to_outline
        from report_writing.services.blueprint_catalog import save_blueprint_task

        task_fix = _load_task(home, progress_id)
        if task_fix is not None:
            dirty = False
            pending_rows = list(getattr(task_fix, "pending_study", None) or [])
            if pending_rows:
                applied = _apply_study_drafts_to_outline(task_fix, pending_rows)
                if applied:
                    dirty = True
            from report_writing.controller.study_flow import _push_brief_points_to_outline

            before_pts = [
                tuple(getattr(n, "points", None) or []) for n in list((task_fix.outline.chapters if task_fix.outline else None) or [])
            ]
            _push_brief_points_to_outline(task_fix)
            after_pts = [
                tuple(getattr(n, "points", None) or []) for n in list((task_fix.outline.chapters if task_fix.outline else None) or [])
            ]
            if before_pts != after_pts:
                dirty = True
            if extra.get("outline_confirmed") and not bool(
                getattr(task_fix, "outline_confirmed", False)
            ):
                task_fix.outline_confirmed = True
                dirty = True
            if dirty:
                from report_writing.services.task_ids import is_blueprint_task_id
                from report_writing.services.store import TaskStore

                # 无蓝本·样本 / 选用副本（w*）只写任务目录；库蓝本才进 catalog
                if is_blueprint_task_id(str(getattr(task_fix, "id", "") or progress_id)):
                    save_blueprint_task(task_fix)
                else:
                    TaskStore(home / "tasks" / str(task_fix.id or progress_id)).save(task_fix)
                step, extra = derive_blueprint_wizard_step(home, progress_id)
    except Exception:
        pass
    step = clamp_wizard_step_regression(prev_step, step)
    # 会话/任务已确认目录，或已有成稿：禁止再倒回确认目录（否则保存重写会贴回确认卡）
    if normalized_wizard_step(step) == "outline_confirm":
        session_confirmed = bool(wizard.get("outline_confirmed"))
        task_confirmed = bool(extra.get("outline_confirmed"))
        if not task_confirmed:
            try:
                from report_writing.chat.helpers import _load_task

                prog_task = _load_task(home, progress_id)
                task_confirmed = bool(
                    prog_task and getattr(prog_task, "outline_confirmed", False)
                )
            except Exception:
                task_confirmed = False
        prev_ns = normalized_wizard_step(prev_step)
        if session_confirmed or task_confirmed or wizard_step_index(prev_ns) >= wizard_step_index(
            "study"
        ):
            step = "study"
            wizard["outline_confirmed"] = True
            extra["outline_confirmed"] = True
    # 有蓝本 id 时名称步必已完成；禁止 sync/migrate 残留把会话打回 meta
    if wizard_step_index(normalized_wizard_step(step)) <= wizard_step_index("meta"):
        step = "upload"
    chapters_ready = int(extra.get("chapter_count") or 0) > 0 or bool(extra.get("chapters"))
    re_extract_inflight = pending_cmd == "re_extract_outline" and pending_outline_extract
    if re_extract_inflight:
        # 用户刚点「重新提取」且 trigger 仍在：停在 outline；勿把任务旧目录写进向导预览
        step = "outline"
        chapters_ready = False
    elif pending_cmd == "re_extract_outline" and chapters_ready and not pending_outline_extract:
        # 提取流已完成、客户端已清 trigger：推进并丢掉挂起命令
        wizard.pop("pending_wizard_command", None)
        pending_cmd = ""
    elif pending_outline_extract and not chapters_ready:
        # 目录仍空时的首次提取：停在 outline 并保留 trigger
        step = "outline"
    elif pending_outline_extract and chapters_ready:
        # 提取已完成但 leftover trigger 未清：勿倒回 outline 再次提取
        wizard.pop("trigger_outline_extract", None)
    elif pending_cmd == "re_upload_sample":
        step = "upload"
    elif pending_study:
        from report_writing.chat.wizard_loop_guard import clear_stale_chapter_study_triggers

        clear_stale_chapter_study_triggers(home, wizard, progress_id or bid)
        if wizard.get("trigger_chapter_study") or wizard.get("chapter_study_keys"):
            step = "study"
    wizard["blueprint_id"] = bid
    if work_id:
        wizard["working_task_id"] = work_id
    wizard["step"] = step
    from report_writing.chat.wizard_loop_guard import clear_stale_begin_study_trigger

    clear_stale_begin_study_trigger(wizard, extra)
    for key, value in extra.items():
        if value is not None and value != "":
            wizard[key] = value
    if re_extract_inflight:
        wizard["step"] = "outline"
        wizard["trigger_outline_extract"] = True
        wizard["pending_wizard_command"] = "re_extract_outline"
        wizard["chapters"] = []
        wizard["chapter_count"] = 0
        wizard["outline_confirmed"] = False
    if step != "outline":
        wizard.pop("trigger_outline_extract", None)
        if pending_cmd == "re_extract_outline":
            wizard.pop("pending_wizard_command", None)
    if step != "study":
        wizard.pop("trigger_begin_study", None)
    if step == "done":
        wizard["step"] = "study"
        step = "study"
        wizard.pop("trigger_outline_extract", None)
    if prev_mode:
        wizard["mode"] = prev_mode
    if prev_mode == WIZARD_MODE_SELECTED and step in CREATE_ONLY_WIZARD_STEPS:
        chapters_ready = int(wizard.get("chapter_count") or 0) > 0 or bool(wizard.get("chapters"))
        wizard["step"] = "outline_confirm" if chapters_ready else "outline_confirm"
        step = wizard["step"]
    wizard = slim_wizard_chapters(wizard)
    session.freeform = {**(session.freeform or {}), "blueprint_wizard": wizard}
    return wizard


def _wizard_card_title(step: str, *, mode: str = "") -> str:
    m = (mode or "").strip()
    if m == WIZARD_MODE_SELECTED:
        labels = {
            "outline_confirm": "选用蓝本 · 确认目录",
            "outline_done": "选用蓝本 · 确认目录",
            "study": "选用蓝本 · 分析要点",
            "study_done": "选用蓝本 · 分析要点",
            "done": "选用蓝本 · 分析要点",
        }
        return labels.get(step, "选用蓝本")
    # create / freeform_sample：无蓝本·依据样本，收录前不入库
    labels = {
        "meta": "依据样本 · 名称",
        "upload": "依据样本 · 上传样板",
        "outline": "依据样本 · 提取目录",
        "outline_confirm": "依据样本 · 确认目录",
        "study": "依据样本 · 分析要点",
        "study_done": "依据样本 · 分析要点",
        "done": "依据样本 · 分析要点",
    }
    return labels.get(step, "依据样本编写")


def refresh_latest_wizard_card_in_session(home: Path, session: ChatSession) -> bool:
    """把会话里「最新一张」蓝本向导卡片的 payload 与当前 wizard 对齐。"""
    wizard = sync_blueprint_wizard_session(home, session)
    if not wizard:
        return False
    bid = str(wizard.get("blueprint_id") or "").strip()
    step = str(wizard.get("step") or "").strip()
    if not bid or not step:
        return False
    messages = list(session.messages or [])
    target_mi = -1
    target_ci = -1
    for mi in range(len(messages) - 1, -1, -1):
        msg = messages[mi]
        if (getattr(msg, "role", None) or "") != "assistant":
            continue
        cards = list(msg.cards or [])
        for ci in range(len(cards) - 1, -1, -1):
            card = cards[ci]
            if getattr(card, "type", "") != "blueprint_wizard":
                continue
            payload = dict(getattr(card, "payload", None) or {})
            if str(payload.get("blueprint_id") or "").strip() != bid:
                continue
            target_mi, target_ci = mi, ci
            break
        if target_mi >= 0:
            break
    if target_mi < 0:
        for mi in range(len(messages) - 1, -1, -1):
            msg = messages[mi]
            if (getattr(msg, "role", None) or "") != "assistant":
                continue
            cards = list(msg.cards or [])
            for ci in range(len(cards) - 1, -1, -1):
                if getattr(cards[ci], "type", "") == "blueprint_wizard":
                    target_mi, target_ci = mi, ci
                    break
            if target_mi >= 0:
                break
    if target_mi < 0:
        return False
    msg = messages[target_mi]
    cards = list(msg.cards or [])
    card = cards[target_ci]
    payload = dict(getattr(card, "payload", None) or {})
    from report_writing.chat.wizard_loop_guard import normalized_wizard_step

    # 每一步对话卡片是历史快照：refresh 不得把「上传样板」卡改写成「提取目录」
    card_step = normalized_wizard_step(str(payload.get("step") or "").strip())
    card_bid = str(payload.get("blueprint_id") or "").strip()
    # 无 id 的「填写名称」卡是历史快照，严禁被会话真值改写成 upload（会丢步骤记录）
    if (
        not card_bid
        and wizard_step_index(card_step) <= wizard_step_index("meta")
        and wizard_step_index(normalized_wizard_step(step)) > wizard_step_index("meta")
    ):
        return False
    keep_step = card_step if wizard_step_index(card_step) >= 0 else step
    # 已有蓝本 id 时名称步必已完成；禁止把 id 写进卡内却仍停在「填写名称」
    if bid and wizard_step_index(normalized_wizard_step(keep_step)) <= wizard_step_index("meta"):
        keep_step = "upload"
    # 最新卡是当前 live 卡：允许按任务真值向前推进（刷新后不得停在过期的确认目录）
    truth_step = normalized_wizard_step(step)
    pending_cmd = str(wizard.get("pending_wizard_command") or "").strip()
    # 用户主动回退（重新上传/重新提取/回确认目录）：允许最新卡 step 倒回
    intentional_rollback = pending_cmd in {
        *CREATE_ONLY_WIZARD_COMMANDS,
        "go_outline_confirm",
    }
    if intentional_rollback and card_bid == bid and truth_step:
        keep_step = truth_step
    elif (
        card_bid == bid
        and wizard_step_index(truth_step) > wizard_step_index(normalized_wizard_step(keep_step))
    ):
        keep_step = truth_step
    for key, value in wizard.items():
        if key in {"step"}:
            continue
        if value is not None and value != "":
            payload[key] = value
    if "outline_confirmed" in wizard:
        payload["outline_confirmed"] = bool(wizard.get("outline_confirmed"))
    if wizard_step_index(normalized_wizard_step(keep_step)) >= wizard_step_index("study"):
        payload["outline_confirmed"] = True
    payload["blueprint_id"] = bid
    payload["step"] = keep_step
    payload.update(slim_wizard_chapters(payload))
    keep_title = str(getattr(card, "title", "") or "").strip() or _wizard_card_title(keep_step)
    # 仅当卡片尚无标题、仍是占位、或 meta 标题已与步数不符时，才改标题
    if (
        not keep_title
        or keep_title in {"蓝本创建步骤", "在对话中创建蓝本"}
        or (
            keep_step != "meta"
            and ("填写名称" in keep_title or keep_title.endswith("· 名称"))
        )
    ):
        keep_title = _wizard_card_title(keep_step)
    cards[target_ci] = card.model_copy(update={"payload": payload, "title": keep_title})
    msg.cards = cards
    messages[target_mi] = msg
    session.messages = messages
    return True


def latest_blueprint_wizard_card_from_session(session: ChatSession):
    """会话消息里最后一张蓝本向导卡（与 refresh 查找顺序一致）。"""
    messages = list(session.messages or [])
    for mi in range(len(messages) - 1, -1, -1):
        msg = messages[mi]
        if (getattr(msg, "role", None) or "") != "assistant":
            continue
        cards = list(msg.cards or [])
        for ci in range(len(cards) - 1, -1, -1):
            card = cards[ci]
            if getattr(card, "type", "") == "blueprint_wizard":
                return card
    return None


def _payload_wizard_step_index(payload: ChatPayload) -> int:
    from report_writing.chat.wizard_loop_guard import normalized_wizard_step

    payload = ChatActionPayload.parse(payload)
    return wizard_step_index(normalized_wizard_step(str(payload.get("step") or "")))


def best_wizard_card_for_session(session: ChatSession, session_wizard: dict[str, Any] | None = None):
    """同 blueprint_id 下步序最高的向导卡；无 id 时取步序最高的最近一张。"""
    wiz = dict(session_wizard or (session.freeform or {}).get("blueprint_wizard") or {})
    truth_bid = str(wiz.get("blueprint_id") or session.blueprint_id or "").strip()
    best = None
    best_si = -1
    best_pos = (-1, -1)
    messages = list(session.messages or [])
    for mi, msg in enumerate(messages):
        if (getattr(msg, "role", None) or "") != "assistant":
            continue
        cards = list(msg.cards or [])
        for ci, card in enumerate(cards):
            if getattr(card, "type", "") != "blueprint_wizard":
                continue
            payload = dict(getattr(card, "payload", None) or {})
            pb = str(payload.get("blueprint_id") or "").strip()
            if truth_bid and pb and pb != truth_bid:
                continue
            si = _payload_wizard_step_index(payload)
            pos = (mi, ci)
            if si > best_si or (si == best_si and pos > best_pos):
                best_si = si
                best_pos = pos
                best = card
    return best


# 会话气泡可挂卡：名称/上传/提取/确认目录；分析与完成改由文档窗承接
from report_writing.chat.chat_card_policy import WIZARD_STEPS_CHAT as _CHAT_WIZARD_CARD_STEPS  # noqa: F401


def finalize_wizard_cards_for_response(
    session: ChatSession,
    cards: list,
    session_wizard: dict[str, Any],
) -> list:
    """SSE 响应：会话可操作向导卡回贴；分析/完成不每轮回贴。"""
    import copy

    from report_writing.chat.wizard_loop_guard import normalized_wizard_step, should_skip_redundant_wizard_card

    wiz = dict(session_wizard or {})
    truth_step = normalized_wizard_step(str(wiz.get("step") or ""))
    # 已确认目录 / 已越过学习：勿把确认目录卡再合成进本轮回复
    if truth_step in {"outline_confirm", "outline_done"} and bool(
        wiz.get("outline_confirmed")
    ):
        truth_step = "study"
        wiz["step"] = "study"
    ti = wizard_step_index(truth_step)
    truth_bid = str(wiz.get("blueprint_id") or session.blueprint_id or "").strip()

    non_wiz = [c for c in cards if getattr(c, "type", "") != "blueprint_wizard"]
    handler_wiz = [c for c in cards if getattr(c, "type", "") == "blueprint_wizard"]

    # 已进入分析/完成：文档窗承接；本轮若 handler 未主动带可操作卡则不再合成
    if truth_step and truth_step not in _CHAT_WIZARD_CARD_STEPS and ti >= wizard_step_index("study"):
        early_handler = []
        for card in handler_wiz:
            payload = dict(getattr(card, "payload", None) or {})
            step = normalized_wizard_step(str(payload.get("step") or ""))
            # 仅保留仍属会话可操作向导的卡；分析卡一律不回贴
            if step in _CHAT_WIZARD_CARD_STEPS:
                early_handler.append(card)
        return non_wiz + ([copy.deepcopy(early_handler[-1])] if early_handler else [])

    def _acceptable(card) -> bool:
        payload = dict(getattr(card, "payload", None) or {})
        if should_skip_redundant_wizard_card(wiz, payload):
            return False
        si = _payload_wizard_step_index(payload)
        if ti >= 0 and si >= 0 and si < ti:
            return False
        pb = str(payload.get("blueprint_id") or "").strip()
        if truth_bid and pb and pb != truth_bid:
            return False
        return True

    best_handler = None
    best_si = -1
    for card in handler_wiz:
        if not _acceptable(card):
            continue
        si = _payload_wizard_step_index(dict(getattr(card, "payload", None) or {}))
        if best_handler is None or si > best_si:
            best_handler = card
            best_si = si

    def _floor_step(step: str) -> str:
        s = normalized_wizard_step(str(step or "").strip())
        if truth_bid and wizard_step_index(s) <= wizard_step_index("meta"):
            return "upload"
        return s or ("upload" if truth_bid else "meta")

    if best_handler is not None:
        card = copy.deepcopy(best_handler)
        payload = dict(getattr(card, "payload", None) or {})
        floored = _floor_step(str(payload.get("step") or wiz.get("step") or ""))
        # 会话可操作步才回贴；分析/完成改由文档窗
        if floored not in _CHAT_WIZARD_CARD_STEPS:
            return non_wiz
        if str(payload.get("step") or "").strip() != floored or (
            truth_bid and not str(payload.get("blueprint_id") or "").strip()
        ):
            from report_writing.chat.cards import _blueprint_wizard_card

            return non_wiz + [
                _blueprint_wizard_card(
                    blueprint_id=truth_bid or str(payload.get("blueprint_id") or ""),
                    step=floored,
                    title=_wizard_card_title(floored),
                    extra={**wiz, **payload, "step": floored},
                )
            ]
        return non_wiz + [card]

    # 分析/完成：无本轮卡时不合成
    if truth_step and truth_step not in _CHAT_WIZARD_CARD_STEPS:
        return non_wiz

    # 提取中刻意空卡：避免再插一张「提取目录」把前端流式请求 remount 掉
    if (
        truth_step == "outline"
        and bool(wiz.get("outline_extract_active") or wiz.get("trigger_outline_extract"))
        and not handler_wiz
    ):
        return non_wiz

    best_session = best_wizard_card_for_session(session, wiz)
    if best_session is not None:
        payload = dict(getattr(best_session, "payload", None) or {})
        si = _payload_wizard_step_index(payload)
        if ti < 0 or si >= ti or not truth_bid:
            floored = _floor_step(str(payload.get("step") or wiz.get("step") or ""))
            if floored not in _CHAT_WIZARD_CARD_STEPS:
                return non_wiz
            if floored != str(payload.get("step") or "").strip() or (
                truth_bid and wizard_step_index(normalized_wizard_step(str(payload.get("step") or ""))) <= wizard_step_index("meta")
            ):
                from report_writing.chat.cards import _blueprint_wizard_card

                return non_wiz + [
                    _blueprint_wizard_card(
                        blueprint_id=truth_bid or str(payload.get("blueprint_id") or ""),
                        step=floored,
                        title=_wizard_card_title(floored),
                        extra={**wiz, **payload, "step": floored},
                    )
                ]
            return non_wiz + [copy.deepcopy(best_session)]

    if truth_bid and str(wiz.get("step") or "").strip():
        from report_writing.chat.cards import _blueprint_wizard_card

        step = _floor_step(str(wiz.get("step") or "upload").strip())
        if step not in _CHAT_WIZARD_CARD_STEPS:
            return non_wiz
        synth = _blueprint_wizard_card(
            blueprint_id=truth_bid,
            step=step,
            title=_wizard_card_title(step),
            extra=wiz,
        )
        return non_wiz + [synth]

    if handler_wiz:
        last = handler_wiz[-1]
        last_step = normalized_wizard_step(
            str((getattr(last, "payload", None) or {}).get("step") or "")
        )
        if last_step in _CHAT_WIZARD_CARD_STEPS or not last_step:
            return non_wiz + [copy.deepcopy(last)]
        return non_wiz
    latest = latest_blueprint_wizard_card_from_session(session)
    if latest is not None:
        latest_step = normalized_wizard_step(
            str((getattr(latest, "payload", None) or {}).get("step") or "")
        )
        if latest_step in _CHAT_WIZARD_CARD_STEPS:
            return non_wiz + [copy.deepcopy(latest)]
    return non_wiz


def wizard_step_index(step: str) -> int:
    try:
        return _WIZARD_STEP_ORDER.index(step)
    except ValueError:
        return -1
