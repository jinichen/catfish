"""Authorization code 存储 (BL-F11.P2, 6/9 鸿波).

# 目的

`/authorize` 颁发的一次性 authorization_code → `/token` 换 id_token 用. OAuth 2.0
RFC 6749 §4.1 流程的中间态.

# 历史 (旧版 in-memory dict, 6/9 之前)

老版 `routes.py:_CodeStore` 是 `dict[code, _AuthCode]` 进程内存. 单 worker OK,
但 BL-F11 bench 发现 identity 想多 worker scale 时, authorization_code 在
worker A 颁发, 浏览器跳转后 POST /token 可能落到 worker B → 找不到 code → 失败.
1000 employee 早高峰全走 authorization_code grant 时, 4 worker 部署 75% 登不进.

# 这版 (sqlite, 跨 worker 共享)

跟 refresh_tokens.py 同模式: sqlite 单文件 ~/.catfish/identity-server/auth_codes.db.
多 worker 启动时各 open 同 db, 写靠 sqlite 默认 5s busy timeout 串行化.

Schema:

  auth_codes (
    code           TEXT PRIMARY KEY,           -- secrets.token_urlsafe(32)
    user_email     TEXT NOT NULL,              -- user.email (consume 时回查 registry)
    client_id      TEXT NOT NULL,
    redirect_uri   TEXT NOT NULL,
    scope          TEXT NOT NULL,
    nonce          TEXT NOT NULL,
    issued_at      INTEGER NOT NULL,           -- unix seconds
    used_at        INTEGER                     -- NULL = 未用, 非空 = 用过 (一次性)
  )
  INDEX idx_auth_codes_issued ON (issued_at)   -- cleanup 过期用

# 一次性 + TTL 300s

`consume(code)`: 在事务里 UPDATE used_at = now WHERE code = ? AND used_at IS NULL,
RETURNING *. UPDATE 没 hit 行 (已用过 / 不存在 / 过期) → 返 None. 避免读后写的
race (worker A 读到未用, B 同时读到未用, 都成功 — UPDATE 是原子的).

# 跟 refresh_tokens.py 区别

- refresh_token TTL 30 天, 这个 5 分钟
- refresh_token rotation chain (parent_token), 这个一次性销毁
- refresh_token revoke 单独 admin op, 这个 lazy cleanup_expired
"""
from __future__ import annotations

import logging
import os
import secrets
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger("catfish.identity.code_store")


_CODE_TTL_SECS = 300  # 5 分钟, OIDC 推荐


def _default_db_path() -> Path:
    """auth_codes.db 默认位置. env CATFISH_AUTH_CODE_DB 覆盖."""
    if env := os.environ.get("CATFISH_AUTH_CODE_DB"):
        return Path(env).expanduser()
    home = os.environ.get("HOME") or os.environ.get("USERPROFILE") or "."
    return Path(home) / ".catfish" / "identity-server" / "auth_codes.db"


_SCHEMA = """
CREATE TABLE IF NOT EXISTS auth_codes (
    code         TEXT PRIMARY KEY,
    user_email   TEXT NOT NULL,
    client_id    TEXT NOT NULL,
    redirect_uri TEXT NOT NULL,
    scope        TEXT NOT NULL,
    nonce        TEXT NOT NULL,
    issued_at    INTEGER NOT NULL,
    used_at      INTEGER
);
CREATE INDEX IF NOT EXISTS idx_auth_codes_issued ON auth_codes(issued_at);
"""


@dataclass
class AuthCodeRecord:
    """一次性 authorization code 的持久化记录.

    跟旧 `_AuthCode` 区别: 存 user_email 不存 user 对象 (sqlite 不能 serialize
    IdentityUser). consume 后 routes_token.py 用 registry.find(user_email) 回查.
    """

    code: str
    user_email: str
    client_id: str
    redirect_uri: str
    scope: str
    nonce: str
    issued_at: int
    used_at: Optional[int] = None

    @property
    def used(self) -> bool:
        return self.used_at is not None


