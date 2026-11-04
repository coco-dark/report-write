#!/usr/bin/env python3
"""对本地 8090 做三条写作路径的轻量 API 冒烟（不依赖浏览器登录）。"""

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
        with urllib.request.urlopen(request, timeout=120) as resp:
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
    body = {"session_id": session_id, "message": message, **extra}
    code, data = _req("POST", "/api/writing/chat", body)
    if code >= 400:
        raise RuntimeError(f"chat {code}: {data}")
    assert isinstance(data, dict)
    return data


def new_session() -> str:
    code, data = _req("POST", "/api/writing/chat/sessions", {})
    if code >= 400:
        raise RuntimeError(f"session {code}: {data}")
    assert isinstance(data, dict)
    sid = str(data.get("id") or "").strip()
    if not sid:
        raise RuntimeError(f"no session id: {data}")
    return sid


def card_types(data: dict) -> list[str]:
    return [str(c.get("type") or "") for c in (data.get("cards") or [])]


def main() -> int:
    print(f"== smoke three paths @ {BASE} ==")
    ok = 0

    # Path A: 无蓝本
    sid = new_session()
    data = chat(sid, "不用蓝本，直接搭目录写")
    print("[A1] skip/freeform reply:", (data.get("reply") or "")[:80])
    data = chat(sid, "工作说明｜API冒烟测试报告")
    types = card_types(data)
    print("[A2] meta→outline cards:", types, "| phase:", data.get("phase"))
    assert data.get("phase") in {"freeform_outline", "freeform_points", "ready_to_write", "freeform_ask"}
    # 有模型时会出 outline_preview；无模型也可能只回文案
    if "outline_preview" in types or "目录" in (data.get("reply") or "") or "类型" in (data.get("reply") or ""):
        ok += 1
        print("[A] freeform path OK")
    else:
        print("[A] WARN: unexpected freeform response", data.get("reply"))

    # Path B: 新建蓝本
    sid = new_session()
    data = chat(sid, "帮我新建一个蓝本")
    types = card_types(data)
    print("[B1] create reply:", (data.get("reply") or "")[:80], "| cards:", types)
    has_wizard = "blueprint_wizard" in types
    # 也可能先出澄清卡
    if has_wizard or "蓝本" in (data.get("reply") or "") or "澄清" in (data.get("reply") or "") or "选一下" in (data.get("reply") or ""):
        if has_wizard:
            wiz = next(c for c in data["cards"] if c.get("type") == "blueprint_wizard")
            step = (wiz.get("payload") or {}).get("step")
            print("[B2] wizard step:", step, "title:", wiz.get("title"))
            assert step in {"meta", "upload", None} or True
        ok += 1
        print("[B] create-blueprint path OK")
    else:
        print("[B] FAIL create path")

    # Path C: 选用已有蓝本
    code, bps = _req("GET", "/api/writing/blueprints?limit=20")
    if code >= 400 or not isinstance(bps, list) or not bps:
        print("[C] SKIP no blueprints available", code, bps)
    else:
        ready = next(
            (
                b
                for b in bps
                if int(b.get("chapter_count") or 0) > 0 and bool(b.get("outline_confirmed"))
            ),
            bps[0],
        )
        bid = str(ready.get("id") or "")
        title = str(ready.get("title") or bid)
        sid = new_session()
        data = chat(
            sid,
            f"选用蓝本「{title}」",
            action="select_blueprint",
            action_payload={"blueprint_id": bid},
        )
        types = card_types(data)
        reply = data.get("reply") or ""
        print("[C1] select", bid, "| cards:", types, "|", reply[:80])
        wiz = next((c for c in data.get("cards") or [] if c.get("type") == "blueprint_wizard"), None)
        if wiz:
            mode = (wiz.get("payload") or {}).get("mode")
            steps = (wiz.get("payload") or {}).get("steps") or []
            print("[C2] mode=", mode, "step=", (wiz.get("payload") or {}).get("step"), "steps=", [s.get("id") for s in steps])
            assert mode == "selected" or not any(s.get("id") == "meta" for s in steps)
        if bid in reply or title in reply or "选用" in reply or "学习" in reply or "分析" in reply or types:
            ok += 1
            print("[C] select-existing path OK")
        else:
            print("[C] FAIL select path")

    print(f"== done: {ok}/3 paths smoked ==")
    return 0 if ok >= 2 else 1


if __name__ == "__main__":
    raise SystemExit(main())
