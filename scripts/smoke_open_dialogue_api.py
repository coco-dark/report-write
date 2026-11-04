#!/usr/bin/env python3
"""开放式自由对话冒烟：只发口语 message，少用/不用 action。

模拟真实用户多轮闲聊式推进：无蓝本、选用已有、新建蓝本三条路径。
"""

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
    code, data = _req(
        "POST",
        "/api/writing/chat",
        {"session_id": session_id, "message": message, **extra},
    )
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


def cards(data: dict) -> list[str]:
    return [str(c.get("type") or "") for c in (data.get("cards") or [])]


def reply(data: dict) -> str:
    return str(data.get("reply") or "")


def turn(label: str, data: dict) -> None:
    r = reply(data).replace("\n", " ")
    print(f"[{label}] phase={data.get('phase')} cards={cards(data)}")
    print(f"       {r[:110]}")


def bad(*parts: str) -> bool:
    text = " ".join(parts)
    return any(
        x in text
        for x in (
            "没认出",
            "尚未说明",
            "用户希望",
            "再说一下想做什么",
            "再说一下想怎么",
        )
    )


def pick_blueprint_title() -> tuple[str, str] | None:
    code, bps = _req("GET", "/api/writing/blueprints?limit=30")
    if not isinstance(bps, list) or not bps:
        return None
    ready = next(
        (
            b
            for b in bps
            if int(b.get("chapter_count") or 0) > 0 and b.get("outline_confirmed")
        ),
        None,
    ) or bps[0]
    title = str(ready.get("title") or ready.get("name") or "").strip()
    bid = str(ready.get("id") or "").strip()
    if not bid:
        return None
    return bid, title or bid


