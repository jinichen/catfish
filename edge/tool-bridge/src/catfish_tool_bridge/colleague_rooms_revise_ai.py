"""群里一起改文档 (colleague_rooms_revise.py) 里跟小鲶有关的两样:
  · suggest       冲突处让小鲶给一版兼顾各人改法的文字 (JSON-RPC room/revise_suggest, Rust 注入员工令牌; 只回建议, 用不用发起人点)
  · tool_dispatch 小鲶的工具 catfish_form 的 revise_* action (发起 / 进度 / 合并 / 定冲突 / 催 / 发出)
"""
from __future__ import annotations

import json
import re
from pathlib import PurePath
from typing import Any

from . import colleague_rooms as cr
from . import forms_net
from .colleague_rooms_revise import _get, _owner_rec, _put, decide, merge, progress, publish, remind, start

# ── 小鲶的建议 (要调大模型, 异步; Rust 注入令牌) ───────────────────────────

_NUM = re.compile(r"\d+(?:\.\d+)?")


def check_suggestion(text: str, base: str, options: list[str]) -> str | None:
    """建议稿里的数字 (金额 / 日期 / 数量) 必须来自原文或某个人的改法; 对不上返回原因。"""
    allowed = set(_NUM.findall(base)) | {n for o in options for n in _NUM.findall(o)}
    bad = [n for n in _NUM.findall(text) if n not in allowed]
    return f"建议稿里有原文和大家改法里都没有的数字 ({'、'.join(bad[:3])}), 不采用" if bad else None


async def suggest(params: dict[str, Any]) -> dict[str, Any]:
    from .gateway_llm import call_llm  # noqa: PLC0415
    token, gw = str(params.get("auth_token") or "").strip(), str(params.get("gateway_url") or "").strip().rstrip("/")
    if not token or not gw:
        raise ValueError("没登录 (拿不到访问令牌), 登录后再让小鲶给建议")
    room_id, rev_id, cid = str(params.get("room_id") or ""), str(params.get("rev_id") or ""), str(params.get("cid") or "")
    import asyncio  # noqa: PLC0415
    an = await asyncio.to_thread(merge, room_id, rev_id)
    c = next((c for c in an["conflicts"] if c["cid"] == cid), None)
    if not c or c["kind"] != "text":
        raise ValueError("这一处没法给文字建议")
    opts = "\n".join(f"【{'、'.join(o['names'])}的改法】\n{o['text']}" for o in c["options"])
    msgs = [{"role": "system", "content": (
        "你在帮发起人合并几位同事对同一份文档同一处的修改。给出一版兼顾各人改法的文字: 各人改的是不同的地方就都保留; "
        "改的是同一个数 / 同一个说法、意思相反或互相排斥, 就不要硬合, 只回答 NONE。"
        "不要编造任何原文和改法里都没有的数字、日期、人名、金额。只输出合并后的那段文字本身, 不要解释、不要加引号。")},
        {"role": "user", "content": f"位置: {c['where']}\n【原文】\n{c['base']}\n{opts}"}]
    out = await call_llm(msgs, source="plugin:toolbridge-room-revise", gateway_url=gw, auth_token=token,
                         temperature=0.2, max_tokens=1500, timeout_s=120.0)
    text = re.sub(r"<think>.*?</think>", "", out or "", flags=re.S).strip().strip("「」\"'")
    if not text or text.upper() == "NONE":
        res = {"text": None, "reason": "几种改法意思相反, 小鲶不硬合 —— 请你定"}
    else:
        bad = check_suggestion(text, c["base"], [o["text"] for o in c["options"]])
        res = {"text": None, "reason": bad} if bad else {"text": text, "reason": None}
    rec = _owner_rec(rev_id)
    rec.setdefault("suggestions", {})[cid] = res
    _put(rec)
    return {"ok": True, **res}


# ── 小鲶的工具 (catfish_form 的 revise_* action) ───────────────────────────
# 发起 / 催 / 发出合并版会给同事发消息: 先不带 confirmed 念给员工, 同意才发。下载修改 / 交回 / 不改了只有员工自己在卡片上点。

def _room_of(rev_id: str) -> str:
    rec = _get(rev_id)
    if not rec:
        raise LookupError("找不到这一轮修改 (rev_id 不对, 或者不是你发起 / 被请的)")
    return rec["room_id"]


def tool_dispatch(action: str, args: dict[str, Any]) -> dict[str, Any]:
    rev_id = str(args.get("rev_id") or "")
    if action == "revise_list":
        with cr.store.tx() as con:
            recs = [json.loads(r["data"]) for r in con.execute("SELECT data FROM revise")]
        out = []
        for r in sorted(recs, key=lambda r: r.get("at") or r.get("opened_at") or "", reverse=True)[:20]:
            try:
                pr = progress(r["room_id"], r["rev_id"])
            except (ValueError, LookupError):
                continue
            out.append({"rev_id": r["rev_id"], "title": r["title"], "room": cr.load(r["room_id"]).get("name"), "mine": pr["owner"],
                        "version": r["version"], "people": {p["name"]: p["state"] for p in pr["people"]},
                        "merged": (pr["merged"] or {}).get("version"), "closed": pr["closed"]})
        return {"ok": True, "rounds": out}
    if action == "revise_start":
        room = cr.load(str(args.get("room_id") or ""))
        eds = [str(x).lower() for x in args.get("recipients") or []] or cr._joined_others(room, forms_net.me())
        if not args.get("confirmed"):
            return {"ok": True, "needs_confirmation": True,
                    "proposal": f"在群「{room.get('name')}」里请 {'、'.join(cr._label(room, e) for e in eds)} 修改 {PurePath(str(args.get('path') or '')).name}"
                                + (f", {args['deadline']} 截止" if args.get("deadline") else ""),
                    "note": "念给员工听, 员工同意后带 confirmed=true 再调"}
        return start(room["room_id"], {"path": args.get("path"), "editors": eds, "deadline": args.get("deadline"),
                                        "note": args.get("note"), "title": args.get("title")})
    room_id = _room_of(rev_id)
    if action == "revise_status":
        return progress(room_id, rev_id)
    if action == "revise_merge":
        return merge(room_id, rev_id)
    if action == "revise_decide":
        return decide(room_id, rev_id, args.get("cid"), args.get("pick"), args.get("text"))
    if action in ("revise_remind", "revise_publish"):
        if not args.get("confirmed"):
            pr = progress(room_id, rev_id)
            what = ("催 " + "、".join(p["name"] for p in pr["people"] if p["state"] in ("pending", "editing")) + " 交回"
                    if action == "revise_remind" else f"把《{pr['card']['title']}》的合并版发到群里")
            return {"ok": True, "needs_confirmation": True, "proposal": what, "note": "念给员工听, 员工同意后带 confirmed=true 再调"}
        return remind(room_id, rev_id) if action == "revise_remind" else publish(room_id, rev_id, args.get("note"))
    raise ValueError("revise_ 只能是 list / start / status / merge / decide / publish / remind")


