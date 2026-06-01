"""
task_manager.py — BL-A2.1 (5/8 ship): chat 非阻塞 + 长任务后台跑.

# 为啥这模块

5/7 鸿波: "鲶鱼在跑修订 docx 任务时, chat 锁住不能干别的, 不是 Agent."

真 Agent 应该:
1. 长任务 (写 docx / 多步流程) **后台跑**, 立即返 task_id
2. 员工**继续输入**新消息, 走另一个 LLM 调用
3. 老任务**完成时通知** 员工

# 三个工具

catfish_run_task(task_kind, payload, label?)
    启动后台任务. 立即返 task_id, 不等任务完成.

catfish_task_status(task_id)
    查任务状态: pending / running / completed / failed.

catfish_task_result(task_id)
    取任务结果. 已完成返 result, 还在跑返 status=running.

# 任务持久化策略

第一阶段 (5/8 ship): in-memory dict, 进程死则丢
第二阶段 (5/22 后): SQLite ~/.catfish/tasks.db

第一阶段对 demo 够 — Companion 长跑期间任务在 process 内有效.

# 任务 kind (枚举, 不让 LLM 任意填)

- "execute_code": 跑一段 python (走沙箱). payload={"code": "...", "lang": "python"}
- "edit_docx":   修订 docx 文件 (read → edit → save). payload={"path": "...", "edits": [...]}
- "write_docx":  从零写 docx. payload={"path": "...", "title": "...", "sections": [...]}
- "shell_pipeline": 多步 shell. payload={"steps": [...]}

简化: 5/8 先 ship execute_code 一个 kind, 其他 5/22 后扩.

# 测试

10 单测 (tests/test_task_manager.py):
    - 启动任务返 task_id
    - 查 pending → 立即变 running
    - 简单任务执行完变 completed + result
    - 失败任务变 failed + error
    - 不存在 task_id 查询返 not_found
    - 多任务并发 (10 个) 都能跑
    - status 不可序列化的 result 用 repr 兜底
    - 大输出截断 (max_chars)
    - cancel task (5/22 后, 暂占位)
    - cleanup 老任务 (24h 自动删)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import json
import os
import secrets
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

logger = logging.getLogger("catfish.tool_bridge.task_manager")

# 任务输出最大字符数 (防 LLM 拿到天量结果)
_MAX_RESULT_CHARS = 100_000

# 老任务自动清理时间 (秒). 完成 24h 后从 store 移除.
_TASK_TTL_SECONDS = 24 * 3600


@dataclass
class Task:
    """单个 background task 的状态记录."""
    task_id: str
    kind: str
    label: str  # 给员工看的人类可读描述
    status: str = "pending"  # pending / running / completed / failed / interrupted
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    result: Any = None
    error: str | None = None
    # BL-LONG-RUNNING-V1-PHASE-C (6/1): payload 跨重启保留, retry 时拿来重启同样
    # input. 不暴露给 LLM (status_dict 不带), jsonl 持久化 (本机, 跟 task metadata
    # 同密级).
    payload: dict = field(default_factory=dict, repr=False)
    # 内部 asyncio task 引用, 不序列化给 LLM
    _async_task: asyncio.Task | None = field(default=None, repr=False)


class TaskManager:
    """In-memory background task store + runner.

    单例 (5/8 in-memory, 5/22 后改 SQLite 持久化).
    """

    def __init__(self) -> None:
        self._tasks: dict[str, Task] = {}

    def _new_task_id(self) -> str:
        """生成 8 位 hex task_id (跟 hermes 风格一致)."""
        return f"task_{secrets.token_hex(4)}"

    def submit(
        self,
        kind: str,
        label: str,
        runner: Callable[[], Awaitable[Any]],
        *,
        payload: dict | None = None,
    ) -> Task:
        """启动后台任务, 立即返 Task (status=pending → running 异步).

        Args:
            kind: 任务类型枚举
            label: 给员工看的描述 (例 "修订《资质管理办法》")
            runner: 真正跑任务的 async coroutine factory
            payload: BL-LONG-RUNNING-V1-PHASE-C (6/1) — 保存原 input 供 retry.
                     用 submit_typed_task 入口时这个会被自动传入. 直接调 submit()
                     的老 caller 不传时默认空 dict (能 work, 但 retry 失效).

        Returns:
            Task 对象 (含 task_id 等)
        """
        task_id = self._new_task_id()
        task = Task(
            task_id=task_id, kind=kind, label=label,
            payload=payload or {},
        )
        self._tasks[task_id] = task

        # BL-LONG-RUNNING-V1-PHASE-C (6/1): submit 时立即写 jsonl status=pending
        # row, 含 payload. 这样进程崩了重启扫 jsonl 能发现哪些 task stuck (有
        # pending 没对应 completed/failed), 标 interrupted; 也给 retry 工具拿
        # 到原 input. 失败 swallow (jsonl 是非关键路径).
        try:
            _persist_task_started_to_jsonl(task)
        except Exception:
            logger.warning(
                "task jsonl started 写失败 (非关键): id=%s",
                task_id, exc_info=True,
            )

        async def _run_wrapper():
            task.status = "running"
            try:
                result = await runner()
                # 截断超大输出
                task.result = self._truncate_result(result)
                task.status = "completed"
                logger.info(
                    "task done: id=%s kind=%s elapsed=%.1fs",
                    task_id, kind, time.time() - task.started_at,
                )
            except Exception as e:
                logger.exception("task failed: id=%s kind=%s", task_id, kind)
                task.error = f"{type(e).__name__}: {e}"
                task.status = "failed"
            finally:
                task.finished_at = time.time()
                # BL-A2.3: 任务完成通知 (macOS 通知 + 桌宠 bubble queue).
                # 通知失败不阻塞任务结果记录, swallow exception.
                try:
                    _notify_task_done(task)
                except Exception:
                    logger.warning(
                        "task done 通知失败 (无关键路径): id=%s",
                        task_id, exc_info=True,
                    )
                # BL-HERMES013-RED-2 (5/13): jsonl 持久化 — 写一行让重启后
                # catfish-web Kanban 页面看得到. in-memory dict 重启就丢, 但
                # jsonl 跨进程跨重启活. 不存 result (太大), 只存元数据 + error.
                # 失败不阻塞 (swallow), 跟通知同优先级 (非关键路径).
                try:
                    _persist_task_to_jsonl(task)
                except Exception:
                    logger.warning(
                        "task jsonl 持久化失败 (无关键路径): id=%s",
                        task_id, exc_info=True,
                    )

        # BL-LONG-RUNNING-V1-FIX (5/31): tool-bridge call_tool 入口是 sync,
        # 没 running event loop, 老代码直接标 failed → 用户调 catfish_run_task
        # 立刻报 "no event loop". 修: detect 后 spawn 专线程跑 asyncio.run().
        # async caller (本来就有 loop, 例 hermes streaming) 走原 create_task 分支.
        #
        # WARN 修: 先建 coroutine 拿引用, async 路径成功就 await, 失败时显式
        # close() 防 "coroutine was never awaited" RuntimeWarning.
        coro = _run_wrapper()
        try:
            task._async_task = asyncio.create_task(coro)
        except RuntimeError:
            # 显式回收原 coroutine 防 RuntimeWarning, 然后在 thread 里 new 一个.
            coro.close()
            # 没 running loop — 起一个 daemon thread 跑自己的 loop.
            # daemon=True 让进程退出时不卡 (任务半截死可接受, jsonl 已记 pending).
            def _thread_target() -> None:
                try:
                    asyncio.run(_run_wrapper())
                except Exception:  # noqa: BLE001
                    logger.exception(
                        "submit-via-thread: asyncio.run 内部异常: task_id=%s",
                        task_id,
                    )
            t = threading.Thread(
                target=_thread_target,
                name=f"catfish-task-{task_id}",
                daemon=True,
            )
            t.start()
            # _async_task 留空, 别处 (cancel) 已经处理 None 兜底.
            logger.info(
                "submit: 无 running loop → spawn daemon thread tid=%s for task_id=%s",
                t.ident, task_id,
            )
        return task

    def get(self, task_id: str) -> Task | None:
        return self._tasks.get(task_id)

    def status_dict(self, task_id: str) -> dict:
        """返给 LLM 看的 status (剥掉 _async_task)."""
        task = self.get(task_id)
        if task is None:
            return {"task_id": task_id, "status": "not_found"}
        return {
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
        }

    def result_dict(self, task_id: str) -> dict:
        """返完整结果, 含 result / error."""
        task = self.get(task_id)
        if task is None:
            return {"task_id": task_id, "status": "not_found"}
        d = self.status_dict(task_id)
        if task.status == "completed":
            d["result"] = task.result
        elif task.status == "failed":
            d["error"] = task.error
        return d

    def list_active(self) -> list[dict]:
        """列当前所有 active (pending/running/recently completed) 任务."""
        return [self.status_dict(t.task_id) for t in self._tasks.values()]

    def cleanup_expired(self) -> int:
        """清 24h 之前完成的任务. 返清了几条."""
        now = time.time()
        expired: list[str] = []
        for tid, t in self._tasks.items():
            if t.finished_at and (now - t.finished_at) > _TASK_TTL_SECONDS:
                expired.append(tid)
        for tid in expired:
            del self._tasks[tid]
        if expired:
            logger.info("cleaned %d expired tasks", len(expired))
        return len(expired)

    @staticmethod
    def _truncate_result(result: Any) -> Any:
        """超大 result 截断, 防 LLM 拿到天量字符串."""
        if isinstance(result, str) and len(result) > _MAX_RESULT_CHARS:
            return result[:_MAX_RESULT_CHARS] + f"\n[... 截断 {len(result) - _MAX_RESULT_CHARS} 字]"
        if isinstance(result, dict):
            # dict 里的 stdout/stderr 字段截断
            new_d = dict(result)
            for key in ("stdout", "stderr", "output"):
                v = new_d.get(key)
                if isinstance(v, str) and len(v) > _MAX_RESULT_CHARS:
                    new_d[key] = v[:_MAX_RESULT_CHARS] + f"\n[... 截断 {len(v) - _MAX_RESULT_CHARS} 字]"
            return new_d
        return result


# 单例
_manager: TaskManager | None = None


def manager() -> TaskManager:
    """全局 task manager (lazy).

    BL-LONG-RUNNING-V1-PHASE-C (6/1): 首次 init 时扫 jsonl 找 stuck task 标
    interrupted. 进程重启后, Companion 看到的 stuck task 立刻翻 "中断" 状态.
    幂等: 已经 interrupted 的 task 不重复标. 不阻塞 manager 初始化 (有异常 swallow).
    """
    global _manager
    if _manager is None:
        _manager = TaskManager()
        try:
            n = mark_interrupted_on_startup()
            if n:
                logger.info("manager init: 启动扫到 %d 个 stuck task 已标 interrupted", n)
        except Exception:
            logger.warning("manager init: mark_interrupted 失败 (非关键)", exc_info=True)
    return _manager


# ============================================================
# 任务 kind 注册表 — 5/8 先 ship execute_code 一个
# ============================================================

async def _runner_execute_code(payload: dict) -> dict:
    """跑 execute_code 任务 (走 sandbox)."""
    from . import sandbox  # noqa: PLC0415  lazy
    code = payload.get("code") or ""
    lang = payload.get("lang") or "python"
    timeout_s = int(payload.get("timeout_s") or 60)
    return sandbox.run_in_sandbox(code, lang=lang, timeout_s=timeout_s)


_KIND_RUNNERS: dict[str, Callable[[dict], Awaitable[dict]]] = {
    "execute_code": _runner_execute_code,
}


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
    }
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


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


#: BL-E27.4 (5/8): macOS 通知去重 — 同 label 1 小时内已发过 → 不重复发.
#: 跨 _notify_task_done 调用的 in-memory 状态, 进程死则丢. dict[label, last_notify_ts].
_RECENT_NOTIFY_BY_LABEL: dict[str, float] = {}
_NOTIFY_DEDUP_WINDOW_S = 3600  # 1 小时
_MIN_NOTIFY_ELAPSED_FOR_SUCCESS = 30.0  # 成功任务 ≥ 30s 才发系统通知


def _is_test_task(task: Task) -> bool:
    """识别 demo / 测试任务 — 避免 BL-A2 测试时反复跑导致通知中心刷屏."""
    label = (task.label or "").lower()
    kind = (task.kind or "").lower()
    return (
        "_test" in kind
        or "test_" in kind
        or kind == "test"
        or "测试" in (task.label or "")
        or label.startswith("test ")
    )


def _should_send_macos_notify(task: Task, elapsed: float) -> bool:
    """BL-E27.4 收紧规则 — 啥时候真发 macOS 通知.

    失败 → 始终发 (除非测试任务).
    成功 → 仅 ≥ 30 秒, 不是测试任务, 同 label 1h 内没发过.
    """
    if _is_test_task(task):
        return False
    label_key = task.label or task.kind or ""
    now = time.time()
    last = _RECENT_NOTIFY_BY_LABEL.get(label_key, 0.0)
    if now - last < _NOTIFY_DEDUP_WINDOW_S:
        return False  # 同 label 去重
    if task.status == "failed":
        # 失败始终通知 (除非测试任务, 已上面拦)
        _RECENT_NOTIFY_BY_LABEL[label_key] = now
        return True
    if task.status == "completed":
        # 成功仅长任务通知
        if elapsed >= _MIN_NOTIFY_ELAPSED_FOR_SUCCESS:
            _RECENT_NOTIFY_BY_LABEL[label_key] = now
            return True
    return False


def _notify_task_done(task: Task) -> None:
    """BL-A2.3 + BL-E27.4 (5/8 重写): 任务完成 双通道通知.

    通道 1: macOS osascript 通知 — 收紧规则:
      - 失败 → 始终发
      - 成功 → ≥ 30s 才发
      - 测试任务过滤 ('_test' / '测试')
      - 同 label 1h 内去重 (防 demo 反复跑刷屏 — 鸿波 5/8 凌晨抱怨)
      - env CATFISH_TASK_NOTIFY=0 一键关

    通道 2: 桌宠 bubble + 状态着色 (BL-E27.4 主通道):
      - 写 ~/.catfish/pet_pending_bubbles.jsonl, 桌宠 polling 读
      - 桌宠头部颜色 indicator (services/pet_status.rs 聚合)
      - 单击桌宠 → 打开 Companion 看详情, 同时清 unseen 标记
      - 比 macOS 通知中心累积一周直观

    设计哲学 (5/8 鸿波): 鲶鱼是同事不是工具, 同事不会每件小事打断你 —
    桌宠颜色低打扰是主, macOS 通知降级到"出错 / 长任务" 兜底.
    """
    if task.finished_at is None or task.started_at is None:
        return
    elapsed = task.finished_at - task.started_at

    if task.status == "completed":
        title = "鲶鱼 · 任务完成"
        msg = f"{task.label or task.kind} 完成 ({elapsed:.0f}s)"
    elif task.status == "failed":
        title = "鲶鱼 · 任务失败"
        msg = f"{task.label or task.kind} 失败: {(task.error or '')[:80]}"
    else:
        return  # other states 不通知

    # === 通道 1: macOS osascript 通知 (BL-E27.4 收紧) ===
    if (
        os.environ.get("CATFISH_TASK_NOTIFY", "1") != "0"
        and _platform_is_macos()
        and _should_send_macos_notify(task, elapsed)
    ):
        try:
            import subprocess
            safe_title = title.replace('"', "'")
            safe_msg = msg.replace('"', "'")
            script = f'display notification "{safe_msg}" with title "{safe_title}"'
            subprocess.Popen(
                ["osascript", "-e", script],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            logger.warning("macOS osascript 通知失败", exc_info=True)

    # 通道 2: 桌宠 bubble queue (写 ~/.catfish/pet_pending_bubbles.jsonl)
    # Companion pet.tsx 端 polling 读这个文件 + 触发 pet 冒泡
    try:
        catfish_dir = Path(os.environ.get("HOME") or ".") / ".catfish"
        catfish_dir.mkdir(parents=True, exist_ok=True)
        bubble_file = catfish_dir / "pet_pending_bubbles.jsonl"
        bubble_text = (
            f"{task.label or task.kind} 做完了" if task.status == "completed"
            else f"{task.label or task.kind} 没做成"
        )
        bubble = {
            "ts": time.time(),
            "kind": "task_done",
            "task_id": task.task_id,
            "task_status": task.status,
            "text": bubble_text,
        }
        with bubble_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(bubble, ensure_ascii=False) + "\n")
    except Exception:
        logger.warning("写 pet_pending_bubbles.jsonl 失败", exc_info=True)


def _platform_is_macos() -> bool:
    """检测是否 macOS (osascript 仅 macOS)."""
    import platform as _p
    return _p.system() == "Darwin"


def submit_typed_task(kind: str, payload: dict, label: str = "") -> dict:
    """工具调用入口: 按 kind 选 runner, 启 task, 返 status_dict.

    BL-LONG-RUNNING-V1-PHASE-C (6/1): payload 透传给 manager.submit() 保存,
    用于 retry. 老 caller 行为不变 (payload 不返给 LLM).
    """
    runner_factory = _KIND_RUNNERS.get(kind)
    if runner_factory is None:
        return {
            "ok": False,
            "error": f"未知任务 kind: {kind!r}, 支持: {list(_KIND_RUNNERS.keys())}",
        }

    async def _bound_runner():
        return await runner_factory(payload)

    task = manager().submit(
        kind=kind,
        label=label or f"{kind} task",
        runner=_bound_runner,
        payload=payload,  # PHASE-C: 保存供 retry
    )
    return {
        "ok": True,
        "task_id": task.task_id,
        "status": task.status,
        "label": task.label,
    }


def retry_task(args: dict) -> dict:
    """BL-LONG-RUNNING-V1-PHASE-C (6/1): retry 中断或失败的任务.

    入参: {"task_id": "task_xxx"}
    行为:
      1. 从 jsonl 找原 task_id 的 kind + payload + label
      2. 找不到 → ok=False
      3. 启一个新 task (新 task_id), 拿原 payload 当 input
      4. 返新 task_id 给 caller (LLM 拿去 catfish_task_status 跟踪)

    设计选择: 新 task_id 不复用原 id. 防 jsonl 状态混乱 (原 id 已有 pending/
    interrupted row, 新 id 干净). 关联通过 result_preview 描述 "retry of
    task_xxx" — 不在 schema 加 retry_of 字段 (避免 schema 蔓延).
    """
    task_id = (args.get("task_id") or "").strip()
    if not task_id:
        return {"ok": False, "error": "缺 task_id"}

    found = find_task_payload_by_id(task_id)
    if found is None:
        return {
            "ok": False,
            "error": f"找不到 task_id={task_id!r} (jsonl 没记录 / payload 字段缺 — "
                     f"task 是 5/8 ~ 6/1 老 schema 的, 无 retry 支持)",
        }
    kind, payload, label = found

    new_label = f"重试 {label}" if label else f"retry of {task_id}"
    result = submit_typed_task(kind=kind, payload=payload, label=new_label)
    if result.get("ok"):
        logger.info(
            "retry_task: 原 %s (kind=%s) → 新 %s",
            task_id, kind, result.get("task_id"),
        )
        result["original_task_id"] = task_id
    return result
