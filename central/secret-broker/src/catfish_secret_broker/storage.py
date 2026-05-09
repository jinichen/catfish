"""Secret 存储后端 — keyring 优先, 内存兜底 (BL-G6 dev MVP, 5/9)."""
from __future__ import annotations

import logging
import os
import threading
from typing import Protocol

logger = logging.getLogger("catfish.secret_broker.storage")

# keyring service 名 (mac Keychain / wincred / libsecret 用这个分隔)
_KEYRING_SERVICE = "catfish-secret-broker"


class SecretStorage(Protocol):
    """所有后端实现这个接口."""

    def get(self, ref: str) -> str | None: ...
    def set(self, ref: str, value: str) -> None: ...
    def delete(self, ref: str) -> bool: ...
    def exists(self, ref: str) -> bool: ...

    @property
    def backend_name(self) -> str: ...


class InMemoryStorage:
    """进程内 dict — 兜底用, 重启丢. dev 测试 / keyring 不可用时."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._data: dict[str, str] = {}

    def get(self, ref: str) -> str | None:
        with self._lock:
            return self._data.get(ref)

    def set(self, ref: str, value: str) -> None:
        with self._lock:
            self._data[ref] = value

    def delete(self, ref: str) -> bool:
        with self._lock:
            return self._data.pop(ref, None) is not None

    def exists(self, ref: str) -> bool:
        with self._lock:
            return ref in self._data

    @property
    def backend_name(self) -> str:
        return "memory"


class KeyringStorage:
    """keyring 后端 — mac Keychain / Win wincred / Linux libsecret."""

    def __init__(self) -> None:
        import keyring  # noqa: PLC0415  延迟 import, 没装也能 fallback
        self._keyring = keyring

    def get(self, ref: str) -> str | None:
        try:
            return self._keyring.get_password(_KEYRING_SERVICE, ref)
        except Exception as e:
            logger.warning("keyring.get_password(%s) 失败: %s", ref, e)
            return None

    def set(self, ref: str, value: str) -> None:
        self._keyring.set_password(_KEYRING_SERVICE, ref, value)

    def delete(self, ref: str) -> bool:
        try:
            self._keyring.delete_password(_KEYRING_SERVICE, ref)
            return True
        except Exception:
            # keyring 抛 PasswordDeleteError when not exists. 静默返 False.
            return False

    def exists(self, ref: str) -> bool:
        return self.get(ref) is not None

    @property
    def backend_name(self) -> str:
        return "keyring"


def make_storage() -> SecretStorage:
    """工厂 — 根据 env 选后端 (默认 keyring → 内存兜底).

    env CATFISH_SECRET_BROKER_BACKEND:
        keyring (默认), memory (强制内存, 测试用)
    """
    backend_env = os.environ.get("CATFISH_SECRET_BROKER_BACKEND", "keyring").lower()
    if backend_env == "memory":
        logger.info("secret-broker: 使用内存后端 (env CATFISH_SECRET_BROKER_BACKEND=memory)")
        return InMemoryStorage()

    # 默认: keyring, 失败兜底到内存
    try:
        storage = KeyringStorage()
        # 启动时 ping 一下确认 keyring 真能用
        storage.set("__health__", "ok")
        storage.delete("__health__")
        logger.info("secret-broker: 使用 keyring 后端")
        return storage
    except Exception as e:
        logger.warning(
            "secret-broker: keyring 后端不可用 (%s), 降级到内存. "
            "Linux 装 dbus-x11 / libsecret-tools, mac/Win 应直接可用",
            e,
        )
        return InMemoryStorage()
