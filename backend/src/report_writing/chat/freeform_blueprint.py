"""无蓝本写作：将当前任务大纲与章节配置沉淀为蓝本。"""

from __future__ import annotations

import copy
import secrets
from datetime import datetime
from pathlib import Path

from report_writing.controller import confirm_outline, ensure_task
from report_writing.domain.models import ChatCard, ChatPhase, ChatSession, WritingTask
from report_writing.services.store import TaskStore
from report_writing.services.task_ids import is_writing_task_id


def should_offer_save_blueprint(session: ChatSession) -> bool:
    """文档窗口是否展示「收录到蓝本库」：无蓝本写作（含依据样本支线）、尚未入库。

    依据样本 = 无蓝本创作支线，不是「直接创建到蓝本库」；收录前不入库。
    选用库蓝本 / 已有源蓝本的副本只走「同步到蓝本库」，不展示收录。
    """
    ff = dict(session.freeform or {})
    if ff.get("blueprint_offer_dismissed"):
        return False
    from report_writing.chat.session_documents import collected_blueprint_id_for_focus

    if collected_blueprint_id_for_focus(session):
        return False
    from report_writing.chat.wizard_state import (
        is_freeform_sample_wizard_mode,
        is_selected_blueprint_session,
        is_selected_wizard_mode,
    )

    sample = is_freeform_sample_wizard_mode(ff.get("blueprint_wizard"))
    if not sample and is_selected_blueprint_session(session):
        return False
    if is_selected_wizard_mode(ff.get("blueprint_wizard")):
        return False
    writing_path = str(ff.get("writing_path") or "").strip()
    if writing_path in {"selected", "blueprint"} and not sample:
        return False
    tid = (session.task_id or "").strip()
    if not tid or not is_writing_task_id(tid):
        return False
    if not ff.get("meta_confirmed") and not sample:
        # 自动大纲已经写出目录时，定名标记可能被焦点重绑清掉；有标题仍应能收录
        titled = bool(str(ff.get("title") or "").strip() or ff.get("writing_doc_named"))
        if not titled:
            return False
    # 有大纲即可在文档窗收录（确认前也可，与大纲卡「收录到蓝本库」一致）
    return True


def freeform_outline_is_confirmed(session: ChatSession, home: Path | None = None) -> bool:
    ff = dict(session.freeform or {})
    if ff.get("outline_confirmed"):
        return True
    if ff.get("outline_pending_edit"):
        return False
    tid = (session.task_id or "").strip()
    if home is None or not tid:
        return False
    try:
        from report_writing.chat.helpers import _load_task

        task = _load_task(home, tid)
        return bool(task and getattr(task, "outline_confirmed", False))
    except Exception:
        return False


def save_blueprint_offer_card(session: ChatSession, task: WritingTask) -> ChatCard:
    tid = (session.task_id or task.id or "").strip()
    n = len(task.outline.chapters) if task.outline else 0
    title = (task.title or "").strip() or str(
        (session.freeform or {}).get("title") or "未命名"
    )
    ff = dict(session.freeform or {})
    pending = bool(ff.get("outline_pending_edit"))
    if pending:
        hint = (
            f"目录已生成共 {n} 章，可先保存为蓝本备下次用；也可改满意后点「确认大纲并继续」再写。"
        )
    else:
        hint = (
            f"当前无蓝本任务共 {n} 章，目录与已配的写作要点可保存为新蓝本，"
            "下次写同类文档可直接选用。"
        )
    return ChatCard(
        type="save_blueprint_offer",
        title="保存为蓝本？",
        payload={
            "task_id": tid,
            "suggested_title": title,
            "chapter_count": n,
            "hint": hint,
        },
    )


def composer_save_blueprint_state(home: Path | None, session: ChatSession) -> dict:
    """文档窗口：是否展示「收录到蓝本库」。"""
    if not should_offer_save_blueprint(session):
        return {"show": False}
    tid = (session.task_id or "").strip()
    if not tid or not is_writing_task_id(tid):
        return {"show": False}
    title = ""
    if home is not None:
        from report_writing.chat.helpers import _load_task

        task = _load_task(home, tid)
        if task is None or not task.outline or not (task.outline.chapters or []):
            return {"show": False}
        if str(getattr(task, "source_blueprint_id", "") or "").strip():
            return {"show": False}
        title = str(task.title or "").strip()
    ff = dict(session.freeform or {})
    if not title:
        title = str(ff.get("title") or "").strip()
    return {
        "show": True,
        "task_id": tid,
        "suggested_title": title or tid,
    }


