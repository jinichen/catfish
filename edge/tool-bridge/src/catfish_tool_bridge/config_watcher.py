"""监 ~/.hermes/config.yaml 变化, 配置改了就 graceful 重启 tool-bridge.

# 为啥需要这个 (踩过的坑)
=========================
tool-bridge import hermes 时一次性读 config.yaml 拿 cdp_url 等配置, 之后**不再 rescan**.
但配置文件可能被外部改:
  - chrome.rs `chrome_launch` 后台 poll 拿到新 webSocketDebuggerUrl 写回 config (#50)
  - 员工手动跑 catfish-browser-attach.sh 重写 cdp_url
  - 员工自己 vim ~/.hermes/config.yaml 改别的字段 (model preference / api_base)
  - 其他工具改了 config

只有 #50 主路径触发了 chrome.rs `kill_tool_bridge_for_respawn` (chrome.rs 里我们加的),
**其余路径都没人通知 tool-bridge**, 它就一直用旧 config 内存缓存. 撞坑表现:
  - browser_navigate 用旧 cdp_url 撞 404 / WebSocket 拒接
  - 模型用了 user 改过的旧 model_id, 路由错误

config_watcher 兜住所有路径: poll mtime, 变了就退 (quiet period 后), autostart respawn.

# 跟 skill_watcher 的关系
========================
跟 skill_watcher 同一套机制 (poll + quiet period + os._exit + autostart respawn),
共享 mark_dispatch / _wait_quiet 状态. 不抽新模块, 直接复用.

不同点: 监一个文件 (config.yaml) 不是目录, 签名是 (mtime, size).
"""
from __future__ import annotations

import logging
import os
import sys
import threading
import time
from pathlib import Path

# 复用 skill_watcher 的 quiet period 等待 + dispatch 时间戳
from .skill_watcher import _wait_quiet  # noqa: PLC2701

logger = logging.getLogger("catfish.tool_bridge.config_watcher")

# ====== 调参 ======
#: config 改动比 skill 改动更紧迫 (cdp_url 失效会立刻让 browser_navigate 挂),
#: poll 间隔比 skill_watcher 短一些
POLL_INTERVAL_SEC = 10


def _signature(config_path: Path) -> tuple:
    """用 (mtime, size) 做签名. 文件不存在 → (). """
    if not config_path.is_file():
        return ()
    try:
        st = config_path.stat()
        return (st.st_mtime, st.st_size)
    except OSError as e:
        logger.warning("config_watcher signature 失败: %s", e)
        return ()


def _watcher_loop(config_path: Path) -> None:
    baseline = _signature(config_path)
    if baseline == ():
        logger.info(
            "config_watcher 启动: %s 不存在, 跳过监控 (员工没装 hermes config?)",
            config_path,
        )
        return

    logger.info(
        "config_watcher 启动: %s mtime=%.0f size=%d",
        config_path, baseline[0], baseline[1],
    )

    while True:
        time.sleep(POLL_INTERVAL_SEC)
        try:
            current = _signature(config_path)
            if current == baseline:
                continue

            logger.info(
                "config_watcher 检测到 %s 变化: mtime %.0f → %.0f, size %d → %d",
                config_path,
                baseline[0] if baseline else 0, current[0] if current else 0,
                baseline[1] if baseline else 0, current[1] if current else 0,
            )

            # 等安静期 — 跟 skill_watcher 共享 _last_dispatch_ts
            _wait_quiet()

            logger.warning(
                "config_watcher: 退出 tool-bridge, "
                "Companion autostart 会 respawn 用新 config (新 cdp_url / model 配置)"
            )
            sys.stdout.flush()
            sys.stderr.flush()
            time.sleep(0.5)
            os._exit(0)

        except Exception:  # noqa: BLE001
            logger.exception("config_watcher tick 出错, 继续")
            continue


def start(config_path: Path | None = None) -> threading.Thread:
    """起 daemon 线程监 config 文件.

    Args:
        config_path: 监控文件, 默认 ~/.hermes/config.yaml

    Returns:
        Thread 对象 (daemon, 不会阻止主进程退出)
    """
    if config_path is None:
        config_path = Path.home() / ".hermes" / "config.yaml"
    t = threading.Thread(
        target=_watcher_loop,
        args=(config_path,),
        daemon=True,
        name="catfish-config-watcher",
    )
    t.start()
    return t
