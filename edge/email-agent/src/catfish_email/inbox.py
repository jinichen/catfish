"""Adapter 工厂 —— 平台 + 客户端检测, 返回合适的 EmailAdapter。

调用方 (CLI / SKILL helper) 不直接 import adapter, 一律走这里:
    adapter = get_adapter()                  # 自动选当前平台默认
    adapter = get_adapter("foxmail-mac")     # 指定具体客户端
    adapter = get_adapter("apple-mail")      # macOS 原生 Mail.app

5/18 BL-EMAIL-APPLEMAIL: 加 apple-mail (替原 outlook-mac), macOS 默认改成
['apple-mail', 'foxmail-mac'] 候选顺序. 理由: Mail.app 100% 装机, 优先尝试.
"""
from __future__ import annotations

import logging
import platform

from .adapters.base import DataNotFoundError, EmailAdapter

logger = logging.getLogger("catfish_email.inbox")


def get_adapter(client: str | None = None) -> EmailAdapter:
    """工厂: 返回合适的 adapter。

    Args:
        client: 显式指定 'apple-mail' / 'foxmail-mac' / 'outlook-win' / 'foxmail-win'
                None 则按平台自动挑 (Mac → 优先 Apple Mail, 没装就 Foxmail; Win 同理)

    Raises:
        DataNotFoundError: 当前平台一个能用的 adapter 都没找到
        ValueError: 显式指定的 client 名字非法
    """
    if client is not None:
        return _get_adapter_explicit(client)

    # 自动选: 按平台 + 是否已装客户端的优先级
    # 5/18 BL-EMAIL-APPLEMAIL: macOS 默认 Apple Mail.app 优先 (100% 装机), Foxmail 兜底
    system = platform.system()
    candidates: list[str]
    if system == "Darwin":
        candidates = ["apple-mail", "foxmail-mac"]
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
    if client == "apple-mail":
        # 5/18 BL-EMAIL-APPLEMAIL: macOS Mail.app AppleScript adapter
        from .adapters.apple_mail import AppleMailAdapter
        return AppleMailAdapter()
    if client == "foxmail-mac":
        from .adapters.foxmail_mac import FoxmailMacAdapter
        return FoxmailMacAdapter()
    if client == "outlook-mac":
        # 5/17 BL-EMAIL-APPLEMAIL: outlook-mac 改 apple-mail. 这里留兼容 alias.
        from .adapters.apple_mail import AppleMailAdapter
        logger.warning(
            "client='outlook-mac' deprecated, 5/18 起 macOS 改 Apple Mail. "
            "用 'apple-mail' 显式指定."
        )
        return AppleMailAdapter()
    if client == "outlook-win":
        # TODO: 等 outlook_win.py 实现 (pywin32 COM)
        raise NotImplementedError("outlook-win adapter 还没实现")
    if client == "foxmail-win":
        # TODO: Foxmail Windows 7+ 的 .box / SQLite 路径还没探
        raise NotImplementedError("foxmail-win adapter 还没实现")
    raise ValueError(
        f"未知 client: {client!r} "
        f"(合法: apple-mail / foxmail-mac / outlook-win / foxmail-win)",
    )
