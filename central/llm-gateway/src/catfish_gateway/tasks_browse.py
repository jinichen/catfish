"""BL-HERMES013-RED-2 (5/13 鸿波拍板) — 单员工 Kanban 数据源聚合.

# 职责

把员工本地三类"任务" 聚合成统一 TaskCard 列表给 catfish-web /api/tasks/me:

| source        | 数据文件                              | 写者                        | 含义              |
|---------------|--------------------------------------|----------------------------|-------------------|
| `background`  | ~/.catfish/tasks.jsonl               | tool-bridge task_manager    | catfish_run_task 后台任务 |
| `a2a_inbox`   | ~/.catfish/a2a_notifications.jsonl   | gateway a2a_journal_hook    | 别人来求助 (我作为 expert 答的) |

# scope 取舍 (5/13 22:35 鸿波拍板)

跟 hermes 0.13 Multi-Agent Kanban (durable + heartbeat + reclaim + zombie detection)
**不是一回事**. hermes 那个是 hermes 内部多 agent 任务编排. 我们这个是 catfish 用户
角度的"我手头有什么事在跑/做完了". 5/15-5/18 真正接 hermes Kanban API 时把它
当一个 tile 嵌进去.

# scope 1 限制

- 单员工 ✓ (跨员工要 task_manager 持久化到中心 DB, 5/22 后做)
- 单设备 ✓ (jsonl 在本机 ~/.catfish/, 不跨设备)
- 跑中状态弱 (task_manager status running 只在 in-memory, jsonl 只在 finally 写
  最终态. 重启后 in-memory 丢, "跑中" 这一列只能看当前进程内 task_manager.list_active())

# 5/23-5/25 RED-2-PG 改造 (任务 #57, jsonl → PG 单一 source of truth)

5/14 0:15 鸿波拍板 "PG 上线后 jsonl 删" — 这个 tasks_browse.py 模块 5/25 后大改:

- 删 _read_jsonl_lines / _tasks_jsonl_path 相关逻辑
- 改 list_my_tasks 走 PG (asyncpg query, JOIN quota_users + departments)
- 加 list_dept_tasks (manager 看本部门, admin 看全公司, RBAC join 天然)
- a2a_inbox 聚合保留 (a2a 现状是 jsonl, 5/25 不动 a2a 路径)
- KanbanPage 加视图 toggle "我的 / 本部门 / 全公司" (按 me.role 显示)

trade-off (拍板接受): 中央 PG fail / 没 VPN → 看不到本机任务. 单一 source of truth
优先于双写一致性问题.
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import UTC
from pathlib import Path
from typing import Any

logger = logging.getLogger("catfish.gateway.tasks_browse")


def _tasks_jsonl_path() -> Path:
    """跟 task_manager._tasks_jsonl_path() 对齐 (gateway / tool-bridge 同根)."""
    catfish_home = os.environ.get("CATFISH_HOME", "").strip()
    if catfish_home:
        return Path(catfish_home).expanduser() / "tasks.jsonl"
    return Path.home() / ".catfish" / "tasks.jsonl"


def _a2a_notifications_path() -> Path:
    """跟 a2a_journal_hook / a2a_notifications 同源 (BL-FED2.6)."""
    catfish_home = os.environ.get("CATFISH_HOME", "").strip()
    if catfish_home:
        return Path(catfish_home).expanduser() / "a2a_notifications.jsonl"
    return Path.home() / ".catfish" / "a2a_notifications.jsonl"


# 统一 TaskCard 状态枚举 (UI Kanban 5 列)
STATUSES = ("pending", "running", "waiting", "completed", "failed")


def _epoch_to_iso(ts: float | None) -> str | None:
    if ts is None:
        return None
    try:
        from datetime import datetime
        return datetime.fromtimestamp(ts, tz=UTC).isoformat()
    except (TypeError, ValueError):
        return None


def _read_jsonl_lines(path: Path) -> list[dict]:
    """通用 jsonl 读取, 跳坏行不致命."""
    if not path.exists():
        return []
    rows: list[dict] = []
    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError as e:
        logger.warning("tasks_browse: 读 %s 失败: %s", path, e)
        return []
    return rows


def _background_to_card(rec: dict) -> dict[str, Any]:
    """tool-bridge tasks.jsonl 单行 → 统一 TaskCard."""
    status = rec.get("status", "pending")
    if status not in STATUSES:
        status = "pending"
    return {
        "id": rec.get("task_id", "(unknown)"),
        "source": "background",
        "kind": rec.get("kind", "(unknown)"),
        "title": rec.get("label") or rec.get("task_id", "(无标题)"),
        "status": status,
        "started_at": _epoch_to_iso(rec.get("started_at")),
        "finished_at": _epoch_to_iso(rec.get("finished_at")),
        "elapsed_s": rec.get("elapsed_s"),
        "error": rec.get("error"),
        "preview": rec.get("result_preview", ""),
    }


def _a2a_to_card(rec: dict) -> dict[str, Any]:
    """a2a_notifications.jsonl 单行 → 统一 TaskCard.

    a2a 行格式 (跟 a2a_notifications._parse_ts 同):
    {"ts":"2026-05-12T22:30:15","from_sub":"alice@ffcs.cn",
     "question":"资质审核怎么搞?", "purpose":"expert_consult:资质审核",
     "answer_preview":"走 OA 工单...", "chunks_count":5,
     "duration_ms":900, "seen":false}

    映射:
    - source = "a2a_inbox"
    - title = question (短问题)
    - kind = purpose 头部 (expert_consult / 等)
    - status: 已答 (有 answer_preview) → completed; 未答 → waiting
    - started_at = ts (问的时间)
    - finished_at = ts (a2a 已答的话同时刻, 因为 jsonl 写于答完)
    """
    has_answer = bool(rec.get("answer_preview"))
    status = "completed" if has_answer else "waiting"
    purpose = rec.get("purpose", "")
    kind = purpose.split(":", 1)[0] if purpose else "a2a"
    ts = rec.get("ts")  # 已是 ISO 8601 字符串 (gateway a2a_journal_hook 写)
    return {
        "id": f"a2a:{rec.get('from_sub','?')}:{ts}",
        "source": "a2a_inbox",
        "kind": kind,
        "title": rec.get("question", "(无问题)"),
        "status": status,
        "started_at": ts,
        "finished_at": ts if has_answer else None,
        "elapsed_s": (rec.get("duration_ms", 0) / 1000.0) if has_answer else None,
        "error": None,
        "preview": rec.get("answer_preview", "") or f"来自 {rec.get('from_sub','?')}",
        # a2a 特有元数据 (UI 卡片可显示头像)
        "from_sub": rec.get("from_sub"),
        "purpose": purpose,
    }


def _epoch_from_iso_or_float(v: Any) -> float:
    """sort key — 优先用 ISO 字符串 parse, fallback 0."""
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            from datetime import datetime
            # 兼容 'Z' 后缀和无时区
            s = v.rstrip("Z")
            return datetime.fromisoformat(s).timestamp()
        except (TypeError, ValueError):
            return 0.0
    return 0.0


def list_my_tasks(
    hours_back: int | None = 48,
    limit: int = 200,
    sources: list[str] | None = None,
) -> list[dict[str, Any]]:
    """列员工自己本地的任务 (聚合 background + a2a_inbox).

    Args:
        hours_back: 看过去几小时, None = 全部. 默认 48 (周一/周五跨天).
        limit: 最多返几张卡 (按 started_at 倒序).
        sources: 过滤 source ['background', 'a2a_inbox'], None = 全要.

    Returns:
        list[TaskCard dict] — 倒序 (最新在前).
    """
    sources_set = set(sources) if sources else set(("background", "a2a_inbox"))
    cards: list[dict[str, Any]] = []

    # background tasks
    if "background" in sources_set:
        bg_rows = _read_jsonl_lines(_tasks_jsonl_path())
        for rec in bg_rows:
            try:
                cards.append(_background_to_card(rec))
            except Exception:
                logger.debug("tasks_browse: 跳坏 background 行: %r", rec)

    # a2a inbox
    if "a2a_inbox" in sources_set:
        a2a_rows = _read_jsonl_lines(_a2a_notifications_path())
        for rec in a2a_rows:
            try:
                cards.append(_a2a_to_card(rec))
            except Exception:
                logger.debug("tasks_browse: 跳坏 a2a 行: %r", rec)

    # 时间过滤 (cutoff 用 epoch, 卡的 started_at 是 ISO → 转 epoch 比较)
    if hours_back is not None:
        cutoff = time.time() - hours_back * 3600
        cards = [
            c for c in cards
            if _epoch_from_iso_or_float(c.get("started_at")) >= cutoff
        ]

    # 倒序 (最新在前) + cap limit
    cards.sort(key=lambda c: _epoch_from_iso_or_float(c.get("started_at")), reverse=True)
    return cards[:limit]


def status_summary(cards: list[dict[str, Any]]) -> dict[str, int]:
    """统计每个 status 的卡片数 — 给 UI 顶部 badge / 5 列标题用."""
    counts = {s: 0 for s in STATUSES}
    for c in cards:
        s = c.get("status", "pending")
        if s in counts:
            counts[s] += 1
    return counts


__all__ = [
    "list_my_tasks",
    "status_summary",
    "STATUSES",
]
