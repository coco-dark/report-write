"""Chat session HTTP routes."""

from __future__ import annotations

import json
import queue
import threading

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from report_writing.api.deps import _log, chat_invoke, sse_response, task_root
from report_writing.domain.models import ChatRequest, ChatResponse, ChatSession, ChatSessionSummary

router = APIRouter(tags=["chat"])


def _chat_stream_model(system: str, user: str):
    """逐块产出模型回复，供对话 SSE 打字机效果。"""
    from report_writing.infra.llm import live_model

    model = live_model()
    if model is None:
        text = (
            "当前未配置大模型。已收到消息；接入模型后将按机器人对话提示词回复。"
            f"\n（系统提示摘要：{(system or '')[:120]}）"
        )
        for ch in text:
            yield ch
        return
    from langchain_core.messages import HumanMessage, SystemMessage

    messages = [SystemMessage(content=system), HumanMessage(content=user)]
    stream = getattr(model, "stream", None)
    if not callable(stream):
        yield chat_invoke(system, user)
        return
    for chunk in stream(messages):
        content = getattr(chunk, "content", None)
        if content is None:
            content = chunk
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    parts.append(str(item.get("text") or ""))
                else:
                    parts.append(str(getattr(item, "text", None) or item or ""))
            content = "".join(parts)
        text = str(content or "")
        if text:
            yield text


@router.post("/api/writing/chat", response_model=ChatResponse)
def post_chat(body: ChatRequest) -> ChatResponse:
    from report_writing.services.robots import chat as robot_chat

    try:
        return robot_chat(task_root(), body, invoke=chat_invoke)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.post("/api/writing/chat/stream")
def post_chat_stream(body: ChatRequest) -> StreamingResponse:
    """对话 SSE：token 打字机 + process/step 执行过程 + done 终态。"""
    from report_writing.chat_orchestrator import handle_chat

    def worker(emit) -> None:
        try:
            handle_chat(
                task_root(),
                body,
                invoke=chat_invoke,
                emit=emit,
                stream_model=_chat_stream_model,
            )
        except ValueError as error:
            emit({"type": "error", "detail": str(error)})
        except KeyError as error:
            emit({"type": "error", "detail": str(error)})
        except Exception as error:
            _log.exception("chat stream failed")
            emit({"type": "error", "detail": str(error)})

    return sse_response(worker)



@router.post("/api/writing/chat/usage-guide")
def post_usage_guide() -> dict:
    """欢迎页「使用说明」：LLM 润色产品说明（无需会话）。"""
    from report_writing.chat.product_guide import build_usage_guide_reply

    reply = build_usage_guide_reply(chat_invoke)
    return {"reply": reply}


@router.get("/api/writing/chat/sessions", response_model=list[ChatSessionSummary])
def list_chat_sessions(limit: int = 50, robot_id: str = "") -> list[ChatSessionSummary]:
    from report_writing.services.chat_session import list_sessions

    return list_sessions(task_root(), limit=limit, robot_id=robot_id)


@router.post("/api/writing/chat/sessions", response_model=ChatSession)
def post_create_chat_session(body: dict | None = None) -> ChatSession:
    """立即创建空会话并写入开场引导（点「新对话」时调用）。"""
    from report_writing.api.chat_session_helpers import prepare_chat_session_response
    from report_writing.chat.opening import OPENING_GREETING
    from report_writing.domain.models import ChatMessage
    from report_writing.services.chat_session import create_session, save_session

    payload = body if isinstance(body, dict) else {}
    robot_id = str(payload.get("robot_id") or "").strip()
    home = task_root()
    session = create_session(home, robot_id=robot_id)
    session.messages = [ChatMessage(role="assistant", text=OPENING_GREETING)]
    save_session(home, session, durable=True)
    return prepare_chat_session_response(home, session, persist=False)


