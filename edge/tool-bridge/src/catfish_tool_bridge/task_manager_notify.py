"""任务完成的 macOS 系统通知 —— 含去重。

BL-TASKMGR-SPLIT 8/15: 从 task_manager.py 抽出来 (1133 行超限)。纯搬迁, 逻辑一行未改。

⚠ _RECENT_NOTIFY_BY_LABEL 搬过来是安全的: 测试是
`task_manager._RECENT_NOTIFY_BY_LABEL.clear()` / `[...] = ...` —— **改内容不是
赋值**, re-export 指向同一个 dict, 改哪边都一样。
(对比 _manager: 测试写的是 `task_manager._manager = ...`, 那是**赋值**, 搬走就
只改到 re-export, manager() 读的还是自己那份 —— 所以它留在 task_manager.py。)
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from pathlib import Path

from .task_manager_types import Task

logger = logging.getLogger("catfish.tool_bridge.task_manager")

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
