"""待办正文: 开头的状态是不是比后面的记录旧 (11/27)。

约定「最新状态写在正文最前面」—— 早安卡片 / 周报 / 问小鲶都只读开头一段。但没有东西保证这一点: 小鲶更新待办
常往正文**末尾**追加。11/27 ITSS 一级: 开头还是 11/20 的「公示≠已发证」, 当天 09:08 取证写在九千字正文的最后一行,
当晚早安卡片照样写「等待正式发证通知」(鸿波截图「今天不是已经查到正式的公告了? 怎么记不住?」)。

stale_head() 是判据, 写入的两条路都用它当场告诉小鲶去改开头:
  · catfish_create_task 带 body 更新 (task_library.tool_create_task);
  · 脚本里直接改提醒事项 (remindctl / osascript) —— hermes 插件 todo_head_guard.py, 那边有一份同样的判据
    (插件不能 import tool-bridge), tests/test_task_notes.py 钉住两份一致。
catfish_list_tasks 也标出来 (head_stale), 没改的会一直被看到。早安那头另有一道保险 (briefing_advisor_prompts.latestProgress)。

判据: 正文超过 HEAD_CHARS 字, 开头 HEAD_CHARS 字里有日期, 且后面某一行的日期 (不晚于今天) 比开头里最晚的日期还新。
开头没写日期的不判 (每周整段重写「本周进度 / 下周计划」的待办, 下面留着带日期的历史, 不算旧)。
"""
from __future__ import annotations

import re
from datetime import date
from typing import Any

HEAD_CHARS = 300
_FULL = re.compile(r"(20\d{2})[-./年](\d{1,2})[-./月](\d{1,2})日?(?:\s*(\d{1,2}):(\d{2}))?")
#: 行首不带年份的日期 (「11/27 09:08 已取证」「- 11月27日 …」), 按今年算
_LEAD = re.compile(r"^\s*(?:[-·•*]\s*)?(\d{1,2})(?:/|月)(\d{1,2})日?(?:\s+(\d{1,2}):(\d{2}))?(?![\d/%])")
_TAG = re.compile(r"\[catfish-task:[^\]]*\]")


def _key(y: int, m: int, d: int, hh: int = 0, mm: int = 0) -> int:
    return ((y * 100 + m) * 100 + d) * 10000 + hh * 100 + mm


def latest_date_key(text: str, today: date, *, lead: bool = False) -> int:
    """一段文字里最晚的日期 (含时分) 换成能比大小的数; 今天以后的不算 (有效期至 / 截止日不是进展); 没有 → 0。"""
    limit = _key(today.year, today.month, today.day, 23, 59)
    best = 0
    for m in _FULL.finditer(text):
        k = _key(int(m[1]), int(m[2]), int(m[3]), int(m[4] or 0), int(m[5] or 0))
        if best < k <= limit and 1 <= int(m[2]) <= 12 and 1 <= int(m[3]) <= 31:
            best = k
    if lead:
        m = _LEAD.match(text)
        if m and 1 <= int(m[1]) <= 12 and 1 <= int(m[2]) <= 31:
            k = _key(today.year, int(m[1]), int(m[2]), int(m[3] or 0), int(m[4] or 0))
            if best < k <= limit:
                best = k
    return best


def _fmt(k: int) -> str:
    d, hm = divmod(k, 10000)
    y, md = divmod(d, 10000)
    return f"{y}-{md // 100:02d}-{md % 100:02d}" + (f" {hm // 100:02d}:{hm % 100:02d}" if hm else "")


def stale_head(body: Any, today: date | None = None) -> dict[str, str] | None:
    """开头的状态比后面的记录旧 → {"head_date", "newest_date", "newest_line"}; 否则 None。"""
    today = today or date.today()
    raw = _TAG.sub("", str(body or ""))
    text = re.sub(r"\s+", " ", raw).strip()
    if len(text) <= HEAD_CHARS:
        return None
    head_key = latest_date_key(text[:HEAD_CHARS], today)
    if not head_key:
        return None
    newest_key, newest = head_key, ""
    for line in raw.splitlines():
        k = latest_date_key(line, today, lead=True)
        if head_key % 10000 == 0 and k // 10000 == head_key // 10000:
            continue  # 开头只写了日期没写时分: 同一天的记录不算更新
        if k > newest_key:
            newest_key, newest = k, re.sub(r"\s+", " ", line).strip()
    if not newest or newest in text[:HEAD_CHARS]:
        return None
    return {"head_date": _fmt(head_key), "newest_date": _fmt(newest_key), "newest_line": newest[:200]}


def stale_notice(title: str, info: dict[str, str]) -> str:
    return (f"⚠️ 待办「{title}」正文开头的状态是 {info['head_date']} 的, 后面却有 {info['newest_date']} 的新进展"
            f"（「{info['newest_line'][:80]}」）。早安卡片 / 周报 / 问小鲶只读正文开头, 会按旧状态说话。"
            "现在就改这条待办的正文: 开头换成最新的【当前状态 日期】和【下一步】, 旧状态挪到后面的【历史】; 事情办完了就标完成。")
