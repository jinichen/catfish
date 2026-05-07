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
import secrets
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
    status: str = "pending"  # pending / running / completed / failed
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    result: Any = None
    error: str | None = None
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
    ) -> Task:
        """启动后台任务, 立即返 Task (status=pending → running 异步).

        Args:
            kind: 任务类型枚举
            label: 给员工看的描述 (例 "修订《资质管理办法》")
            runner: 真正跑任务的 async coroutine factory

        Returns:
            Task 对象 (含 task_id 等)
        """
        task_id = self._new_task_id()
        task = Task(task_id=task_id, kind=kind, label=label)
        self._tasks[task_id] = task

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

        # asyncio.create_task 立即调度, 不等
        try:
            task._async_task = asyncio.create_task(_run_wrapper())
        except RuntimeError as e:
            # 没在 event loop 里 (testing 环境直接调 submit), 标 failed
            logger.warning(
                "submit: 没找到 event loop, task 直接 failed: %s", e,
            )
            task.status = "failed"
            task.error = f"no event loop: {e}"
            task.finished_at = time.time()
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
    """全局 task manager (lazy)."""
    global _manager
    if _manager is None:
        _manager = TaskManager()
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


def _notify_task_done(task: Task) -> None:
    """BL-A2.3: 任务完成通知 (macOS notification + 桌宠 bubble queue).

    两个通道:
    1. **macOS osascript** display notification — 系统级弹通知 (锁屏 / 后台都能看)
    2. **桌宠 bubble queue** — 写到 ~/.catfish/pet_pending_bubbles.jsonl,
       Companion 桌宠端 polling 读这个文件主动冒泡

    通知文案区分 completed / failed, 简短直接.

    通知 disable 条件:
    - env CATFISH_TASK_NOTIFY=0 → 整个通道关
    - 任务 elapsed < 3 秒 (太短的任务通知打扰员工)
    """
    if os.environ.get("CATFISH_TASK_NOTIFY", "1") == "0":
        return
    if task.finished_at is None or task.started_at is None:
        return
    elapsed = task.finished_at - task.started_at
    if elapsed < 3.0:
        # 太快的任务不通知, 员工还在等结果, 不需要打扰
        return

    if task.status == "completed":
        title = "鲶鱼 · 任务完成"
        msg = f"{task.label or task.kind} 完成 ({elapsed:.0f}s)"
    elif task.status == "failed":
        title = "鲶鱼 · 任务失败"
        msg = f"{task.label or task.kind} 失败: {(task.error or '')[:80]}"
    else:
        return  # other states 不通知

    # 通道 1: macOS osascript 通知 (非阻塞, fire and forget)
    if _platform_is_macos():
        try:
            import subprocess
            # 转义双引号防 osascript 解析炸
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
    """工具调用入口: 按 kind 选 runner, 启 task, 返 status_dict."""
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
    )
    return {
        "ok": True,
        "task_id": task.task_id,
        "status": task.status,
        "label": task.label,
    }
