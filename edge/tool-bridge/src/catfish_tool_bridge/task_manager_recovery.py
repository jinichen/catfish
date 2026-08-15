"""启动时的中断任务恢复 —— 标记 stuck + 自动重试。

BL-TASKMGR-SPLIT 8/15: 从 task_manager.py 抽出来 (1133 行超限)。纯搬迁, 逻辑一行未改。

manager / retry_task / _KIND_AUTO_RETRY 是**在函数体里 import** 的: 它们留在
task_manager.py (manager 因为测试对 _manager 是赋值), 而 task_manager 末尾又要
re-export 本模块 —— 顶层 import 就是循环。
"""
from __future__ import annotations

import json
import logging
import time

from .task_manager_jsonl import _tasks_jsonl_path
from .task_manager_types import Task

logger = logging.getLogger("catfish.tool_bridge.task_manager")

def mark_interrupted_on_startup(stuck_threshold_secs: int = 300) -> int:
    """BL-LONG-RUNNING-V1-PHASE-C (6/1): 启动时扫 jsonl 找 stuck task.

    场景: tool-bridge / hermes 进程跑 long task 时被 kill / 系统重启 / oom,
    task 永远停在 status=running, in-memory dict 重启丢. Companion TasksCard
    显"运行中" 但实际进程死了.

    实现:
      1. scan jsonl, 按 task_id group, 取每个 task_id 最后一条 record
      2. 如果最后一条 status=pending (= submit 时写的) 且 started_at 早于
         now - stuck_threshold_secs (default 5 分钟) → stuck
      3. append 一行 status=interrupted, error="进程重启/崩溃, 未完成"

    返清了几条. caller 一般是 manager() 单例首次 init 时调一次.
    """
    path = _tasks_jsonl_path()
    if not path.exists():
        return 0
    # 读全部, 按 task_id group 找 latest record
    latest_by_task: dict[str, dict] = {}
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
                tid = rec.get("task_id")
                if not tid:
                    continue
                latest_by_task[tid] = rec  # 顺序读, 后写覆盖前
    except Exception:
        logger.exception("mark_interrupted: 读 jsonl 失败")
        return 0

    now = time.time()
    interrupted_count = 0
    for tid, rec in latest_by_task.items():
        if rec.get("status") != "pending":
            continue  # 已经 completed / failed / interrupted, 跳过
        started_at = rec.get("started_at", 0)
        if not isinstance(started_at, (int, float)):
            continue
        age = now - started_at
        if age < stuck_threshold_secs:
            continue  # 还不算 stuck, 给当前进程一个机会
        # 写 interrupted row
        # P3.5.33 (6/18): 进程崩归类 transient (重跑等价初次), 加 retry 评估字段
        # 让 auto_retry_interrupted_on_startup 拿到完整 context.
        interrupted_record = {
            "task_id": tid,
            "kind": rec.get("kind", ""),
            "label": rec.get("label", ""),
            "status": "interrupted",
            "started_at": started_at,
            "finished_at": now,
            "elapsed_s": age,
            "error": f"进程重启/崩溃, 未完成 (stuck {age:.0f}s)",
            "result_preview": "",
            "payload": rec.get("payload", {}),
            # P3.5.33: retry 评估字段 — 进程崩属 transient (重跑同 payload 应该可恢复)
            "retry_count": rec.get("retry_count", 0),
            "max_retries": rec.get("max_retries", 3),
            "last_error_type": "transient",
            "parent_task_id": rec.get("parent_task_id"),
        }
        try:
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(interrupted_record, ensure_ascii=False) + "\n")
            interrupted_count += 1
        except Exception:
            logger.warning("mark_interrupted: append failed for %s", tid, exc_info=True)
    if interrupted_count:
        logger.info(
            "mark_interrupted_on_startup: 标记 %d 个 stuck task interrupted",
            interrupted_count,
        )
    return interrupted_count


# P3.5.33: reentrancy guard. retry_task → submit_typed_task → manager() lazy init
# 时, 若 _manager 还没赋值, 会再次调本函数, 触发重入. 生产路径下 manager() 先
# _manager 赋值再调 auto_retry, 内部 retry_task 调 manager() 不重入. 但 test 路径
# 或将来 caller 直接调本函数时 _manager=None 会触发. guard 是防御深度.
_auto_retry_in_progress: bool = False


