#!/usr/bin/env python3
"""三条路径加深 API 冒烟：无蓝本问元信息、新建 meta、选用后对话学习。"""

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


def main() -> int:
    print(f"== deep smoke @ {BASE} ==")
    fails: list[str] = []

    # ---- A: 无蓝本：先问类型｜标题，再出目录 ----
    sid = new_session()
    d1 = chat(sid, "不用蓝本，直接搭目录写")
    r1 = d1.get("reply") or ""
    print("[A1]", d1.get("phase"), r1[:90])
    if "尚未说明" in r1 or "搭了" in r1 and "用户希望" in r1:
        fails.append("A1: skip still auto-generated bogus outline title")
    if d1.get("phase") not in {"freeform_ask", "freeform_outline", "intent"} and "类型" not in r1:
        # 允许仍停在 ask；不允许带着旁白标题直接出大纲
        if "outline_preview" in card_types(d1) and ("用户希望" in r1 or "尚未说明" in r1):
            fails.append("A1: outline_preview with bogus title")
    d2 = chat(sid, "工作说明｜深度冒烟测试报告")
    print("[A2]", d2.get("phase"), card_types(d2), (d2.get("reply") or "")[:80])
    if "outline_preview" not in card_types(d2) and "目录" not in (d2.get("reply") or ""):
        fails.append("A2: expected outline after 类型｜标题")
    else:
        print("[A] OK")

    # ---- B: 新建蓝本 → meta 卡 → 填名称 ----
    sid = new_session()
    d1 = chat(sid, action="begin_blueprint_wizard", action_payload={})
    print("[B1]", card_types(d1), (d1.get("reply") or "")[:70])
    wiz = next((c for c in d1.get("cards") or [] if c.get("type") == "blueprint_wizard"), None)
    if not wiz or (wiz.get("payload") or {}).get("step") not in {"meta", "upload"}:
        # 口语再试
        d1 = chat(sid, "帮我新建一个蓝本")
        wiz = next((c for c in d1.get("cards") or [] if c.get("type") == "blueprint_wizard"), None)
        print("[B1b]", card_types(d1), (d1.get("reply") or "")[:70])
    if not wiz:
        fails.append("B1: no blueprint_wizard card")
    else:
        d2 = chat(
            sid,
            action="blueprint_wizard",
            action_payload={"step": "meta", "title": "深度冒烟新建蓝本"},
        )
        print("[B2]", card_types(d2), (d2.get("reply") or "")[:80])
        wiz2 = next((c for c in d2.get("cards") or [] if c.get("type") == "blueprint_wizard"), None)
        step = (wiz2 or {}).get("payload", {}).get("step") if wiz2 else ""
        bid = (wiz2 or {}).get("payload", {}).get("blueprint_id") if wiz2 else ""
        if step == "meta" and not bid:
            # 有的实现仍停在 meta 等待上传；有 bid 更好
            print("[B] OK (meta submitted / still on wizard)")
        else:
            print("[B] OK step=", step, "bid=", bid)

    # ---- C: 选用已有 → 对话「开始学习」----
    code, bps = _req("GET", "/api/writing/blueprints?limit=30")
    ready = None
    if isinstance(bps, list):
        ready = next(
            (b for b in bps if int(b.get("chapter_count") or 0) > 0 and b.get("outline_confirmed")),
            None,
        ) or (bps[0] if bps else None)
    if not ready:
        fails.append("C: no blueprint to select")
    else:
        bid = str(ready["id"])
        sid = new_session()
        d1 = chat(sid, action="select_blueprint", action_payload={"blueprint_id": bid})
        print("[C1]", card_types(d1), (d1.get("reply") or "")[:90])
        wiz = next((c for c in d1.get("cards") or [] if c.get("type") == "blueprint_wizard"), None)
        mode = (wiz or {}).get("payload", {}).get("mode") if wiz else None
        if mode != "selected":
            fails.append(f"C1: expected mode=selected got {mode}")
        d2 = chat(sid, "开始学习")
        print("[C2]", d2.get("phase"), card_types(d2), (d2.get("reply") or "")[:90])
        r2 = d2.get("reply") or ""
        if not any(k in r2 for k in ("学", "分析", "要点", "确认目录", "开写")):
            fails.append("C2: study dialogue reply unexpected")
        else:
            # 再试开写口语（可能因未学完而提示）
            d3 = chat(sid, "帮我写已分析的章节")
            print("[C3]", d3.get("phase"), card_types(d3), (d3.get("reply") or "")[:90])
            print("[C] OK")

    print("== fails:", fails or "none", "==")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
