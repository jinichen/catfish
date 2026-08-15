"""tasks.jsonl 的落盘与回查。

BL-TASKMGR-SPLIT 8/15: 从 task_manager.py 抽出来 (1133 行超限)。纯搬迁, 逻辑一行未改。
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

from .task_manager_types import Task, _MAX_RESULT_CHARS

logger = logging.getLogger("catfish.tool_bridge.task_manager")

# BL-HERMES013-RED-2 (5/13): jsonl 持久化路径 — 跟 a2a_notifications 同模式
# (CATFISH_HOME/tasks.jsonl, fallback ~/.catfish/tasks.jsonl).
# catfish-gateway tasks_browse.py 读这个文件给 web /api/tasks/me 用.
def _tasks_jsonl_path() -> Path:
    """跟 a2a_notifications._notifications_path() 对齐 — 同根目录."""
    catfish_home = os.environ.get("CATFISH_HOME", "").strip()
    if catfish_home:
        return Path(catfish_home).expanduser() / "tasks.jsonl"
    return Path.home() / ".catfish" / "tasks.jsonl"


def _persist_task_started_to_jsonl(task: Task) -> None:
    """BL-LONG-RUNNING-V1-PHASE-C (6/1): submit 时写 jsonl status=pending row.

    含 payload (retry 用) + kind + label + started_at. 跟 _persist_task_to_jsonl
    写的 completed/failed row 同 schema (后者会再 append 一行同 task_id,
    新 status). 重启扫 jsonl 时: 同 task_id 最后一行是 pending → stuck.
    """
    path = _tasks_jsonl_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "task_id": task.task_id,
        "kind": task.kind,
        "label": task.label,
        "status": "pending",
        "started_at": task.started_at,
        "finished_at": None,
        "elapsed_s": 0.0,
        "error": None,
        "result_preview": "",
        # PHASE-C 关键: payload 跨重启保留供 retry. 跟 task metadata 同密级,
        # ~/.catfish/tasks.jsonl 跟 attachments.db 一样在员工本机.
        "payload": task.payload,
        # P3.5.33 (6/18 鸿波 catch '端后不再重试'): retry 评估 4 字段跨重启保留.
        "retry_count": task.retry_count,
        "max_retries": task.max_retries,
        "last_error_type": task.last_error_type,
        "parent_task_id": task.parent_task_id,
    }
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _persist_task_to_jsonl(task: Task) -> None:
    """append 一行 jsonl 记录任务最终状态. 给 catfish-web Kanban 页用.

    不存 result 全部 (太大, _MAX_RESULT_CHARS=100K), 只存 result 短摘要 + error.
    任务 still running 时不写 (run_wrapper 只在 finally 调本函数, 此时 status 是
    completed/failed 二选一).

    格式跟 a2a_notifications.jsonl 类似, 给 tasks_browse.py 解析:
    ```json
    {
      "task_id": "task_abc12345",
      "kind": "execute_code",
      "label": "修订《资质管理办法》",
      "status": "completed",
      "started_at": 1763061234.5,
      "finished_at": 1763061334.7,
      "elapsed_s": 100.2,
      "error": null,
      "result_preview": "(执行 87 行 Python, 输出 2.3 KB)"
    }
    ```
    """
    path = _tasks_jsonl_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    # result_preview: 取个 200 字摘要给 UI 卡片显示, 不存全部 result
    result_preview = ""
    if task.status == "completed" and task.result is not None:
        try:
            if isinstance(task.result, dict):
                # execute_code 返 {stdout, stderr, returncode, ...}
                stdout = str(task.result.get("stdout", ""))[:150]
                rc = task.result.get("returncode", "?")
                result_preview = f"rc={rc} stdout={stdout!r}"[:200]
            elif isinstance(task.result, str):
                result_preview = task.result[:200]
            else:
                result_preview = str(task.result)[:200]
        except Exception:
            result_preview = "(result 摘要生成失败)"

    record = {
        "task_id": task.task_id,
        "kind": task.kind,
        "label": task.label,
        "status": task.status,
        "started_at": task.started_at,
        "finished_at": task.finished_at,
        "elapsed_s": (
            (task.finished_at or time.time()) - task.started_at
        ),
        "error": task.error,
        "result_preview": result_preview,
        # BL-LONG-RUNNING-V1-PHASE-C (6/1): payload 重复写 (跟 _persist_task_started
        # 那行重复, 冗余但简化 reader — scan 时不用 join 两行就拿到完整 task 信息).
        "payload": task.payload,
        # P3.5.33 (6/18): retry 评估字段写入 final row, retry_task / auto_retry 读这行.
        "retry_count": task.retry_count,
        "max_retries": task.max_retries,
        "last_error_type": task.last_error_type,
        "parent_task_id": task.parent_task_id,
    }
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def find_task_full_record_by_id(task_id: str) -> dict | None:
    """P3.5.33 (6/18 鸿波 catch): 找 task_id 的**最新** jsonl row, 给 retry 评估用.

    跟 find_task_payload_by_id 的区别:
      - find_task_payload_by_id 拿 **first** row (原始 payload), 给 retry 拿原 input
      - find_task_full_record_by_id 拿 **latest** row, 含最新 retry_count / last_error_type / error
        给 retry 评估 (达上限? permanent?) 用. payload 也在 latest 行 (PHASE-C 冗余写, 跟 first 行同).

    设计选择: 评估 gate 必须拿 latest, 不能拿 first — first 总是 retry_count=0,
    评估永远过. 实际想拿到的是"上次 fail 时 task 的 retry_count = N", 下次 retry 算 N+1.
    """
    path = _tasks_jsonl_path()
    if not path.exists():
        return None
    latest = None
    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("task_id") == task_id:
                    latest = rec  # 顺序读, 后写覆盖前
        return latest
    except Exception:
        logger.exception("find_task_full_record_by_id: 读 jsonl 失败")
        return None


def find_task_payload_by_id(task_id: str) -> tuple[str, dict, str] | None:
    """BL-LONG-RUNNING-V1-PHASE-C (6/1): 从 jsonl 找 task_id 的 kind + payload + label.

    给 retry 用 — 重启同一 input 跑新 task. 返 (kind, payload, label) 或 None.
    扫 jsonl 找含 task_id 的**第一条** record (= submit 时写的 pending row, 含
    原始 payload). 后续 completed/interrupted row 也含 payload, 但拿 first row
    确保是原始 input (没被中途改).
    """
    path = _tasks_jsonl_path()
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("task_id") == task_id:
                    kind = rec.get("kind", "")
                    payload = rec.get("payload", {}) or {}
                    label = rec.get("label", "")
                    if kind:
                        return (kind, payload, label)
        return None
    except Exception:
        logger.exception("find_task_payload_by_id: 读 jsonl 失败")
        return None


def read_tasks_from_jsonl(
    hours_back: int | None = 24,
    limit: int = 200,
) -> list[dict]:
    """供 catfish-gateway tasks_browse.py 读历史任务列表.

    Args:
        hours_back: 看过去几小时, None = 全部
        limit: 最多返几条 (从 jsonl 末尾倒序读)

    Returns:
        list[dict] — 跟 _persist_task_to_jsonl 写的 record 同格式
    """
    path = _tasks_jsonl_path()
    if not path.exists():
        return []
    cutoff = (
        time.time() - hours_back * 3600 if hours_back is not None else None
    )
    rows: list[dict] = []
    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue  # 跳坏行, 不致命
                if cutoff is not None and rec.get("started_at", 0) < cutoff:
                    continue
                rows.append(rec)
    except OSError as e:
        logger.warning("read_tasks_from_jsonl: %s 读失败: %s", path, e)
        return []
    # 倒序 (最新在前) + cap limit
    rows.reverse()
    return rows[:limit]