def save_blueprint_bar_hint(home: Path, session: ChatSession) -> str:
    """助手回复里提示收录到蓝本库。"""
    from report_writing.chat.helpers import _load_task

    task_id = (session.task_id or "").strip()
    task = _load_task(home, task_id)
    if task is None or not task.outline or not task.outline.chapters:
        return ""
    if freeform_outline_is_confirmed(session, home):
        if not should_offer_save_blueprint(session):
            return ""
        return "需要复用本次目录时，可在右侧文档窗口点「收录到蓝本库」。"
    return "确认大纲后，可在右侧文档窗口点「收录到蓝本库」写入蓝本库。"


def append_save_blueprint_offer(
    home: Path,
    session: ChatSession,
    cards: list[ChatCard],
) -> list[ChatCard]:
    """保留调用点；保存入口仅在底部 composer 导出栏。"""
    return list(cards or [])


def session_post_writing_complete(home: Path, session: ChatSession, task_id: str) -> bool:
    """当前会话写作已收尾（可导出/无待写章），保存蓝本时不应再弹「选择书写方式」。"""
    phase = str(session.phase or "")
    if phase == ChatPhase.CONFIRM_EXPORT.value:
        return True
    tid = (task_id or session.task_id or "").strip()
    if not tid:
        return False
    from report_writing.chat.helpers import _load_task
    from report_writing.chat.writing import _pending_write_keys

    task = _load_task(home, tid)
    if task is None:
        return False
    if phase == ChatPhase.WRITING.value and not _pending_write_keys(task):
        return True
    status = str(task.status.value if hasattr(task.status, "value") else task.status or "")
    return status in {"completed", "awaiting_confirm"} and not _pending_write_keys(task)


def promote_writing_task_to_blueprint(
    home: Path,
    writing_task_id: str,
    *,
    title: str = "",
    category: str = "",
    tags: list[str] | None = None,
) -> tuple[str, WritingTask]:
    from report_writing.chat.helpers import _load_task
    from report_writing.services.blueprint_catalog import (
        find_blueprints_by_title,
        normalize_blueprint_title,
        save_blueprint_task,
    )

    tid = (writing_task_id or "").strip()
    if not tid or not is_writing_task_id(tid):
        raise ValueError("仅支持从无蓝本写作任务保存蓝本")
    task = _load_task(home, tid)
    if task is None:
        raise ValueError("写作任务不存在")
    if not task.outline or not task.outline.chapters:
        raise ValueError("还没有可保存的章节目录")

    tip = normalize_blueprint_title(title or task.title or "")
    if not tip:
        raise ValueError("请填写蓝本名称")
    cat = str(category or task.category or "").strip()
    if not cat:
        raise ValueError("请填写蓝本分类")
    incoming_tags = [
        str(t).strip()
        for t in (tags if tags is not None else list(getattr(task, "tags", None) or []))
        if str(t).strip()
    ]
    # 去掉系统内部标记，只保留用户填写标签
    incoming_tags = [t for t in incoming_tags if t.casefold() != "from_freeform"]
    if not incoming_tags:
        raise ValueError("请至少填写一个蓝本标签")
    hits = find_blueprints_by_title(tip)
    if hits:
        raise ValueError(
            f"蓝本库已有同名「{hits[0].title}」，请换一个名称后再收录"
        )

    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    blueprint_id = f"bp{stamp}{secrets.token_hex(2)}"
    bp_dir = home / "tasks" / blueprint_id
    bp_dir.mkdir(parents=True, exist_ok=True)

    data = copy.deepcopy(task.to_dict())
    data["id"] = blueprint_id
    data["title"] = tip
    data["category"] = cat
    data["description"] = (
        (task.description or "").strip() or f"由无蓝本对话任务 {tid} 保存的大纲与要点"
    )
    final_tags = list(incoming_tags)
    if "from_freeform" not in final_tags:
        final_tags.append("from_freeform")
    data["tags"] = final_tags
    data["steps"] = []
    data["status"] = "studying"
    data["chapters"] = []
    data["brief"] = None
    data["brief_confirmed"] = False

    ensure_task(bp_dir, biz_type=str(data.get("biz_type") or "002"))
    cloned = WritingTask.from_dict(data)
    TaskStore(bp_dir).save(cloned)
    confirm_outline(bp_dir)
    cloned = TaskStore(bp_dir).load()
    save_blueprint_task(cloned)
    # 文档副本绑定源库蓝本，便于之后「同步到蓝本库」
    task.source_blueprint_id = blueprint_id
    TaskStore(home / "tasks" / tid).save(task)
    return blueprint_id, cloned
