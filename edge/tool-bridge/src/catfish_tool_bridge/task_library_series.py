"""按期滚动的任务: 新一期建出来, 上一期自动收口 (11/25)。

11/25 鸿波「早安里很多很久之前的数据」: 「资质邮件周扫描 11/02-11/09」「…11/09-11/16」「…11/13-11/20」
三条同时挂着未完成 —— 每周定时任务新建一条, 上一周那条没人关, 早安把所有未完成任务都拿去出卡。

判据只认标题里带**日期区间**的 (MM/DD-MM/DD, 可带年份): 去掉区间后标题一样 = 同一个系列。
不带区间的标题一律不算, 免得把两件不同的事当成前后两期。
"""
from __future__ import annotations

import re
import sqlite3
from typing import Any, Callable

_DATE = r"(?:\d{4}[/.\-年])?\d{1,2}[/.月]\d{1,2}日?"
_RANGE_RE = re.compile(rf"{_DATE}\s*(?:-|–|—|~|～|至|到)\s*{_DATE}")


def series_key(title: str) -> str | None:
    """标题带日期区间 → 去掉区间后的系列名 (去空格标点, 小写); 不带 → None。"""
    if not _RANGE_RE.search(title):
        return None
    rest = re.sub(r"[\s\W_]+", "", _RANGE_RE.sub("", title)).lower()
    return rest if len(rest) >= 2 else None


def supersede_previous(
    conn: sqlite3.Connection, new_task: dict[str, Any], close_row: Callable[[sqlite3.Row, str, str], None],
) -> list[dict[str, str]]:
    """关掉同系列里更早的未完成任务 (标成已完成, 正文留一句去向), 返回被关的那些。"""
    key = series_key(str(new_task.get("title") or ""))
    if key is None:
        return []
    rows = conn.execute(
        "SELECT * FROM tasks WHERE status IN ('pending', 'in_progress') AND task_id != ? "
        "AND created_at <= ? ORDER BY created_at",
        (new_task["task_id"], new_task["created_at"]),
    ).fetchall()
    closed: list[dict[str, str]] = []
    for row in rows:
        if series_key(row["title"]) != key or row["title"] == new_task["title"]:
            continue
        close_row(row, "completed", f"下一期「{new_task['title']}」已建，本期收口；没办完的事项看这条正文")
        closed.append({"task_id": row["task_id"], "title": row["title"]})
    return closed


def carry_over_note(closed: list[dict[str, str]]) -> str:
    return "上一期已自动收口（没办完的事项在那条正文里）：" + "；".join(
        f"{c['title']} (task_id={c['task_id']})" for c in closed)
