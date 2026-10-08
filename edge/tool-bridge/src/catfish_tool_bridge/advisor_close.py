"""catfish_advisor_close_task —— 小鲶在聊天里关掉一张早安卡片 (10/8)。

# 为什么 (10/8 鸿波「这个不是已经完成填写了吗? 为什么又出来」)

员工在聊天里说「期中固定资产盘点整改已经填完了」, 小鲶没有关卡片的正规入口, 就去
手改 ``advisor_cache.json`` (删掉那张卡) 并往 ``distilled_facts.md`` 写「已完结」,
还告诉员工「早晨生成卡片只读那份记录」—— 这句是错的: 早安顾问生成卡片时会把
长期记忆 (distilledFacts)、hermes memory、journal 全部清空不读
(companion-app ``briefing_advisor.ts`` applyRelevanceFilter)。删缓存里的卡也只管到
下一次刷新, 来源邮件还在窗口里就又生成。

这里写的是早安**真正会读**的那份台账: ``~/.catfish/advisor_closed.jsonl``, 跟卡片
上的「完成」按钮同一份。匹配 (uid / 规范化标题 / 来源邮件主题) 在 Companion 侧
``src/lib/advisor_closed.ts``, 这边只负责找到卡、追加一行。

# 为什么追加而不是改写

Companion (Rust) 也写这个文件。单行 O_APPEND 不需要跨进程的锁, 两边谁也不会覆盖
谁刚写的那行。
"""
from __future__ import annotations

import json
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Any

from .advisor_io import catfish_dir

_STATUSES = ("done", "ignored", "snoozed")
_BRACKETS = re.compile(r"【[^】]*】|\[[^\]]*\]|（[^）]*）|\([^)]*\)")


def _norm_title(title: str) -> str:
    """跟 advisor_closed.ts normTitle 同一规则: 去括号内容, 去标点/符号/空白, 小写。"""
    s = _BRACKETS.sub("", title or "")
    return "".join(
        ch for ch in s
        if not ch.isspace() and unicodedata.category(ch)[0] not in ("P", "S", "Z")
    ).lower()


def _same_title(a: str, b: str) -> bool:
    x, y = _norm_title(a), _norm_title(b)
    if not x or not y:
        return False
    short, long_ = (x, y) if len(x) <= len(y) else (y, x)
    return short == long_ or (len(short) >= 8 and short in long_)


def _current_cards() -> list[dict[str, Any]]:
    try:
        data = json.loads((catfish_dir() / "advisor_cache.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    tasks = ((data or {}).get("result") or {}).get("mainTasks") or []
    return [t for t in tasks if isinstance(t, dict)]


def _next_local_midnight() -> str:
    now = datetime.now().astimezone()
    tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return tomorrow.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def close_task(args: dict[str, Any]) -> dict[str, Any]:
    title = str(args.get("title") or "").strip()
    uid = str(args.get("task_uid") or "").strip()
    status = str(args.get("status") or "done").strip()
    reopen = bool(args.get("reopen"))
    note = str(args.get("note") or "").strip()[:300]
    if not title and not uid:
        return {"ok": False, "error": "title 和 task_uid 至少给一个"}
    if status not in _STATUSES:
        return {"ok": False, "error": f"status 只能是 {'/'.join(_STATUSES)}"}

    cards = _current_cards()
    card = next((c for c in cards if uid and c.get("taskUid") == uid), None)
    if card is None and title:
        card = next((c for c in cards if _same_title(str(c.get("title") or ""), title)), None)

    entry: dict[str, Any] = {
        "op": "reopen" if reopen else "close",
        "taskUid": (card or {}).get("taskUid") or uid or None,
        "title": (card or {}).get("title") or title,
        "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "by": "chat",
    }
    if not reopen:
        entry["status"] = status
        entry["refs"] = [str(r) for r in ((card or {}).get("contextRefs") or [])][:10]
        if status == "snoozed":
            entry["until"] = _next_local_midnight()
    if note:
        entry["note"] = note
    entry = {k: v for k, v in entry.items() if v not in (None, "")}

    path = catfish_dir() / "advisor_closed.jsonl"
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    return {
        "ok": True,
        "op": entry["op"],
        "matched_card": (
            {"taskUid": card.get("taskUid"), "title": card.get("title")} if card else None
        ),
        "current_cards": [str(c.get("title") or "") for c in cards],
        "ledger": str(path),
        "effect": (
            "已写入早安「已关闭」台账。之后生成的卡片按 uid / 标题 / 来源邮件主题匹配, "
            "同一封邮件线上的催办、回复、转发都不会再开卡; 早安页刷新后这张卡即消失。"
            if not reopen else "已撤销关闭, 下次早安生成时这件事可以重新出现。"
        ) + (
            "" if card else " 注意: 当前早安卡片里没找到这条, 只按标题记录 —— "
            "请核对 current_cards 里的标题, 必要时用准确标题再调一次。"
        ),
    }
