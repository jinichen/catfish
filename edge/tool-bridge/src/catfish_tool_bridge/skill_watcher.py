"""监 ~/.hermes/skills/ 变化, 新 skill 落盘后 graceful 重启 tool-bridge。

# 为啥需要这个
================
Hermes registry 在 import 时一次性扫 skills, 之后不再 rescan. 但员工通过 LLM
创建新 skill 后 (skill_manage(action=create)), 我们希望 catfish 立刻能用上 ——
否则员工得手动重启 Companion / hermes, UX 烂。

Hermes 没暴露 reload API (registry singleton), 我们也不想 monkey-patch 它的内部.
所以选最稳路径: **进程级重启** — tool-bridge 退出, Companion 的 autostart 自动
respawn 新进程, 新进程 bootstrap 时重新 import 触发 skill 重扫。

# 关键约束
==========
1. 不能在 LLM 调用中途突然退出 — 那会把 LLM 的工具调用打断, 员工困惑
   → 设 "quiet period": 必须 30s 内没 dispatch_tool 调用才退
2. 不依赖 watchdog (跨平台 + 装包麻烦), 用 polling — 邮件 / skill 操作频率不高,
   15s 间隔够用
3. 不是 hot-reload, 是 cold restart — 接受 ~3s 重启间隙

# 时序
======
[LLM 创建 skill]
  → SKILL.md 落盘
  → watcher 下次 poll (≤ 15s) 检测到 mtime 变化
  → 进入"等 quiet" 模式
  → 等到最近一次 dispatch_tool > 30s 前
  → os._exit(0) 退出
  → Companion autostart 检测到 pid 死, 5s 内 respawn
  → 新 tool-bridge 启动 + bootstrap (~3-5s import hermes tools)
  → list_tools 返回含新 skill
  → 员工后续对话立刻能用
"""
from __future__ import annotations

import logging
import os
import sys
import threading
import time
from pathlib import Path

logger = logging.getLogger("catfish.tool_bridge.skill_watcher")

# ====== 调参 ======
#: 多久检查一次 skills/ 目录, 太频 CPU 浪费, 太疏延迟感
POLL_INTERVAL_SEC = 15
#: 检测到变化后, 必须 N 秒没 dispatch_tool 才认为"安全"退出
#: 这是为了不打断正在跑的 LLM 工具调用循环 (典型一次工具调用 < 10s)
QUIET_PERIOD_SEC = 30
#: 单次 quiet 等待最长不能超过 (避免被 churn 卡死, 强制退一次让 autostart 接管)
MAX_QUIET_WAIT_SEC = 300

# ====== 共享状态 (module-level, 简单线程安全) ======
_last_dispatch_ts = 0.0
_lock = threading.Lock()


def mark_dispatch() -> None:
    """adapter.dispatch_tool 每次调用时记下时间戳。

    用来给 watcher 判断"现在 LLM 工具调用是不是繁忙"。
    """
    global _last_dispatch_ts
    with _lock:
        _last_dispatch_ts = time.time()


def _signature(skills_dir: Path) -> tuple:
    """用所有 SKILL.md 的 (path, mtime) 元组集合做签名。

    增 / 删 / 改 任一 SKILL.md 签名都会变, 不管是 hermes 内置还是 catfish 自家。
    """
    if not skills_dir.is_dir():
        return ()
    try:
        return tuple(sorted(
            (str(f), f.stat().st_mtime)
            for f in skills_dir.rglob("SKILL.md")
        ))
    except OSError as e:
        logger.warning("skill_watcher signature 失败: %s", e)
        return ()


def _wait_quiet() -> bool:
    """阻塞直到最近 QUIET_PERIOD_SEC 没 dispatch 调用。

    Returns:
        True  — 安静期已到, 可以退出
        False — 等超过 MAX_QUIET_WAIT_SEC, 强制退 (防 LLM 持续调用导致永远不退)
    """
    waited = 0.0
    while waited < MAX_QUIET_WAIT_SEC:
        with _lock:
            last = _last_dispatch_ts
        idle = time.time() - last
        if idle >= QUIET_PERIOD_SEC:
            return True
        sleep_for = max(1.0, QUIET_PERIOD_SEC - idle + 0.5)
        time.sleep(sleep_for)
        waited += sleep_for
    logger.warning(
        "skill_watcher: 等了 %ds 还在繁忙, 强制退 (LLM 调用会被 ConnectionReset)",
        MAX_QUIET_WAIT_SEC,
    )
    return False


def _watcher_loop(skills_dir: Path) -> None:
    baseline = _signature(skills_dir)
    logger.info(
        "skill_watcher 启动: %s, baseline %d 个 SKILL.md",
        skills_dir, len(baseline),
    )
    while True:
        time.sleep(POLL_INTERVAL_SEC)
        try:
            current = _signature(skills_dir)
            if current == baseline:
                continue

            # 算 diff 给 log 看
            old_set = {p for p, _ in baseline}
            new_set = {p for p, _ in current}
            added = new_set - old_set
            removed = old_set - new_set
            modified = (
                {p for p, m in current if (p, m) not in baseline}
                & {p for p, _ in baseline}
            )

            logger.info(
                "skill_watcher 检测到变化: +%d -%d ~%d (added=%s removed=%s)",
                len(added), len(removed), len(modified),
                list(added)[:3], list(removed)[:3],
            )

            # 等安静期
            _wait_quiet()

            logger.warning(
                "skill_watcher: 退出 tool-bridge, "
                "Companion autostart 会 respawn 重新扫 skill"
            )
            # 让 log handler 把信息刷出去, 然后硬退
            sys.stdout.flush()
            sys.stderr.flush()
            time.sleep(0.5)
            os._exit(0)

        except Exception:  # noqa: BLE001
            logger.exception("skill_watcher tick 出错, 继续")
            # 不要因为偶尔的 exception 把 watcher 杀掉
            continue


def start(skills_dir: Path | None = None) -> threading.Thread:
    """起 daemon 线程监 skills 目录。

    Args:
        skills_dir: 监控目录, 默认 ~/.hermes/skills

    Returns:
        Thread 对象 (daemon, 不会阻止主进程退出)
    """
    if skills_dir is None:
        skills_dir = Path.home() / ".hermes" / "skills"
    t = threading.Thread(
        target=_watcher_loop,
        args=(skills_dir,),
        daemon=True,
        name="catfish-skill-watcher",
    )
    t.start()
    return t
