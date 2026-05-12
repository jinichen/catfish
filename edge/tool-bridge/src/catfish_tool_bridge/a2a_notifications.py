"""BL-FED2.6 (5/12 鸿波拍板) — a2a 通知读取 + catfish_list_a2a_help tool.

# 真用途

BL-FED2.4 反馈环让 bob 答 alice 后自动写 journal + notification jsonl. 但**员工怎
么知道自己今天帮过谁**? Companion 启动时不会自动 popup (实时弹窗确认是
BL-FED2.3-FU 大改, 5/12 内不做).

退而求其次: **员工主动问**鲶鱼"今天我帮过谁?" → LLM 调本 tool → 列出来.

# 数据源

`~/.catfish/a2a_notifications.jsonl` (gateway BL-FED2.6 写的) 每行 JSON:
```json
{"ts":"2026-05-12T22:30:15","from_sub":"alice@ffcs.cn","question":"资质审核怎么搞?",
 "purpose":"expert_consult:资质审核","answer_preview":"走 OA 工单...",
 "chunks_count":5,"duration_ms":900,"seen":false}
```

# 过滤选项

- `hours_back`: 默认 24, 看过去几小时 (None = 全部)
- `unseen_only`: 默认 false (但 Companion 进入时可传 true 算徽章)
- `from_sub`: 按问问的人过滤 (例 "我帮过 alice 哪些?")
- `tag`: 按 purpose 子串过滤 ("我帮人解决过资质问题?")

# 5/13 之后 Companion 怎么接

- Companion 启动时 rust 端 (`commands/a2a_notifications.rs`) 直接读 jsonl, 算
  unseen count, 显示徽章数字
- 点徽章 → 调本 tool 列内容 → 渲染卡片
- 员工标"撤销" → 写一行 `decision: deny` 到下一个 jsonl, 后续 BL-FED2.4-FU
  做"自动加 ALLOW.md deny rule"
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("catfish.tool_bridge.a2a_notifications")


def _notifications_path() -> Path:
    """跟 gateway/a2a_journal_hook._notifications_path() 对齐.

    CATFISH_HOME/a2a_notifications.jsonl, fallback ~/.catfish/...
    """
    catfish_home = os.environ.get("CATFISH_HOME", "").strip()
    if catfish_home:
        return Path(catfish_home).expanduser() / "a2a_notifications.jsonl"
    return Path.home() / ".catfish" / "a2a_notifications.jsonl"


def _parse_ts(s: str) -> datetime | None:
    """ISO 8601 → datetime (tz-aware preferred). 失败返 None."""
    if not s:
        return None
    try:
        # 兼容 'Z' 后缀 + 无 tz
        s2 = s.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s2)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def load_notifications() -> list[dict[str, Any]]:
    """读 jsonl, 返完整 list (旧到新). 不存在 / 损坏 → 返空 list."""
    p = _notifications_path()
    if not p.exists():
        return []
    out: list[dict[str, Any]] = []
    try:
        with p.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue  # 坏行跳过
    except Exception as e:  # noqa: BLE001
        logger.warning("读 notifications.jsonl 失败: %s", e)
        return []
    return out


def filter_notifications(
    notifications: list[dict[str, Any]],
    *,
    hours_back: int | None = 24,
    unseen_only: bool = False,
    from_sub: str = "",
    tag_substr: str = "",
) -> list[dict[str, Any]]:
    """按条件过滤. notifications 入参是 load_notifications 返."""
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=hours_back) if hours_back is not None else None

    out: list[dict[str, Any]] = []
    for n in notifications:
        if cutoff is not None:
            ts = _parse_ts(n.get("ts", ""))
            if ts is None or ts < cutoff:
                continue
        if unseen_only and n.get("seen") is True:
            continue
        if from_sub and n.get("from_sub", "") != from_sub:
            continue
        if tag_substr and tag_substr.lower() not in (n.get("purpose", "") or "").lower():
            continue
        out.append(n)
    return out


def tool_list_a2a_help(args: dict[str, Any]) -> dict[str, Any]:
    """catfish_list_a2a_help 工具入口.

    args:
      hours_back: int (默认 24) — 看过去几小时. 0/null = 全部.
      unseen_only: bool (默认 false) — 只看未"读"的 (Companion 红点逻辑用)
      from_sub: str (可选) — 按问问的同事 sub 过滤
      tag_substr: str (可选) — 按 purpose 子串过滤 (例 '资质' 命中 'expert_consult:资质审核')
      max_items: int (默认 50) — 返多少条 (上限避免炸 prompt)
    """
    hours_back_raw = args.get("hours_back", 24)
    if hours_back_raw in (None, 0, "0"):
        hours_back = None
    else:
        try:
            hours_back = int(hours_back_raw)
        except (TypeError, ValueError):
            hours_back = 24
    unseen_only = bool(args.get("unseen_only", False))
    from_sub = (args.get("from_sub") or "").strip()
    tag_substr = (args.get("tag_substr") or "").strip()
    try:
        max_items = max(1, min(200, int(args.get("max_items") or 50)))
    except (TypeError, ValueError):
        max_items = 50

    raw = load_notifications()
    filtered = filter_notifications(
        raw,
        hours_back=hours_back,
        unseen_only=unseen_only,
        from_sub=from_sub,
        tag_substr=tag_substr,
    )
    # 倒序 (最新先), 截上限
    filtered.sort(key=lambda n: n.get("ts", ""), reverse=True)
    truncated = filtered[:max_items]

    by_sub: dict[str, int] = {}
    by_purpose: dict[str, int] = {}
    for n in filtered:
        s = n.get("from_sub", "?")
        by_sub[s] = by_sub.get(s, 0) + 1
        p = n.get("purpose", "?") or "?"
        by_purpose[p] = by_purpose.get(p, 0) + 1

    summary_bits = [f"📨 过去 {hours_back if hours_back else '∞'}h 共 {len(filtered)} 次 a2a 协助"]
    if by_sub:
        top_subs = sorted(by_sub.items(), key=lambda x: -x[1])[:3]
        summary_bits.append("最常问你的: " + ", ".join(f"{s}({c})" for s, c in top_subs))
    if by_purpose:
        top_p = sorted(by_purpose.items(), key=lambda x: -x[1])[:3]
        summary_bits.append("最常领域: " + ", ".join(f"{p}({c})" for p, c in top_p))
    summary = ". ".join(summary_bits) + "."

    return {
        "ok": True,
        "total": len(filtered),
        "items": truncated,
        "by_sub": by_sub,
        "by_purpose": by_purpose,
        "summary": summary,
        "path": str(_notifications_path()),
    }


__all__ = [
    "load_notifications",
    "filter_notifications",
    "tool_list_a2a_help",
]