class CodeStore:
    """sqlite 后端的 authorization_code 持久化 (BL-F11.P2 多 worker fix).

    跟 RefreshTokenStore 同模式: 短连接, 每次 op 开闭 connection. sqlite 默认
    5s busy timeout 串行化并发写, 多 worker 不会撞.

    issue() 跟 consume() 接口跟旧 in-memory `_CodeStore` 兼容, routes.py 调用不变
    (除了 issue 参数 user= → user_email=).
    """

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self.db_path = db_path or _default_db_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(str(self.db_path), timeout=5.0)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._conn() as conn:
            conn.executescript(_SCHEMA)
        logger.info("auth_codes.db 初始化: %s", self.db_path)

    def issue(
        self,
        *,
        user_email: str,
        client_id: str,
        redirect_uri: str,
        scope: str,
        nonce: str,
    ) -> AuthCodeRecord:
        """颁发一次性 authorization_code. TTL = 5 分钟.

        参数 user_email 不是 user 对象 — sqlite 不存对象, consume 后回查 registry.
        """
        code = secrets.token_urlsafe(32)
        now = int(time.time())
        record = AuthCodeRecord(
            code=code,
            user_email=user_email,
            client_id=client_id,
            redirect_uri=redirect_uri,
            scope=scope,
            nonce=nonce,
            issued_at=now,
        )
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO auth_codes "
                "(code, user_email, client_id, redirect_uri, scope, nonce, issued_at, used_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, NULL)",
                (
                    record.code, record.user_email, record.client_id,
                    record.redirect_uri, record.scope, record.nonce, record.issued_at,
                ),
            )
        # lazy 清过期 (不每次都清, 1% 概率清一次, 避免每次 op 都扫表)
        if secrets.randbelow(100) == 0:
            self._cleanup_expired()
        return record

    def consume(self, code: str) -> Optional[AuthCodeRecord]:
        """一次性消费 code. UPDATE ... WHERE used_at IS NULL 原子保证 race-free.

        返 None 如果:
          - code 不存在
          - code 已用过 (used_at IS NOT NULL)
          - code 过期 (now - issued_at > TTL)
        """
        if not code:
            return None
        now = int(time.time())
        cutoff = now - _CODE_TTL_SECS
        with self._conn() as conn:
            # 原子: UPDATE only if 未用 AND 未过期, RETURNING 拿原行. sqlite 3.35+
            # 支持 RETURNING (Python 3.11+ sqlite3 模块带 sqlite 3.37+ OK).
            row = conn.execute(
                "UPDATE auth_codes SET used_at = ? "
                "WHERE code = ? AND used_at IS NULL AND issued_at >= ? "
                "RETURNING code, user_email, client_id, redirect_uri, scope, nonce, issued_at, used_at",
                (now, code, cutoff),
            ).fetchone()
        if row is None:
            return None
        return AuthCodeRecord(
            code=row["code"],
            user_email=row["user_email"],
            client_id=row["client_id"],
            redirect_uri=row["redirect_uri"],
            scope=row["scope"],
            nonce=row["nonce"],
            issued_at=row["issued_at"],
            used_at=row["used_at"],
        )

    def _cleanup_expired(self) -> int:
        """删过期 ≥ 1 天的 code (留 1 天审计). 返删了几条."""
        cutoff = int(time.time()) - 86400
        with self._conn() as conn:
            cur = conn.execute(
                "DELETE FROM auth_codes WHERE issued_at < ?", (cutoff,)
            )
            n = cur.rowcount
        if n:
            logger.info("cleanup_expired: 删 %d 条 1 天前 auth_codes", n)
        return n

    def __len__(self) -> int:
        with self._conn() as conn:
            row = conn.execute("SELECT COUNT(*) FROM auth_codes").fetchone()
        return row[0]


__all__ = [
    "AuthCodeRecord",
    "CodeStore",
]
