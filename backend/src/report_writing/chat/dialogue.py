"""最近对话格式化（供总控 Agent / 子流程上下文）。"""

from __future__ import annotations

from report_writing.domain.models import ChatSession


def texts_are_same_utterance(left: str, right: str) -> bool:
    """两条正文是否同一句（忽略首尾空白）。短句如「继续」不算，避免误删。"""
    a = (left or "").strip()
    b = (right or "").strip()
    return bool(a) and a == b and len(a) >= 12


def strip_assistant_echo_user_messages(messages: list) -> list:
    """去掉与相邻助手回复全文相同的用户气泡。

    新开文档后刷新，曾把助手正文按序号写进用户消息。那些句子不是用户说的，
    不能再送进意图判断。助手原文保留。
    """
    items = list(messages or [])
    kept: list = []
    index = 0
    while index < len(items):
        msg = items[index]
        role = str(getattr(msg, "role", "") or "").strip()
        text = str(getattr(msg, "text", "") or "")
        if role == "user" and index + 1 < len(items):
            nxt = items[index + 1]
            nrole = str(getattr(nxt, "role", "") or "").strip()
            ntext = str(getattr(nxt, "text", "") or "")
            if nrole == "assistant" and texts_are_same_utterance(text, ntext):
                index += 1
                continue
        kept.append(msg)
        index += 1
    return kept


def message_repeats_assistant_reply(session: ChatSession, message: str) -> bool:
    """本句是否整段等于会话里某条助手回复。那样的句子不能再当成用户起名或下指令。"""
    text = (message or "").strip()
    if len(text) < 12:
        return False
    for msg in list(getattr(session, "messages", None) or []):
        if str(getattr(msg, "role", "") or "").strip() != "assistant":
            continue
        if (str(getattr(msg, "text", "") or "").strip()) == text:
            return True
    return False


def drop_assistant_echo_user_messages(session: ChatSession) -> bool:
    """从会话里删掉被误记成用户话的助手原文。有删除返回 True。"""
    before = list(getattr(session, "messages", None) or [])
    after = strip_assistant_echo_user_messages(before)
    if len(after) == len(before):
        return False
    session.messages = after
    return True


def _resolve_message_document_key(session: ChatSession, raw_key: str) -> str:
    """优先用消息自带 document_key；历史未盖戳时单文档会话回填唯一登记项。

    多文档会话未盖戳消息不得回填为当前焦点，否则 focus_only 会把其它文档历史串进模型上下文。
    """
    key = (raw_key or "").strip()
    if key:
        return key
    from report_writing.chat.session_documents import session_documents_map

    docs = session_documents_map(session)
    if len(docs) == 1:
        return next(iter(docs.keys()))
    return ""


def format_full_dialogue(
    session: ChatSession,
    *,
    max_turns: int = 24,
    max_chars_per_message: int = 2400,
    focus_only: bool = False,
    focus_key: str | None = None,
) -> list[dict[str, str]]:
    """格式化近期对话；每句带 document_id，焦点变化插入切换注记。

    focus_only=True：只保留当前焦点文档的消息，避免跨文档串扰进模型上下文。
    其它文档仅以一行系统摘要提示存在（不带正文）。
    """
    from report_writing.chat.session_documents import (
        document_title_for_key,
        focus_document_key,
        session_documents_map,
    )

    want_focus = (focus_key or "").strip()
    if focus_only and not want_focus:
        want_focus = focus_document_key(session)

    all_msgs = strip_assistant_echo_user_messages(list(session.messages or []))
    if focus_only and want_focus:
        selected: list = []
        for msg in reversed(all_msgs):
            key = _resolve_message_document_key(
                session, str(getattr(msg, "document_key", "") or "")
            )
            if key == want_focus:
                selected.append(msg)
            if len(selected) >= max_turns:
                break
        source = list(reversed(selected))
    else:
        source = all_msgs[-max_turns:]

    rows: list[dict[str, str]] = []
    if focus_only and want_focus:
        docs = session_documents_map(session)
        others = [
            document_title_for_key(session, k) or k
            for k in docs.keys()
            if k != want_focus
        ]
        if others:
            focus_title = document_title_for_key(session, want_focus) or want_focus
            rows.append(
                {
                    "role": "system",
                    "text": (
                        f"[当前焦点文档={focus_title}|{want_focus}；"
                        f"同会话另有 {len(others)} 份文档未纳入正文上下文："
                        + "、".join(others[:6])
                        + ("…" if len(others) > 6 else "")
                        + "。默认只操作焦点文档。]"
                    ),
                    "document_key": want_focus,
                    "document_id": want_focus,
                }
            )

    prev_key = ""
    for msg in source:
        role = str(getattr(msg, "role", "") or "").strip()
        text = str(getattr(msg, "text", "") or "").strip()
        if not text:
            continue
        if len(text) > max_chars_per_message:
            text = text[: max_chars_per_message - 1] + "…"
        key = _resolve_message_document_key(
            session, str(getattr(msg, "document_key", "") or "")
        )
        title = document_title_for_key(session, key) if key else ""
        if key and key != prev_key:
            switch_label = f"{key}|{title}" if title and title != key else (title or key)
            rows.append(
                {
                    "role": "system",
                    "text": f"[文档切换 → {switch_label}]",
                    "document_key": key,
                    "document_id": key,
                }
            )
            prev_key = key
        label = "user" if role == "user" else "assistant"
        if key and title and title != key:
            text = f"[doc_id={key}|{title}] {text}"
        elif key:
            text = f"[doc_id={key}] {text}"
        row: dict[str, str] = {"role": label, "text": text}
        if key:
            row["document_key"] = key
            row["document_id"] = key
        rows.append(row)
    return rows
