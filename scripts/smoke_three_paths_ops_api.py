#!/usr/bin/env python3
"""三条路径对话操作加深：确认大纲、继续学习、指定章重学、写已分析。"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8090"


def _req(method: str, path: str, body: dict | None = None) -> tuple[int, dict | list | str]:
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=180) as resp:
            raw = resp.read().decode("utf-8")
            try:
                return resp.status, json.loads(raw)
            except json.JSONDecodeError:
                return resp.status, raw
    except urllib.error.HTTPError as err:
        raw = err.read().decode("utf-8")
        try:
            return err.code, json.loads(raw)
        except json.JSONDecodeError:
            return err.code, raw


def chat(session_id: str, message: str = "", **extra) -> dict:
    code, data = _req("POST", "/api/writing/chat", {"session_id": session_id, "message": message, **extra})
    if code >= 400:
        raise RuntimeError(f"chat {code}: {data}")
    assert isinstance(data, dict)
    return data


def new_session() -> str:
    code, data = _req("POST", "/api/writing/chat/sessions", {})
    if code >= 400:
        raise RuntimeError(f"session {code}: {data}")
    assert isinstance(data, dict)
    return str(data["id"])


def card_types(data: dict) -> list[str]:
    return [str(c.get("type") or "") for c in (data.get("cards") or [])]


def pick_blueprint() -> dict | None:
    code, bps = _req("GET", "/api/writing/blueprints?limit=30")
    if not isinstance(bps, list) or not bps:
        return None
    return next(
        (b for b in bps if int(b.get("chapter_count") or 0) > 0 and b.get("outline_confirmed")),
        None,
    ) or bps[0]


def main() -> int:
    print(f"== ops smoke @ {BASE} ==")
    fails: list[str] = []

    # ---- A: 裸「不用蓝本」→ 类型｜标题 → 确认大纲 ----
    sid = new_session()
    d1 = chat(sid, "不用蓝本")
    print("[A1]", d1.get("phase"), card_types(d1), (d1.get("reply") or "")[:80])
    if d1.get("phase") not in {"freeform_ask", "freeform_outline", "intent"}:
        fails.append(f"A1: unexpected phase {d1.get('phase')}")
    if "intent_clarify" in card_types(d1) and d1.get("phase") != "freeform_ask":
        fails.append("A1: bare 不用蓝本 should lock freeform, not stay on entry clarify")
    if "尚未说明" in (d1.get("reply") or "") and "outline_preview" in card_types(d1):
        fails.append("A1: bogus outline on skip")
    d2 = chat(sid, "工作说明｜对话操作冒烟报告")
    print("[A2]", d2.get("phase"), card_types(d2), (d2.get("reply") or "")[:70])
    if "outline_preview" not in card_types(d2):
        fails.append("A2: no outline_preview after 类型｜标题")
    else:
        d3 = chat(sid, "确认大纲并继续")
        print("[A3]", d3.get("phase"), card_types(d3), (d3.get("reply") or "")[:80])
        if d3.get("phase") not in {
            "freeform_points",
            "ready_to_write",
            "freeform_outline",
            "create_handoff",
        }:
            fails.append(f"A3: unexpected phase after confirm {d3.get('phase')}")
        else:
            print("[A] OK")

    # ---- B: 新建蓝本口语 → meta 步（不上传文件）----
    sid = new_session()
    d1 = chat(sid, "帮我新建一个蓝本")
    print("[B1]", card_types(d1), (d1.get("reply") or "")[:70])
    if "blueprint_wizard" not in card_types(d1):
        fails.append("B1: no wizard after create phrase")
    else:
        d2 = chat(
            sid,
            action="blueprint_wizard",
            action_payload={"step": "meta", "title": "对话操作冒烟蓝本"},
        )
        wiz = next((c for c in d2.get("cards") or [] if c.get("type") == "blueprint_wizard"), None)
        step = (wiz or {}).get("payload", {}).get("step")
        print("[B2]", step, (d2.get("reply") or "")[:70])
        if step not in {"upload", "meta", "outline"}:
            fails.append(f"B2: unexpected step {step}")
        else:
            print("[B] OK")

    # ---- C: 选用 → 继续学习 → 重学第1章 → 写已分析 ----
    ready = pick_blueprint()
    if not ready:
        fails.append("C: no blueprint")
    else:
        bid = str(ready["id"])
        sid = new_session()
        d1 = chat(sid, action="select_blueprint", action_payload={"blueprint_id": bid})
        print("[C1]", card_types(d1), (d1.get("reply") or "")[:80])
        wiz = next((c for c in d1.get("cards") or [] if c.get("type") == "blueprint_wizard"), None)
        if (wiz or {}).get("payload", {}).get("mode") != "selected":
            fails.append("C1: mode!=selected")

        d2 = chat(sid, "继续学习")
        print("[C2]", d2.get("phase"), card_types(d2), (d2.get("reply") or "")[:80])
        r2 = d2.get("reply") or ""
        if "没认出" in r2:
            fails.append("C2: 继续学习 still failed chapter parse")
        elif not any(k in r2 for k in ("学", "分析", "要点", "开写", "确认目录")):
            fails.append("C2: unexpected continue-study reply")

        d3 = chat(sid, "重学第1章")
        print("[C3]", d3.get("phase"), card_types(d3), (d3.get("reply") or "")[:80])
        r3 = d3.get("reply") or ""
        if "没认出" in r3:
            fails.append("C3: 重学第1章 failed")
        elif not any(k in r3 for k in ("学", "分析", "章", "触发", "开始")):
            # 允许澄清范围卡
            if "clarify" not in card_types(d3) and "intent_clarify" not in card_types(d3):
                if "blueprint_wizard" not in card_types(d3):
                    fails.append("C3: unexpected re-study reply")

        d4 = chat(sid, "帮我写已分析的章节")
        print("[C4]", d4.get("phase"), card_types(d4), (d4.get("reply") or "")[:80])
        if "chapter_picker" not in card_types(d4) and "写" not in (d4.get("reply") or ""):
            fails.append("C4: write-analyzed unexpected")
        else:
            print("[C] OK")

    print("== fails:", fails or "none", "==")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
