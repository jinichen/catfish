"""Adapter 工厂 —— 平台 + 客户端检测, 返回合适的 EmailAdapter。

调用方 (CLI / SKILL helper) 不直接 import adapter, 一律走这里:
    adapter = get_adapter()                  # 自动选当前平台默认
    adapter = get_adapter("foxmail-mac")    # 指定具体客户端

未来加新 adapter (outlook-mac / outlook-win / foxmail-win) 时, 改这一个文件就行,
SKILL.md 跟 CLI 不动。
"""
from __future__ import annotations

import logging
import platform

from .adapters.base import DataNotFoundError, EmailAdapter

logger = logging.getLogger("catfish_email.inbox")


def get_adapter(client: str | None = None) -> EmailAdapter:
    """工厂: 返回合适的 adapter。

    Args:
        client: 显式指定 'foxmail-mac' / 'outlook-mac' / 'outlook-win' / 'foxmail-win'
                None 则按平台自动挑 (Mac → 优先 Outlook, 没装就 Foxmail; Win 同理)

    Raises:
        DataNotFoundError: 当前平台一个能用的 adapter 都没找到
        ValueError: 显式指定的 client 名字非法
    """
    if client is not None:
        return _get_adapter_explicit(client)

    # 自动选: 按平台 + 是否已装客户端的优先级
    system = platform.system()
    candidates: list[str]
    if system == "Darwin":
        candidates = ["outlook-mac", "foxmail-mac"]
    elif system == "Windows":
        candidates = ["outlook-win", "foxmail-win"]
    else:
        raise DataNotFoundError(
            f"catfish-email 暂不支持 {system} 平台 (仅 macOS / Windows)"
        )

    last_err: Exception | None = None
    for c in candidates:
        try:
            return _get_adapter_explicit(c)
        except (DataNotFoundError, ImportError, NotImplementedError) as e:
            last_err = e
            logger.debug("adapter %s 不可用: %s", c, e)
            continue

    raise DataNotFoundError(
        f"{system} 上没找到可用的邮件客户端 (尝试过: {', '.join(candidates)})。"
        f"装个 Outlook 或 Foxmail 再加邮箱账号。最后一个错: {last_err}"
    )


def _get_adapter_explicit(client: str) -> EmailAdapter:
    """按客户端名字 dispatch. 每个分支 lazy import, 减少不需要的依赖。"""
    if client == "foxmail-mac":
        from .adapters.foxmail_mac import FoxmailMacAdapter
        return FoxmailMacAdapter()
    if client == "outlook-mac":
        # TODO: 等 outlook_mac.py 实现
        raise NotImplementedError("outlook-mac adapter 还没实现, 用 foxmail-mac")
    if client == "outlook-win":
        # TODO: 等 outlook_win.py 实现 (pywin32 COM)
        raise NotImplementedError("outlook-win adapter 还没实现")
    if client == "foxmail-win":
        # TODO: Foxmail Windows 7+ 的 .box / SQLite 路径还没探
        raise NotImplementedError("foxmail-win adapter 还没实现")
    raise ValueError(f"未知 client: {client!r} (合法: foxmail-mac / outlook-mac / outlook-win / foxmail-win)")