@router.post("/api/writing/chat/sessions/{session_id}/ui-state", response_model=ChatSession)
def post_chat_ui_state(session_id: str, body: dict) -> ChatSession:
    """持久化对话 UI：已用卡片、过程步骤、蓝本向导进度等。"""
    from report_writing.domain.models import ChatUiStateRequest
    from report_writing.services.chat_session import apply_chat_ui_state, get_session, save_session

    home = task_root()
    session = get_session(home, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    try:
        req = ChatUiStateRequest.model_validate(body or {})
    except Exception as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    # 写入前再读一次热存：避免与 stream 并发时用旧消息列表覆盖「已建好」轮次
    try:
        from report_writing.services.session_redis_store import get_session_hot

        hot = get_session_hot(session_id)
        if isinstance(hot, dict):
            hot_msgs = list(hot.get("messages") or [])
            if len(hot_msgs) > len(list(session.messages or [])):
                session = ChatSession.from_dict(hot)
    except Exception:
        pass
    session = apply_chat_ui_state(session, req)
    from report_writing.api.chat_session_helpers import prepare_chat_session_response

    # ui-state 高频：只落补丁，勿每次从 200+ 章任务重推向导卡
    save_session(home, session, durable=False)
    return prepare_chat_session_response(home, session, persist=False)


@router.post("/api/writing/chat/sessions/{session_id}/focus-document", response_model=ChatSession)
def post_focus_document(session_id: str, body: dict) -> ChatSession:
    """文档小窗 / 口语切换：对齐服务端焦点，并可选写入「已切换到…」助手句。"""
    from report_writing.chat.session_documents import apply_focus_document_by_key
    from report_writing.services.chat_session import get_session, save_session

    home = task_root()
    session = get_session(home, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    payload = body if isinstance(body, dict) else {}
    key = str(payload.get("focus_document_key") or payload.get("document_key") or "").strip()
    announce = payload.get("announce", True)
    if isinstance(announce, str):
        announce = announce.lower() not in {"0", "false", "no"}
    hit = apply_focus_document_by_key(
        session,
        key,
        home=home,
        announce=bool(announce),
    )
    if hit is None:
        raise HTTPException(status_code=400, detail="无效的 focus_document_key")
    from report_writing.api.chat_session_helpers import prepare_chat_session_response

    save_session(home, session, durable=True)
    return prepare_chat_session_response(home, session, persist=False)


@router.delete("/api/writing/chat/sessions/{session_id}")
def delete_chat_session(session_id: str) -> dict:
    from report_writing.services.chat_session import delete_session

    if not delete_session(task_root(), session_id):
        raise HTTPException(status_code=404, detail="会话不存在")
    return {"ok": True}


@router.get("/api/writing/chat/sessions/{session_id}", response_model=ChatSession)
def get_chat_session(session_id: str) -> ChatSession:
    from report_writing.api.chat_session_helpers import prepare_chat_session_response
    from report_writing.services.chat_session import get_session

    home = task_root()
    session = get_session(home, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    return prepare_chat_session_response(home, session, persist=True)


@router.post("/api/writing/chat/element-configure/confirm")
def post_confirm_element_configure(body: dict) -> dict:
    """用户在前端确认要素配置后写入任务并关联章节。"""
    from report_writing.chat.element_configure_pending import dismiss_configure_pending, list_configure_pending
    from report_writing.chat.element_library_pending import queue_element_pending
    from report_writing.chat.writing import _apply_add_chapter_element
    from report_writing.services.chat_session import get_session, save_session
    from report_writing.services.store import TaskStore

    session_id = str((body or {}).get("session_id") or "").strip()
    pending_id = str((body or {}).get("pending_id") or "").strip()
    element_payload = (body or {}).get("element")
    if not session_id or not pending_id or not isinstance(element_payload, dict):
        raise HTTPException(status_code=400, detail="需要 session_id、pending_id、element")
    home = task_root()
    session = get_session(home, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    pending = next((p for p in list_configure_pending(session) if p.get("pending_id") == pending_id), None)
    if pending is None:
        raise HTTPException(status_code=404, detail="待配置要素不存在或已处理")
    task_id = str(pending.get("task_id") or "").strip()
    chapter_key = str(pending.get("chapter_key") or "").strip()
    if not task_id or not chapter_key:
        raise HTTPException(status_code=400, detail="待办缺少 task_id 或 chapter_key")
    store = TaskStore(home / "tasks" / task_id)
    task = store.load()
    ui_keys = {"knowledge_folder_picks", "knowledge_folder_unmatched", "ref_element_picks"}
    clean_payload = {k: v for k, v in element_payload.items() if k not in ui_keys}
    merged = {**(pending.get("draft") or {}), **clean_payload}
    for k in ui_keys:
        merged.pop(k, None)
    merged["task_id"] = task_id
    merged["chapter_key"] = chapter_key
    if pending.get("point_index") is not None:
        merged["point_index"] = pending.get("point_index")
    slot_l = str(pending.get("slot_label") or "").strip()
    if slot_l:
        merged["slot"] = slot_l
    from report_writing.domain.point_items_sync import human_label, is_element_id_token

    pending_draft = pending.get("draft") if isinstance(pending.get("draft"), dict) else {}
    merged_name = human_label(
        merged.get("name"),
        pending_draft.get("name"),
        slot_l,
        pending_draft.get("category"),
        merged.get("category"),
    )
    if merged_name:
        merged["name"] = merged_name
    elif is_element_id_token(merged.get("name")):
        merged["name"] = ""
    try:
        element = _apply_add_chapter_element(task, chapter_key, merged)
        if str(element.kind or "").strip() == "image":
            from report_writing.services.element_images import (
                absolute_image_path,
                ensure_image_element_ready,
            )

            source = str(getattr(element, "image_source", "") or "upload").strip() or "upload"
            if source == "llm" and not absolute_image_path(home, task_id, element):
                ensure_image_element_ready(
                    home, task_id, element, generate_if_llm=True
                )
        store.save(task)
    except Exception as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    from report_writing.chat.writing_supplement_check import (
        mark_gaps_filled_for_element,
        mark_resolved_gaps_in_registry,
    )

    mark_gaps_filled_for_element(session, task, str(element.id))
    mark_resolved_gaps_in_registry(session, task, home)
    store.save(task)
    dismiss_configure_pending(session, [pending_id])
    draft_tags = merged.get("tags") if isinstance(merged.get("tags"), list) else []
    queue_element_pending(
        session,
        element,
        task_id=task_id,
        chapter_key=chapter_key,
        chapter_title=str(pending.get("chapter_title") or "").strip(),
        category=str(merged.get("category") or "").strip(),
        tags=[str(t).strip() for t in draft_tags if str(t).strip()],
        purpose=str(merged.get("purpose") or "").strip(),
    )
    # 待收录改由文档窗口角标展示；仍入队，不往对话消息塞卡片
    from report_writing.chat.element_library_pending import list_pending

    pending_n = len(list_pending(session))
    reply = f"「{element.name or '要素'}」已关联到本章。"
    if pending_n:
        reply += " 若要复用，可在右侧文档窗口点「待收录要素」收入个人要素库。"
    save_session(home, session)
    node = next(
        (c for c in (task.outline.chapters if task.outline else []) if c.key == chapter_key),
        None,
    )
    from report_writing.services.doc_outline_cache import invalidate_doc_outline_cache

    invalidate_doc_outline_cache(task_id)
    source_bp = str(getattr(task, "source_blueprint_id", "") or "").strip()
    if source_bp:
        invalidate_doc_outline_cache(source_bp)
    return {
        "ok": True,
        "task_id": task_id,
        "chapter_key": chapter_key,
        "element": element.model_dump(),
        "element_ids": list(getattr(node, "element_ids", None) or []) if node else [],
        "point_items": [
            dict(x)
            for x in list(getattr(node, "point_items", None) or [])
            if isinstance(x, dict)
        ]
        if node
        else [],
        "points": list(getattr(node, "points", None) or []) if node else [],
        "reply": reply,
        "cards": [],
        "element_library_pending_count": pending_n,
    }


@router.post("/api/writing/chat/element-configure/dismiss")
def post_dismiss_element_configure(body: dict) -> dict:
    from report_writing.chat.element_configure_pending import dismiss_configure_pending
    from report_writing.services.chat_session import get_session, save_session

    session_id = str((body or {}).get("session_id") or "").strip()
    if not session_id:
        raise HTTPException(status_code=400, detail="需要 session_id")
    session = get_session(task_root(), session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    ids = body.get("pending_ids")
    removed = dismiss_configure_pending(session, ids if isinstance(ids, list) else None)
    save_session(task_root(), session)
    return {"removed": removed}


@router.post("/api/writing/chat/element-library/queue")
def post_queue_element_library_pending(body: dict) -> dict:
    """文档窗新增/关联要素后入队「待收录」，不发会话消息。"""
    from report_writing.chat.element_library_pending import list_pending, queue_element_pending
    from report_writing.domain.models import ElementNode
    from report_writing.services.chat_session import get_session, save_session
    from report_writing.services.store import TaskStore

    session_id = str((body or {}).get("session_id") or "").strip()
    task_id = str((body or {}).get("task_id") or "").strip()
    chapter_key = str((body or {}).get("chapter_key") or "").strip()
    chapter_title = str((body or {}).get("chapter_title") or "").strip()
    if not session_id or not task_id:
        raise HTTPException(status_code=400, detail="需要 session_id、task_id")
    home = task_root()
    session = get_session(home, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    try:
        task = TaskStore(home / "tasks" / task_id).load()
    except Exception as error:
        raise HTTPException(status_code=404, detail=f"任务不存在：{error}") from error

    want_ids = {
        str(x).strip()
        for x in list((body or {}).get("element_ids") or [])
        if str(x).strip()
    }
    raw_elements = (body or {}).get("elements")
    by_payload: dict[str, ElementNode] = {}
    if isinstance(raw_elements, list):
        for item in raw_elements:
            if not isinstance(item, dict):
                continue
            try:
                node = ElementNode.model_validate(item)
            except Exception:
                continue
            eid = str(getattr(node, "id", "") or "").strip()
            if eid:
                by_payload[eid] = node
                want_ids.add(eid)

    if not chapter_title and chapter_key and task.outline is not None:
        for node in list(task.outline.chapters or []):
            if str(getattr(node, "key", "") or "") == chapter_key:
                chapter_title = (
                    f"{getattr(node, 'sequence', '') or ''} {getattr(node, 'title', '') or ''}".strip()
                )
                break

    queued = 0
    for element in list(task.element_nodes or []):
        eid = str(getattr(element, "id", "") or "").strip()
        if not eid:
            continue
        if want_ids and eid not in want_ids:
            continue
        if (getattr(element, "library_item_id", "") or "").strip():
            continue
        src = by_payload.get(eid) or element
        entry = queue_element_pending(
            session,
            src,
            task_id=task_id,
            chapter_key=chapter_key,
            chapter_title=chapter_title,
            category=str(getattr(src, "category", "") or "").strip(),
            tags=list(getattr(src, "tags", None) or []),
            purpose=str(getattr(src, "purpose", "") or "").strip(),
        )
        if entry:
            queued += 1

    save_session(home, session)
    pending = list_pending(session)
    return {
        "ok": True,
        "queued": queued,
        "count": len(pending),
        "element_library_pending": pending,
    }


@router.post("/api/writing/chat/writing/pause")
def post_pause_writing(body: dict) -> dict:
    """暂停批量写作：打标取消当前会话写作循环，并将已确认章节写入数据库。"""
    from report_writing.chat.actions import pause_writing_session

    session_id = str((body or {}).get("session_id") or "").strip()
    try:
        session, cards, saved = pause_writing_session(task_root(), session_id)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return {
        "ok": True,
        "session_id": session.id,
        "phase": session.phase,
        "task_id": session.task_id or "",
        "saved_chapters": saved,
        "cards": [c.to_dict() for c in cards],
    }



