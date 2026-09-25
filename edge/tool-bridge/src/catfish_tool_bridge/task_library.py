"""本机用户任务库。

这和 ``task_manager`` 有意分开：task_manager 保存后台执行任务；本模块保存
员工的长期行动，并把 Reminders 作为可同步的个人提醒投影。
"""
from __future__ import annotations

import hashlib
import os
import platform
import re
import sqlite3
import time
from datetime import datetime, time as day_start, timedelta
from pathlib import Path
from typing import Any


_COMPLETED_STATUSES = {"completed", "cancelled"}
_VALID_STATUSES = {"pending", "in_progress", "completed", "cancelled"}
_SCOPES = {"active", "today", "week", "overdue", "all"}
_SNAPSHOT_LIMIT = 500  # Reminders 全量快照上限; 撑满就当可能截断, 不据此关任务


def _db_path() -> Path:
    configured = str(os.environ.get("CATFISH_TASK_LIBRARY_PATH", "")).strip()
    return Path(configured) if configured else Path.home() / ".catfish" / "task_library.db"


def _connect() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tasks (
            task_id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            due_date_iso TEXT,
            body TEXT NOT NULL DEFAULT '',
            priority INTEGER NOT NULL DEFAULT 0,
            source TEXT NOT NULL DEFAULT 'manual',
            source_id TEXT,
            list_name TEXT,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_due ON tasks(due_date_iso)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_source ON tasks(source, source_id)")
    # 9/10: 库级元数据 (目前只有一项: 上次成功导入 Reminders 的时间)
    conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.commit()
    return conn


_META_REMINDERS_IMPORTED_AT = "reminders_imported_at"


def _meta_get_float(key: str) -> float | None:
    with _connect() as conn:
        row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    try:
        return float(row["value"]) if row else None
    except (TypeError, ValueError):
        return None


def _meta_set(key: str, value: str) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        conn.commit()


def _now() -> float:
    return time.time()


def _parse_due(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "")
    if len(text) > 19 and text[19] in "+-":
        text = text[:19]
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed.replace(tzinfo=None)


def _scope_bounds(now: datetime) -> tuple[datetime, datetime]:
    today = datetime.combine(now.date(), day_start.min)
    week_start = today - timedelta(days=today.weekday())
    return week_start, week_start + timedelta(days=7)


def _stable_id(source: str, source_id: str | None, title: str, due: str | None) -> str:
    if source_id:
        return f"{source}:{source_id}"
    raw = f"{source}\0{title}\0{due or ''}".encode("utf-8")
    return f"{source}:generated-{hashlib.sha256(raw).hexdigest()[:20]}"


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    result = dict(row)
    result["completed"] = result["status"] == "completed"
    result["reminder_id"] = result["source_id"] if result["source"] == "reminders" else None
    return result


def upsert_task(task: dict[str, Any]) -> dict[str, Any]:
    title = str(task.get("title") or task.get("text") or "").strip()
    if not title:
        raise ValueError("任务标题不能为空")
    source = str(task.get("source") or "manual").strip() or "manual"
    source_id = str(task.get("source_id") or "").strip() or None
    due = task.get("due_date_iso")
    due_text = str(due).strip() if due is not None and str(due).strip() else None
    task_id = str(task.get("task_id") or "").strip() or _stable_id(source, source_id, title, due_text)
    status = str(task.get("status") or "pending").strip().lower() or "pending"
    if task.get("completed") is True:
        status = "completed"
    if status not in _VALID_STATUSES:
        raise ValueError("status 只支持 pending/in_progress/completed/cancelled")
    body = str(task.get("body") or "")
    try:
        priority = max(0, min(9, int(task.get("priority") or 0)))
    except (TypeError, ValueError):
        priority = 0
    list_name = str(task.get("list_name") or "").strip() or None
    now = _now()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO tasks (
                task_id, title, status, due_date_iso, body, priority, source,
                source_id, list_name, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(task_id) DO UPDATE SET
                title=excluded.title, status=excluded.status,
                due_date_iso=excluded.due_date_iso, body=excluded.body,
                priority=excluded.priority, source=excluded.source,
                source_id=excluded.source_id, list_name=excluded.list_name,
                updated_at=excluded.updated_at
            """,
            (task_id, title, status, due_text, body, priority, source, source_id, list_name, now, now),
        )
        row = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
    assert row is not None
    return _row_to_dict(row)


def _rows_by_reminder_id(conn: sqlite3.Connection) -> dict[str, list[sqlite3.Row]]:
    rows: dict[str, list[sqlite3.Row]] = {}
    for row in conn.execute(
        "SELECT * FROM tasks WHERE source = 'reminders' AND source_id IS NOT NULL "
        "ORDER BY updated_at DESC"
    ):
        rows.setdefault(row["source_id"], []).append(row)
    return rows


def _append_note(body: str, note: str) -> str:
    return f"{body.rstrip()}\n\n{note}".strip() if note not in body else body


def upsert_reminders(reminders: list[dict[str, Any]]) -> int:
    """把 Reminders 快照并进任务库 (9/26 重做, 之前反复冒重复/已删条目的根因都在这)。

    · 一条提醒只对应一行: 先认正文里的 [catfish-task:ID]; 标记被改备注时抹掉了,
      就认 source_id 相同的已有行。以前这里直接按提醒 ID 新建一行, 结果同一条提醒
      在任务库里两行 (一行新备注, 一行旧备注), 早安页两条都读。
    · 同一条提醒已经有多行 (历史遗留) → 留一行, 其余标 cancelled。
    · 任务库里已取消的不被导入改回 pending —— 取消是员工/小鲶的决定, 提醒还挂着
      只说明没去 Reminders 里删, 不说明事情又活了。提醒勾完成照常记 completed。
    """
    count = 0
    with _connect() as conn:
        by_reminder = _rows_by_reminder_id(conn)
    for reminder in reminders:
        title = str(reminder.get("title") or "").strip()
        if not title:
            continue
        body = str(reminder.get("body") or "")
        reminder_id = str(reminder.get("id") or "").strip() or None
        existing = by_reminder.get(reminder_id or "", [])
        marker = re.search(r"\[catfish-task:([^\]]+)\]", body)
        if marker:
            task_id = marker.group(1)
        elif existing:
            live = [row for row in existing if row["status"] != "cancelled"]
            task_id = (live or existing)[0]["task_id"]
        else:
            task_id = _stable_id("reminders", reminder_id, title, reminder.get("due_date_iso"))
        with _connect() as conn:
            prev = conn.execute("SELECT status FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        status = "completed" if reminder.get("completed") else "pending"
        if prev is not None and prev["status"] == "cancelled" and status == "pending":
            status = "cancelled"
        upsert_task({
            "task_id": task_id,
            "title": title,
            "status": status,
            "due_date_iso": reminder.get("due_date_iso"),
            "body": body,
            "priority": reminder.get("priority"),
            "source": "reminders",
            "source_id": reminder_id,
            "list_name": reminder.get("list_name"),
        })
        for row in existing:
            if row["task_id"] != task_id and row["status"] not in _COMPLETED_STATUSES:
                _close_row(row, "cancelled", f"重复：同一条提醒已由 {task_id} 承载")
        count += 1
    return count


def _close_row(row: sqlite3.Row, status: str, note: str) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE tasks SET status = ?, body = ?, updated_at = ? WHERE task_id = ?",
            (status, _append_note(row["body"], f"（{note}，{datetime.now():%Y-%m-%d} 自动关闭）"),
             _now(), row["task_id"]),
        )
        conn.commit()


def close_deleted_reminders(snapshot: list[dict[str, Any]], *, limit: int) -> list[str]:
    """Reminders 里已经删掉的提醒, 任务库里对应的未完成行一起关掉。

    以前导入只增改不删: 员工在 Reminders 删了一条, 任务库那行永远是 pending,
    早安页每天照读, 删了的待办第二天又冒出来 (9/24「新提醒事项」、9/26 北京福富
    高新那张卡都是这样)。只在拿到完整快照时做: 快照撑满 limit 可能被截断,
    快照为空而库里有未完成提醒多半是读取出了问题, 这两种都不动。
    """
    if len(snapshot) >= limit:
        return []
    seen = {str(item.get("id") or "").strip() for item in snapshot} - {""}
    with _connect() as conn:
        open_rows = [
            row for row in conn.execute(
                "SELECT * FROM tasks WHERE source = 'reminders' AND source_id IS NOT NULL "
                "AND status IN ('pending', 'in_progress')"
            )
        ]
    if not seen and open_rows:
        return []
    closed = []
    for row in open_rows:
        if row["source_id"] not in seen:
            _close_row(row, "cancelled", "Reminders 里已删除")
            closed.append(row["task_id"])
    return closed


def _import_snapshot(snapshot: dict[str, Any], limit: int) -> None:
    items = snapshot.get("reminders", [])
    upsert_reminders(items)
    close_deleted_reminders(items, limit=limit)


def list_tasks(args: dict[str, Any] | None = None, *, now: datetime | None = None) -> dict[str, Any]:
    args = args or {}
    scope = str(args.get("scope") or "active").strip().lower()
    if scope not in _SCOPES:
        return {"ok": False, "error": "scope 只支持 active/today/week/overdue/all", "tasks": []}
    try:
        limit = max(1, min(500, int(args.get("limit", 100))))
    except (TypeError, ValueError):
        return {"ok": False, "error": "limit 应是 1-500 整数", "tasks": []}
    include_completed = bool(args.get("include_completed", False))
    list_name = str(args.get("list_name") or "").strip()
    current = now or datetime.now()
    today_start = datetime.combine(current.date(), day_start.min)
    week_start, next_week = _scope_bounds(current)
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM tasks ORDER BY updated_at DESC").fetchall()
    selected: list[dict[str, Any]] = []
    for row in rows:
        task = _row_to_dict(row)
        if not include_completed and task["status"] in _COMPLETED_STATUSES:
            continue
        if list_name and task["list_name"] != list_name:
            continue
        due = _parse_due(task["due_date_iso"])
        if scope == "today" and (due is None or not today_start <= due < today_start + timedelta(days=1)):
            continue
        if scope == "week":
            due_in_week = due is not None and week_start <= due < next_week
            updated_at = float(task.get("updated_at") or 0)
            updated_this_week = (
                task["source"] != "reminders"
                and week_start.timestamp() <= updated_at < next_week.timestamp()
            )
            # 本周新建/更新的长期行动也要出现在本周清单，不能被未来截止日隐藏。
            if not due_in_week and not updated_this_week:
                continue
        if scope == "overdue" and (due is None or due >= today_start):
            continue
        selected.append(task)
    selected.sort(key=lambda task: (_parse_due(task["due_date_iso"]) is None, _parse_due(task["due_date_iso"]) or datetime.max))
    selected = selected[:limit]
    return {
        "ok": True,
        "scope": scope,
        "list_name": list_name or None,
        "include_completed": include_completed,
        "tasks": selected,
        "count": len(selected),
        "summary": f"📋 本机任务库{scope}共有 {len(selected)} 条符合条件的待办.",
    }


def tool_create_task(args: dict[str, Any]) -> dict[str, Any]:
    """创建或更新一个用户行动；这是任务库的写入口。"""
    title = str(args.get("title") or "").strip()
    if not title:
        return {"ok": False, "error": "title 不能空"}
    due = args.get("due_date_iso")
    if due is not None and str(due).strip() and _parse_due(str(due)) is None:
        return {"ok": False, "error": "due_date_iso 格式错，应是本地 ISO 8601 时间"}
    task_id = str(args.get("task_id") or "").strip() or None
    similar: list[dict[str, Any]] = []
    if task_id is None and not str(args.get("source_id") or "").strip():
        same, similar = _find_existing_by_title(title)
        task_id = same
    try:
        task = upsert_task({
            "task_id": task_id,
            "title": title,
            "status": args.get("status", "pending"),
            "due_date_iso": due,
            "body": args.get("body"),
            "priority": args.get("priority"),
            "source": args.get("source", "conversation"),
            "source_id": args.get("source_id"),
            "list_name": args.get("list_name"),
        })
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    result: dict[str, Any] = {
        "ok": True,
        "task": task,
        "summary": f"✅ 已写入本机任务库：{task['title']}",
    }
    if similar:
        result["possible_duplicates"] = similar
        result["summary"] += (
            "\n⚠️ 任务库里还有标题相近的未完成任务："
            + "；".join(f"{t['title']} (task_id={t['task_id']})" for t in similar)
            + "。如果是同一件事，把刚建的这条用 status=cancelled 关掉，改那条 (带 task_id)。"
        )
    return result


def _title_key(title: str) -> str:
    return re.sub(r"[\s\W_]+", "", title).lower()


def _bigrams(text: str) -> set[str]:
    return {text[i:i + 2] for i in range(len(text) - 1)}


def _find_existing_by_title(title: str) -> tuple[str | None, list[dict[str, Any]]]:
    """没带 task_id 的新建: 标题 (去标点空格) 相同的未完成任务直接复用;
    相近的列出来提醒模型。以前 task_id 由标题哈希生成, 小鲶每换一种说法
    (「高新资质申报跟进」「跟进北京福富高新申报受理/补件反馈」「中电高新申报跟进」)
    就多一条任务, 早安页当成几件事分别出卡片。"""
    key = _title_key(title)
    grams = _bigrams(key)
    with _connect() as conn:
        rows = conn.execute(
            "SELECT task_id, title FROM tasks WHERE status IN ('pending', 'in_progress') "
            "ORDER BY updated_at DESC"
        ).fetchall()
    similar = []
    for row in rows:
        other = _title_key(row["title"])
        if other == key:
            return row["task_id"], []
        other_grams = _bigrams(other)
        if grams and other_grams:
            overlap = len(grams & other_grams) / min(len(grams), len(other_grams))
            if overlap >= 0.4:
                similar.append({"task_id": row["task_id"], "title": row["title"]})
    return None, similar[:5]


def tool_sync_tasks_to_reminders(args: dict[str, Any]) -> dict[str, Any]:
    """把本周任务库行动幂等投影到 macOS Reminders。"""
    if platform.system() != "Darwin":
        return {"ok": False, "error": "任务同步到 Reminders 只 macOS 支持", "created": []}
    from . import reminders  # noqa: PLC0415
    snapshot = reminders.tool_list_reminders(
        {"scope": "all", "include_completed": True, "limit": _SNAPSHOT_LIMIT},
    )
    if not snapshot.get("ok"):
        return {"ok": False, "error": snapshot.get("error") or "读取 Reminders 失败", "created": []}
    current = snapshot.get("reminders", [])
    _import_snapshot(snapshot, _SNAPSHOT_LIMIT)
    existing_markers: set[str] = set()
    for reminder in current:
        marker = re.search(r"\[catfish-task:([^\]]+)\]", str(reminder.get("body") or ""))
        if marker:
            existing_markers.add(marker.group(1))
    tasks_result = list_tasks(args)
    created: list[str] = []
    for task in tasks_result["tasks"]:
        task_id = str(task["task_id"])
        if task["source"] == "reminders" or task_id in existing_markers:
            continue
        marker = f"[catfish-task:{task_id}]"
        body = f"{task['body']}\n\n{marker}".strip()
        result = reminders.tool_create_reminder({
            "title": task["title"],
            "body": body,
            "due_date_iso": task["due_date_iso"],
            "list_name": task["list_name"] or "提醒事项",
            "priority": task["priority"],
        })
        if result.get("ok"):
            created.append(task_id)
    return {
        "ok": True,
        "scope": tasks_result["scope"],
        "created": created,
        "count": len(created),
        "summary": f"✅ 已将 {len(created)} 条任务同步到 Reminders（重复运行不会重复创建）",
    }


# Reminders 导入的时间上限。早安页给每个来源 15s, Companion RPC 30s; 导入
# 超过这个数就放弃这一轮, 用本地库应答并带 sync_warning —— 任务库是真源,
# Reminders 只是投影, 投影慢不该让真源读不出来。
REMINDERS_IMPORT_TIMEOUT_SEC = 12.0

# 两次导入之间的最短间隔。Reminders 的 AppleScript 桥每批属性 ~0.9s/50 条
# (9/10 逐句实测), 全量导一次 ≈ 5.5s, 而 list_tasks 一天被调几十次 (早安页
# 每轮刷新 + 对话里模型调用)。每次读都导 = 每次读都等 5 秒起。改成: 距上次
# 成功导入不到这个间隔就直接读本地库; 早安页一轮刷新 (~80 分钟一次) 最多导一次。
REMINDERS_IMPORT_MIN_INTERVAL_SEC = 5 * 60


def _reminders_import_due(now: float) -> bool:
    last = _meta_get_float(_META_REMINDERS_IMPORTED_AT)
    return last is None or now - last >= REMINDERS_IMPORT_MIN_INTERVAL_SEC


def tool_list_tasks(args: dict[str, Any]) -> dict[str, Any]:
    """读取任务库；macOS 上先把 Reminders 快照导入 (幂等 upsert, 按间隔节流)。

    args.force_sync=true 跳过节流 (员工刚在 Reminders.app 里改了东西, 想马上看到)。
    """
    sync_warning = None
    now = _now()
    if platform.system() == "Darwin" and (bool(args.get("force_sync")) or _reminders_import_due(now)):
        from . import reminders  # noqa: PLC0415
        snapshot = reminders.tool_list_reminders(
            {"scope": "all", "include_completed": True, "limit": _SNAPSHOT_LIMIT},
            timeout_sec=REMINDERS_IMPORT_TIMEOUT_SEC,
        )
        if snapshot.get("ok"):
            _import_snapshot(snapshot, _SNAPSHOT_LIMIT)
            _meta_set(_META_REMINDERS_IMPORTED_AT, repr(now))
        else:
            sync_warning = snapshot.get("error") or "Reminders 导入失败"
    result = list_tasks(args)
    if sync_warning:
        result["sync_warning"] = sync_warning
    return result


__all__ = [
    "list_tasks", "tool_create_task", "tool_list_tasks", "tool_sync_tasks_to_reminders",
    "upsert_reminders", "upsert_task",
]