def main() -> int:
    print(f"== open dialogue smoke @ {BASE} ==")
    fails: list[str] = []

    # ========== Path F: 无蓝本自由写 ==========
    print("\n-- F freeform open dialogue --")
    sid = new_session()
    d = chat(sid, "嗨，我想写个东西，不太想找蓝本")
    turn("F1", d)
    # 可能仍 clarify；用口语锁 freeform
    if "intent_clarify" in cards(d) or d.get("phase") == "intent":
        d = chat(sid, "不用蓝本，我自己定目录就行")
        turn("F1b", d)
    if d.get("phase") not in {"freeform_ask", "freeform_outline"} and "类型" not in reply(d):
        if "不用蓝本" not in reply(d) and "大纲" not in reply(d) and "目录" not in reply(d):
            fails.append("F1: did not enter freeform path")

    d = chat(sid, "写个工作说明吧，就叫开放对话冒烟报告")
    turn("F2", d)
    # 若仍要类型｜标题格式
    if "outline_preview" not in cards(d) and d.get("phase") != "freeform_outline":
        d = chat(sid, "工作说明｜开放对话冒烟报告")
        turn("F2b", d)
    if "outline_preview" not in cards(d):
        fails.append("F2: no outline after topic")
    if bad(reply(d)):
        fails.append("F2: bogus/error reply")

    d = chat(sid, "先这样吧，目录可以了，确认大纲")
    turn("F3", d)
    r3 = reply(d)
    if "缺少写作任务" in r3 or bad(r3):
        fails.append("F3: error on confirm")
    elif d.get("phase") == "freeform_outline" and "搭了" in r3 and "已确认" not in r3:
        fails.append("F3: confirm phrase regenerated outline instead of confirming")
    elif d.get("phase") not in {
        "freeform_points",
        "ready_to_write",
        "create_handoff",
        "freeform_outline",
    }:
        d = chat(sid, "确认大纲并继续")
        turn("F3b", d)
        if d.get("phase") not in {"freeform_points", "ready_to_write", "create_handoff"}:
            fails.append(f"F3: unexpected phase {d.get('phase')}")
        else:
            print("[F] OK")
    else:
        print("[F] OK")

    # ========== Path S: 口语选用已有蓝本 ==========
    print("\n-- S select open dialogue --")
    picked = pick_blueprint_title()
    if not picked:
        fails.append("S: no blueprint available")
    else:
        bid, title = picked
        sid = new_session()
        # 先口语匹配/选用（不带 action）
        d = chat(sid, f"我想用已有蓝本，有没有叫「{title}」的？用那个")
        turn("S1", d)
        wiz = next(
            (c for c in d.get("cards") or [] if c.get("type") == "blueprint_wizard"),
            None,
        )
        mode = (wiz or {}).get("payload", {}).get("mode") if wiz else None
        selected = mode == "selected" or (
            "就用" in reply(d) or "选用" in reply(d) or "蓝本" in reply(d)
        )
        if mode != "selected":
            # 口语匹配失败时退一步：仍用口语「选用」+ 列表卡，再 action 兜底并标注
            d = chat(
                sid,
                action="select_blueprint",
                action_payload={"blueprint_id": bid},
            )
            turn("S1b_action_fallback", d)
            wiz = next(
                (c for c in d.get("cards") or [] if c.get("type") == "blueprint_wizard"),
                None,
            )
            mode = (wiz or {}).get("payload", {}).get("mode") if wiz else None
            if mode != "selected":
                fails.append(f"S1: mode={mode} after select")
            else:
                print("       (note: natural match missed; used select action)")

        d = chat(sid, "那先把还没学的学一下吧")
        turn("S2", d)
        if "没认出" in reply(d):
            fails.append("S2: study-all parse fail")
        elif not any(k in reply(d) for k in ("学", "分析", "开写", "确认目录")):
            fails.append("S2: unexpected study reply")

        d = chat(sid, "等等，第1章我想再学一遍")
        turn("S3", d)
        if "没认出" in reply(d):
            fails.append("S3: re-study parse fail")
        # 若范围澄清，口语回答
        if "intent_clarify" in cards(d) or "子章节" in reply(d) or "整章" in reply(d):
            d = chat(sid, "整章一起学就行")
            turn("S3b", d)
            if "没认出" in reply(d):
                fails.append("S3b: scope resolve fail")

        d = chat(sid, "先别学那么多了，帮我挑已分析的章开写")
        turn("S4", d)
        if "chapter_picker" not in cards(d) and "写" not in reply(d):
            fails.append("S4: write-analyzed unexpected")
        elif bad(reply(d)):
            fails.append("S4: error reply")
        else:
            print("[S] OK")

    # ========== Path C: 口语新建蓝本（停在上传，不传文件）==========
    print("\n-- C create open dialogue --")
    sid = new_session()
    d = chat(sid, "帮我新建一个蓝本，名字就叫开放对话新建蓝本")
    turn("C1", d)
    if "blueprint_wizard" not in cards(d):
        d = chat(sid, "创建新蓝本")
        turn("C1b", d)
    if "blueprint_wizard" not in cards(d):
        fails.append("C1: no wizard card")
    else:
        wiz = next(
            (c for c in d.get("cards") or [] if c.get("type") == "blueprint_wizard"),
            None,
        )
        step = (wiz or {}).get("payload", {}).get("step")
        # 若还停在 meta，口语补名称
        if step == "meta":
            d = chat(sid, "蓝本名称：开放对话新建蓝本")
            turn("C2", d)
            wiz = next(
                (c for c in d.get("cards") or [] if c.get("type") == "blueprint_wizard"),
                None,
            )
            step = (wiz or {}).get("payload", {}).get("step")
            # 卡片提交兜底
            if step == "meta":
                d = chat(
                    sid,
                    action="blueprint_wizard",
                    action_payload={"step": "meta", "title": "开放对话新建蓝本"},
                )
                turn("C2b_action_fallback", d)
                wiz = next(
                    (c for c in d.get("cards") or [] if c.get("type") == "blueprint_wizard"),
                    None,
                )
                step = (wiz or {}).get("payload", {}).get("step")
        if step not in {"upload", "meta", "outline", "outline_confirm", "study"}:
            fails.append(f"C2: unexpected step {step}")
        else:
            print(f"[C] OK step={step}")

    print("\n== fails:", fails or "none", "==")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
