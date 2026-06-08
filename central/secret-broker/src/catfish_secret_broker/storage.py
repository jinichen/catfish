"""Secret 存储后端 — 3 个 backend (5/9 dev, 6/9 BL-BROKER-DEPLOY 加 prod PG 加密).

# 后端选择 (env CATFISH_SECRET_BROKER_BACKEND)

- `pg` (6/9 加, **prod 默认**): PostgreSQL 加密表, AES-256-GCM per-secret nonce.
  master key 从 env CATFISH_SECRET_MASTER_KEY (base64 32 byte). docker 部署用.
- `keyring` (dev only): mac Keychain / Win wincred / Linux libsecret. docker
  container 没桌面 session → keyring 不可用, 不要 prod 用. 已 BL-BROKER-DEPLOY
  确认: docker 起 keyring 会 fallback memory, 重启丢凭据 → 所有员工要重 OAuth.
- `memory`: 进程内 dict, 测试用.
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Optional, Protocol

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


class PgEncryptedStorage:
    """PostgreSQL 加密后端 — prod docker 部署用 (6/9 BL-BROKER-DEPLOY).

    # 加密方案

    AES-256-GCM (NIST SP 800-38D). 每条 secret 独立 96-bit nonce (GCM 推荐).
    认证标签 16 byte 附在 ciphertext 尾部 (cryptography 库默认行为).

    密文存储:
        ciphertext = AES-GCM(key=master_key, nonce=random_96bit, plaintext=value)
        + 16 byte auth tag (already 包含在 cryptography 库 encrypt 返回里)

    解密时只需要 master_key + nonce + ciphertext, 验 tag 不过抛 InvalidTag.

    # master key 管理

    env CATFISH_SECRET_MASTER_KEY: base64 编码 32 byte (256 bit) 密钥.
    deploy.sh 首次部署时 openssl rand -base64 32 自动生成, 写回 .env.
    改 master key 会让所有现存 secret 不可解密 — 不要换!
    (Phase 2 加 key rotation: 加 master_key_version 列, 支持 multi-key 解密)

    # Schema

    secret_broker_secrets (
        ref         TEXT PRIMARY KEY,    -- 业务调用方的 reference (如 'oauth:jira:alice@x.com')
        ciphertext  BYTEA NOT NULL,      -- AES-GCM 密文 + 16 byte tag
        nonce       BYTEA NOT NULL,      -- 12 byte (96 bit) random nonce per secret
        created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
    )

    # 安全声明

    - DB 备份里只有密文, 没 master_key → 备份外泄不会泄露 secret
    - master_key 在 env, docker secret / systemd-creds 可注入
    - 加密层全 server side, 客户端只看到明文 (gateway / mcp-registry 调时已经验过员工身份)
    """

    _SCHEMA = """
    CREATE TABLE IF NOT EXISTS secret_broker_secrets (
        ref         TEXT PRIMARY KEY,
        ciphertext  BYTEA NOT NULL,
        nonce       BYTEA NOT NULL,
        created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    """

    def __init__(self, db_url: str, master_key: bytes) -> None:
        # 延迟 import: keyring backend 不需要 psycopg / cryptography
        import psycopg  # noqa: PLC0415
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: PLC0415

        if len(master_key) != 32:
            raise ValueError(
                f"master_key 必须 32 byte (256 bit), 实际 {len(master_key)} byte. "
                f"用 openssl rand -base64 32 生成"
            )

        self._db_url = db_url
        self._aead = AESGCM(master_key)
        self._psycopg = psycopg
        self._lock = threading.Lock()  # psycopg 连接非线程安全

        self._init_schema()

    def _init_schema(self) -> None:
        with self._psycopg.connect(self._db_url) as conn:
            with conn.cursor() as cur:
                cur.execute(self._SCHEMA)
            conn.commit()
        logger.info("secret_broker_secrets 表初始化 OK (pg 加密后端)")

    def get(self, ref: str) -> Optional[str]:
        from cryptography.exceptions import InvalidTag  # noqa: PLC0415
        with self._lock, self._psycopg.connect(self._db_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT ciphertext, nonce FROM secret_broker_secrets WHERE ref = %s",
                    (ref,),
                )
                row = cur.fetchone()
        if row is None:
            return None
        ciphertext, nonce = bytes(row[0]), bytes(row[1])
        try:
            plaintext = self._aead.decrypt(nonce, ciphertext, associated_data=None)
        except InvalidTag:
            logger.error(
                "secret %s 解密失败 (InvalidTag) — master_key 改了? 改回原 key 或重新写入",
                ref,
            )
            return None
        return plaintext.decode("utf-8")

    def set(self, ref: str, value: str) -> None:
        import secrets as _secrets  # noqa: PLC0415
        nonce = _secrets.token_bytes(12)  # GCM 推荐 96-bit nonce
        ciphertext = self._aead.encrypt(
            nonce, value.encode("utf-8"), associated_data=None
        )
        with self._lock, self._psycopg.connect(self._db_url) as conn:
            with conn.cursor() as cur:
                # UPSERT — 已存在则覆盖 (跟 keyring set 一致语义)
                cur.execute(
                    """
                    INSERT INTO secret_broker_secrets (ref, ciphertext, nonce)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (ref) DO UPDATE
                    SET ciphertext = EXCLUDED.ciphertext,
                        nonce = EXCLUDED.nonce,
                        updated_at = now()
                    """,
                    (ref, ciphertext, nonce),
                )
            conn.commit()

    def delete(self, ref: str) -> bool:
        with self._lock, self._psycopg.connect(self._db_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM secret_broker_secrets WHERE ref = %s", (ref,)
                )
                deleted = cur.rowcount > 0
            conn.commit()
        return deleted

    def exists(self, ref: str) -> bool:
        with self._lock, self._psycopg.connect(self._db_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM secret_broker_secrets WHERE ref = %s", (ref,)
                )
                return cur.fetchone() is not None

    @property
    def backend_name(self) -> str:
        return "pg"


def _load_master_key() -> bytes:
    """env CATFISH_SECRET_MASTER_KEY → 32 byte AES-256 key.

    格式: base64 编码. 32 byte 长 (raw 也接受, 24 byte 是 hex 跟 base64 都不是
    32 → 报错让客户用 openssl rand -base64 32 重新生成).
    """
    import base64  # noqa: PLC0415
    env = os.environ.get("CATFISH_SECRET_MASTER_KEY", "").strip()
    if not env:
        raise RuntimeError(
            "CATFISH_SECRET_MASTER_KEY 未设. 用 openssl rand -base64 32 生成"
        )
    if env in ("CHANGE_ME", "CHANGE_ME_RUN_DEPLOY_SH"):
        raise RuntimeError(
            "CATFISH_SECRET_MASTER_KEY 是占位值. 跑 deploy.sh 让它自动生成, "
            "或手工 openssl rand -base64 32 改 .env"
        )
    try:
        key = base64.b64decode(env)
    except Exception as e:
        raise RuntimeError(
            f"CATFISH_SECRET_MASTER_KEY 不是 base64: {e}. 用 openssl rand -base64 32 重生"
        ) from e
    if len(key) != 32:
        raise RuntimeError(
            f"CATFISH_SECRET_MASTER_KEY 长度 {len(key)} byte ≠ 32. "
            f"用 openssl rand -base64 32 重生"
        )
    return key


def make_storage() -> SecretStorage:
    """工厂 — 根据 env 选后端.

    env CATFISH_SECRET_BROKER_BACKEND:
        pg       — prod 默认, PG 加密表 (6/9 BL-BROKER-DEPLOY)
        keyring  — dev only, mac/Win 桌面 keyring (docker 起不来)
        memory   — 测试用, 重启丢
    """
    backend_env = os.environ.get("CATFISH_SECRET_BROKER_BACKEND", "keyring").lower()

    if backend_env == "pg":
        # 6/9 BL-BROKER-DEPLOY: prod docker 部署走这.
        db_url = os.environ.get("CATFISH_DB_URL", "").strip()
        if not db_url:
            raise RuntimeError(
                "CATFISH_SECRET_BROKER_BACKEND=pg 但 CATFISH_DB_URL 未设"
            )
        master_key = _load_master_key()
        logger.info("secret-broker: 使用 pg 加密后端 (AES-256-GCM)")
        return PgEncryptedStorage(db_url=db_url, master_key=master_key)

    if backend_env == "memory":
        logger.info("secret-broker: 使用内存后端 (env CATFISH_SECRET_BROKER_BACKEND=memory)")
        return InMemoryStorage()

    # 默认: keyring (dev), 失败兜底到内存. **prod 不该走这** — 改 BACKEND=pg.
    try:
        storage = KeyringStorage()
        # 启动时 ping 一下确认 keyring 真能用
        storage.set("__health__", "ok")
        storage.delete("__health__")
        logger.info("secret-broker: 使用 keyring 后端 (仅 dev — prod 切 BACKEND=pg)")
        return storage
    except Exception as e:
        logger.warning(
            "secret-broker: keyring 后端不可用 (%s), 降级到内存. "
            "Linux 装 dbus-x11 / libsecret-tools, mac/Win 应直接可用. "
            "**docker 部署应改 CATFISH_SECRET_BROKER_BACKEND=pg**",
            e,
        )
        return InMemoryStorage()
