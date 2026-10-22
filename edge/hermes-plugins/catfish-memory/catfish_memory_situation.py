"""「此刻」块: 主聊天每轮知道现在几点、今天还有什么会、有没有急邮件、哪些待办快到期 (10/22 P1)。

原来主聊天只有「今天: 日期 星期几」。早安页知道的日程 / 急邮件 / 待办, 主聊天得靠
模型自己想起来调三四个工具 —— 员工问「今天先做什么」「下午那个会要准备什么」, 经常
答成泛泛的建议。

数据来源 (都在本机, 只读):
  ~/.catfish/situation/calendar_today.json  Companion 每 15 分钟写 (services/situation_snapshot.rs)
  ~/.catfish/situation/unread_email.json    Companion 邮件扫描器每轮写 (只有主题 / 发件人 / 急中低)
  ~/.catfish/task_library.db                待办库本体 (Companion 和 tool-bridge 都在写)

过期就不用: 日程看日期是不是今天, 邮件超过 90 分钟不用 —— Companion 没开时不能拿旧数据
冒充「此刻」。哪一块没有数据就整块不出, 不写「今天没有会」这种可能是假的话。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional

_EMAIL_FRESH = timedelta(minutes=90)
_MAX_EVENTS = 6
_MAX_URGENT = 4
_MAX_TODOS = 5
#: 这么多天内到期的待办算「快到期」
_DUE_SOON_DAYS = 3


def _period(h: int) -> str:
    if h < 6:
        return "凌晨"
    if h < 9:
        return "早上"
    if h < 12:
        return "上午"
    if h < 14:
        return "中午"
    if h < 18:
        return "下午"
    return "晚上"


def _parse_dt(s: str, tz) -> Optional[datetime]:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.replace(tzinfo=tz) if dt.tzinfo is None else dt.astimezone(tz)


def _read_json(path: Path) -> Optional[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def calendar_lines(catfish_home: Path, now: datetime) -> List[str]:
    data = _read_json(catfish_home / "situation" / "calendar_today.json")
    if not data or data.get("date") != now.strftime("%Y-%m-%d"):
        return []
    events = data.get("events") or []
    all_day = [e for e in events if e.get("all_day")]
    timed = []
    for e in events:
        if e.get("all_day"):
            continue
        start, end = _parse_dt(e.get("start", ""), now.tzinfo), _parse_dt(e.get("end", ""), now.tzinfo)
        if start is None or (end or start) < now:
            continue  # 已经结束的不提
        timed.append((start, end, e))
    timed.sort(key=lambda t: t[0])
    lines: List[str] = []
    for start, end, e in timed[:_MAX_EVENTS]:
        # 起止相同的是「截止点」式日程 (EventKit 常见), 只写一个时间
        when = f"{start:%H:%M}" + (f"–{end:%H:%M}" if end and end != start else "")
        if start <= now:
            when += " (进行中)"
        where = f" @{e['location']}" if e.get("location") else ""
        lines.append(f"  - {when} {e.get('summary') or '(无标题)'}{where}")
    for e in all_day[:2]:
        lines.append(f"  - 全天 {e.get('summary') or '(无标题)'}")
    if not lines:
        return ["今天剩下的日程: 没有"]
    return ["今天剩下的日程:"] + lines


def email_lines(catfish_home: Path, now: datetime) -> List[str]:
    data = _read_json(catfish_home / "situation" / "unread_email.json")
    if not data:
        return []
    at = _parse_dt(data.get("updated_at", ""), now.tzinfo)
    if at is None or now - at > _EMAIL_FRESH:
        return []
    total, urgent = int(data.get("total") or 0), int(data.get("urgent") or 0)
    if total == 0:
        return ["未读邮件: 没有"]
    head = f"未读邮件 {total} 封 (急 {urgent} / 中 {int(data.get('medium') or 0)} / 低 {int(data.get('low') or 0)})"
    lines = [head + (":" if urgent else "")]
    for it in [i for i in data.get("items") or [] if i.get("urgency") == "急"][:_MAX_URGENT]:
        lines.append(f"  - 急「{it.get('subject', '')}」— {it.get('sender', '')}")
    return lines


def todo_lines(catfish_home: Path, now: datetime) -> List[str]:
    db = catfish_home / "task_library.db"
    if not db.exists():
        return []
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=1)
        try:
            rows = con.execute(
                "SELECT title, due_date_iso FROM tasks WHERE status = 'pending' "
                "ORDER BY due_date_iso IS NULL, due_date_iso, priority"
            ).fetchall()
        finally:
            con.close()
    except sqlite3.Error:
        return []
    if not rows:
        return []
    soon_limit = now + timedelta(days=_DUE_SOON_DAYS)
    soon = []
    for title, due in rows:
        d = _parse_dt(due or "", now.tzinfo)
        if d is not None and d <= soon_limit:
            tag = "已过期" if d < now else ("今天到期" if d.date() == now.date() else f"{d:%m-%d} 到期")
            soon.append(f"  - [{tag}] {title}")
    head = f"待办 {len(rows)} 项未完成"
    if not soon:
        return [head + f", {_DUE_SOON_DAYS} 天内没有到期的"]
    return [head + f", 其中 {len(soon)} 项已过期或 {_DUE_SOON_DAYS} 天内到期:"] + soon[:_MAX_TODOS]


def render_situation(catfish_home: Path, now: Optional[datetime] = None) -> str:
    now = now or datetime.now().astimezone()
    weekday = "一二三四五六日"[now.weekday()]
    lines = [f"现在: {now:%Y-%m-%d} 星期{weekday} {now:%H:%M} ({_period(now.hour)})"]
    for part in (calendar_lines, email_lines, todo_lines):
        try:
            lines += part(catfish_home, now)
        except Exception:  # noqa: BLE001 —— 一块坏了不影响别的块, 更不能拖垮 prefetch
            continue
    return (
        "## 📍 此刻 (本机日历 / 邮件 / 待办快照。列出来的以这里为准; 没列出的那类是这台机器没拿到,"
        " 需要时再用工具查, 不要当成「没有」)\n\n"
        + "\n".join(lines)
    )
