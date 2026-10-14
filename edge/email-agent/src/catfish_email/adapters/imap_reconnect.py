"""IMAP 连接被服务器中途断开时的处理 (从 imap_mail.py 拆出来, 那边快 800 行了)。"""
from __future__ import annotations

import functools
import imaplib
import logging

from .base import ClientNotRunningError

logger = logging.getLogger(__name__)


def drop_conn(adapter) -> None:
    """连接已经断了: 不发 LOGOUT (会再抛一次), 直接丢掉, 下次 _connect 重建。"""
    conn, adapter._conn = adapter._conn, None
    if conn is not None:
        try:
            conn.shutdown()
        except Exception:  # noqa: BLE001
            pass


def reconnect_once(method):
    """服务器中途断开时重连再试一次 —— 只给只读操作用 (写操作重试可能做两遍)。

    10/14 Windows 读「Fw: 业务外包框架协议」: FETCH 整封时 ``imaplib.IMAP4.abort: socket error: EOF``
    —— 服务器把连接关了, 原始 traceback 直接糊在界面上。Python 文档对 IMAP4.abort 的说法就是
    "关掉实例重建一个通常就能恢复"。重连后还不行, 换成员工看得懂的一句话 (ClientNotRunningError)。
    """

    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        try:
            return method(self, *args, **kwargs)
        except (imaplib.IMAP4.abort, OSError) as error:
            logger.warning("IMAP 连接中途断了 (%s: %s), 重连再试一次", type(error).__name__, error)
            self._drop_conn()
        try:
            return method(self, *args, **kwargs)
        except (imaplib.IMAP4.abort, OSError) as error:
            self._drop_conn()
            raise ClientNotRunningError(
                f"邮件服务器 {self.config.host} 中途断开了连接, 重连后还是不行 "
                f"({type(error).__name__})。稍后再试, 或检查网络 / 安全软件是否拦了 IMAP。"
            ) from error

    return wrapper
