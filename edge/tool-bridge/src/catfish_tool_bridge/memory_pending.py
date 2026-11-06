"""待你确认的记忆整理建议 (11/4)。

鸿波 11/4 问「MEMORY.md 快满了为什么没有自动处理」: hermes 聊完后的后台回顾会提合并 / 删旧条目的建议,
但后台**不许自己删改记忆** (hermes write_approval 闸, 只许 add), 删改建议存成
``<HERMES_HOME>/pending/memory/<id>.json`` 等人批 —— 原来只有 hermes 命令行 ``/memory pending`` 看得到,
鲶鱼界面上没有入口, 建议一直压着, 记忆只增不减。

这里把它们拿给「我的记忆」卡片看 / 批 / 放弃, 全用 hermes 自己的函数:
- 列: ``write_approval.list_pending``
- 同意: ``memory_tool.apply_memory_pending`` + ``load_on_disk_store`` (跟网关 ``/memory approve`` 同一条路:
  只改建议生成时钉住的那条原文, 那条已经变了就拒绝, 不会误删后来的新条目), 成功才删掉这条建议
- 放弃: ``write_approval.discard_pending``

USER.md (target=user) 和 MEMORY.md (target=memory) 的建议都在同一个 pending/memory 目录里, 一起列。
"""
from __future__ import annotations

import re
from typing import Any

_ID_RE = re.compile(r"^[0-9a-f]{6,32}$")
_OPS = ("add", "replace", "patch", "remove")


def _ops(payload: dict[str, Any]) -> list[dict[str, Any]]:
    raw = (payload.get("operations") or []) if payload.get("action") == "batch" else [payload]
    out: list[dict[str, Any]] = []
    for op in raw:
        if not isinstance(op, dict) or op.get("action") not in _OPS:
            continue
        out.append({
            "action": op["action"],
            # replace / patch / remove 要动的那条 (建议生成时钉住的全文); 没钉的老建议只有 old_text
            "entry": str(op.get("matched_entry") or op.get("old_text") or ""),
            "pinned": bool(op.get("matched_entry")),
            # add / replace 写进去的新内容; patch 是替换 old_text 的那一段 (条目其余部分不动)
            "content": str(op.get("content") or op.get("new_text") or ""),
            "old_text": str(op.get("old_text") or "") if op["action"] == "patch" else "",
        })
    return out


def _current_entries(store, target: str) -> list[str]:
    try:
        return list(store._entries_for(target))  # noqa: SLF001 — hermes 没有公开的读接口
    except Exception:  # noqa: BLE001
        return []


def list_pending() -> dict[str, Any]:
    from tools import write_approval as wa  # noqa: PLC0415
    from tools.memory_tool import load_on_disk_store  # noqa: PLC0415

    store = load_on_disk_store()
    current = {t: set(_current_entries(store, t)) for t in ("user", "memory")}
    items: list[dict[str, Any]] = []
    for rec in wa.list_pending(wa.MEMORY):
        payload = rec.get("payload") or {}
        target = payload.get("target") if payload.get("target") in ("user", "memory") else "memory"
        ops = _ops(payload)
        # 要删 / 改的那条已经不在了 (员工自己删过、别的整理改过) → 同意也会被 hermes 拒, 提示放弃
        stale = any(
            op["action"] != "add" and (not op["pinned"] or op["entry"] not in current[target]) for op in ops
        )
        items.append({
            "id": str(rec.get("id") or ""),
            "target": target,
            "origin": rec.get("origin") or "foreground",
            "created_at": rec.get("created_at") or 0,
            "ops": ops,
            "stale": stale,
        })
    return {"ok": True, "items": items}


def resolve(params: dict[str, Any]) -> dict[str, Any]:
    from tools import write_approval as wa  # noqa: PLC0415
    from tools.memory_tool import apply_memory_pending, load_on_disk_store  # noqa: PLC0415

    pid = str(params.get("id") or "").strip()
    if not _ID_RE.match(pid):
        raise ValueError("建议 id 不对")
    approve = bool(params.get("approve"))
    rec = wa.get_pending(wa.MEMORY, pid)
    if not rec:
        return {"ok": False, "error": "这条建议已经不在了 (可能刚处理过)"}
    from . import memory_compact_moves as moves  # noqa: PLC0415
    if not approve:
        wa.discard_pending(wa.MEMORY, pid)
        moves.drop(pid)
        return {"ok": True, "applied": False}
    # 11/06: 「挪进知识库」的建议 —— 先写知识库, 写成了再把记忆那条换成指针 (反过来的话指针指向一个不存在的页)
    moved = moves.apply(pid)
    if not moved.get("ok"):
        return {"ok": False, "error": moved.get("error")}
    result = apply_memory_pending(rec.get("payload") or {}, load_on_disk_store())
    if not result.get("success"):
        return {"ok": False, "error": _plain_error(str(result.get("error") or ""))}
    wa.discard_pending(wa.MEMORY, pid)
    moves.drop(pid)
    return {"ok": True, "applied": True, **({"wiki": moved["wiki"]} if moved.get("wiki") else {})}


def _plain_error(msg: str) -> str:
    low = msg.lower()
    if "retry in a moment" in low:
        return "记忆文件正被占用, 过一会儿再点一次"
    if "would put memory at" in low or "exceed" in low:
        return "照这条建议改完会超出记忆上限, 先删几条旧的再试"
    if "predates entry pinning" in low or "no entry matched" in low or "entry changed since" in low:
        return "要改的那条记忆已经变了, 这条建议用不上了, 点「不要」放弃就行"
    return msg or "没改成"
