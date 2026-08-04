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

config_watcher 兜住所有路径: poll 内容哈希, 真变了就退 (quiet period 后), autostart respawn.

# 跟 skill_watcher 的关系
========================
跟 skill_watcher 同一套机制 (poll + quiet period + os._exit + autostart respawn),
共享 mark_dispatch / _wait_quiet 状态. 不抽新模块, 直接复用.

不同点: 监一个文件 (config.yaml) 不是目录, 签名是**内容 sha256** (不是 mtime
—— 8/4: 用 mtime 会被 Companion 的 JWT 刷新触发, 详见 _signature 文档).
"""
from __future__ import annotations

import logging
import os
import sys
import threading
import hashlib
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
    """用**内容哈希**做签名. 文件不存在 → ().

    # 为什么不是 (mtime, size)  (8/4 鸿波查 tool-bridge 每天重启 ~100 次)

    老实现是 `(st.st_mtime, st.st_size)`。问题是 Companion 自己会定期重写这个
    文件 —— `hermes_jwt_sync` 每隔一段时间刷新 `model.api_key` 里的 JWT。JWT
    长度固定, 所以**内容等长、mtime 变了**, 签名判定"变了"。

    于是形成一个 Companion 自己咬自己的闭环:

        Companion 刷 JWT → 重写 config.yaml (size 不变, mtime 变)
          → config_watcher 判定变化 → os._exit(0) 主动退出
          → watchdog 判死 → pkill + respawn
          → 循环

    实测鸿波机器 ~100 次/天, 凌晨照跑, 累计 7381 次。日志里没有 traceback、
    没有 shutdown 记录 —— 因为它是**正常退出**的, 每个实例的最后一行都停在启动
    序列的末尾。查了大半天才从"手动脱离 Companion 跑一次"里看到真因。

    实测那次的证据: `mtime 1785843739 → 1785844734, size 1986 → 1986`。
    size 一个字节没变。

    改成内容哈希后, 只有**内容真的变了**才重启。JWT 轮换本身确实会改内容
    (api_key 值变了), 所以那种情况仍会重启一次 —— 但那是必要的, 而不是
    每次 touch 都来一遍。
    """
    if not config_path.is_file():
        return ()
    try:
        data = config_path.read_bytes()
    except OSError as e:
        logger.warning("config_watcher signature 失败: %s", e)
        return ()
    return (hashlib.sha256(data).hexdigest(), len(data))


def _watcher_loop(config_path: Path) -> None:
    baseline = _signature(config_path)
    if baseline == ():
        logger.info(
            "config_watcher 启动: %s 不存在, 跳过监控 (员工没装 hermes config?)",
            config_path,
        )
        return

    logger.info(
        "config_watcher 启动: %s sha256=%s… size=%d",
        config_path, baseline[0][:12], baseline[1],
    )

    while True:
        time.sleep(POLL_INTERVAL_SEC)
        try:
            current = _signature(config_path)
            if current == baseline:
                continue

            logger.info(
                "config_watcher 检测到 %s 内容变化: sha256 %s… → %s…, size %d → %d",
                config_path,
                (baseline[0] if baseline else "")[:12],
                (current[0] if current else "")[:12],
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