def auto_retry_interrupted_on_startup() -> int:
    """P3.5.33 (6/18 鸿波 catch '端后不再重试'): 启动时自动 retry 符合条件的 interrupted task.

    紧跟 mark_interrupted_on_startup 调用, 修-1 (评估) + 修-2 (自动 supervisor) 捆绑.

    评估规则 (全过才 retry):
      1. latest row 是 interrupted (mark_interrupted 标的)
      2. kind 在 _KIND_AUTO_RETRY 白名单 (默认只 execute_code, 有副作用的 kind 不进白名单)
      3. retry_count < max_retries (没达上限)
      4. last_error_type != 'permanent' (interrupted 默认 transient, 但留 hook 兜底)

    幂等设计: retry_task 启新 task (新 task_id), 写新的 pending row. 原 task 的
    interrupted row 不动. 下次启动再扫不会再 retry 同一原 task — 因为它的 latest
    row 是 pending (新 task_id) 或 completed/failed (新 task 跑完后). 老 interrupted
    被新 task_id 链路接走.

    返实际触发的次数. 失败 swallow (跟 mark_interrupted 同优先级).
    """
    global _auto_retry_in_progress
    if _auto_retry_in_progress:
        # 重入 (例 retry_task → submit_typed_task → manager() lazy init → 又调本函数).
        # 直接返 0, 让外层完成后由外层 counter 统计.
        return 0
    _auto_retry_in_progress = True
    try:
        return _auto_retry_interrupted_on_startup_inner()
    finally:
        _auto_retry_in_progress = False


def _auto_retry_interrupted_on_startup_inner() -> int:
    """实际 auto_retry 逻辑, 不带 guard. 给 auto_retry_interrupted_on_startup 包."""
    # 延迟 import —— 见文件头 (task_manager 末尾要 re-export 本模块, 顶层就是循环)
    from .task_manager import _KIND_AUTO_RETRY, retry_task  # noqa: PLC0415

    path = _tasks_jsonl_path()
    if not path.exists():
        return 0

    # 跟 mark_interrupted 一样, 按 task_id group 拿 latest
    latest_by_task: dict[str, dict] = {}
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
                tid = rec.get("task_id")
                if tid:
                    latest_by_task[tid] = rec
    except Exception:
        logger.exception("auto_retry_interrupted_on_startup: 读 jsonl 失败")
        return 0

    auto_retried = 0
    for tid, rec in latest_by_task.items():
        if rec.get("status") != "interrupted":
            continue
        kind = rec.get("kind", "")
        if kind not in _KIND_AUTO_RETRY:
            logger.info(
                "auto_retry skip %s: kind=%s 不在白名单 (_KIND_AUTO_RETRY)",
                tid, kind,
            )
            continue
        retry_count = rec.get("retry_count", 0)
        max_retries = rec.get("max_retries", 3)
        if retry_count >= max_retries:
            logger.info(
                "auto_retry skip %s: 达上限 %d/%d", tid, retry_count, max_retries,
            )
            continue
        last_err_type = rec.get("last_error_type", "unknown")
        if last_err_type == "permanent":
            logger.info(
                "auto_retry skip %s: last_error_type=permanent", tid,
            )
            continue
        # 评估全过 → 触发 retry. retry_task 自己再过一遍评估 gate (防御深度).
        try:
            result = retry_task({"task_id": tid})
            if result.get("ok"):
                auto_retried += 1
                logger.info(
                    "auto_retry: %s (第 %d 次) → 新 %s (kind=%s)",
                    tid, retry_count + 1, result.get("task_id"), kind,
                )
            else:
                # retry_task 拒了 (race / 评估 gate 二次过). 不强行, log 完跳过.
                logger.warning(
                    "auto_retry %s: retry_task 拒: %s",
                    tid, result.get("error"),
                )
        except Exception:
            logger.exception("auto_retry %s: 异常", tid)

    if auto_retried:
        logger.info(
            "auto_retry_interrupted_on_startup: 实际触发 %d 次 retry", auto_retried,
        )
    return auto_retried
