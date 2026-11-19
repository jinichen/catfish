"""员工个人长期钥匙 (11/19, 给 Claude Code 这类工具直连中央用)。

鸿波「加一个长期有效的钥匙给 Claude Code 用」。平时登录拿到的 access_token 一小时过期,
靠 Companion 后台续; Claude Code 只能填一把固定的钥匙, 所以要一种不过期、但随时能收回的。

# 设计
- **员工自己生成, 不自动发**: 绝大多数人用不上; 自动给每人发一把等于平白多出几百把不过期的钥匙
- 钥匙 = `cfk_` + 随机串, 只在生成那一刻给员工看一次; 库里只存 sha256, 库泄露也拿不到钥匙
- 权限不烤进钥匙: 网关每次 (缓存一分钟) 来问 introspect, 这边按**当前**的用户资料出 claims ——
  管理员改了部门 / 可用模型 / 锁了人, 一分钟内钥匙跟着变; 锁了、删了的人钥匙直接失效
- 每人最多 MAX_ACTIVE 把, 员工随时吊销; 最后一次使用时间记下来 (5 分钟写一次, 不每次写库)

# 存储
sqlite (跟 refresh_tokens.db 同目录, identity_data 卷里持久化), 单实例多 worker 短连接。
"""
from __future__ import annotations

import hashlib
import logging
import os
import secrets
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger("catfish.identity.api_keys")

PREFIX = "cfk_"
MAX_ACTIVE = 5
_TOUCH_EVERY_SECS = 300

_SCHEMA = """
CREATE TABLE IF NOT EXISTS api_keys (
    id            TEXT PRIMARY KEY,
    key_hash      TEXT NOT NULL UNIQUE,
    sub           TEXT NOT NULL,
    name          TEXT NOT NULL,
    hint          TEXT NOT NULL,
    created_at    INTEGER NOT NULL,
    last_used_at  INTEGER,
    revoked_at    INTEGER
);
CREATE INDEX IF NOT EXISTS idx_api_keys_sub ON api_keys(sub);
"""


def _default_db_path() -> Path:
    if env := os.environ.get("CATFISH_API_KEY_DB"):
        return Path(env).expanduser()
    home = os.environ.get("HOME") or os.environ.get("USERPROFILE") or "."
    return Path(home) / ".catfish" / "identity-server" / "api_keys.db"


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


@dataclass
class ApiKey:
    id: str
    sub: str
    name: str
    hint: str
    created_at: int
    last_used_at: Optional[int] = None
    revoked_at: Optional[int] = None

    def public(self) -> dict:
        """给员工看的 (没有钥匙本身, 只有末 4 位)。"""
        return {"id": self.id, "name": self.name, "hint": f"{PREFIX}…{self.hint}",
                "created_at": self.created_at, "last_used_at": self.last_used_at}


class TooManyKeys(Exception):
    pass


class ApiKeyStore:
    def __init__(self, db_path: Optional[Path] = None) -> None:
        self.db_path = db_path or _default_db_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(_SCHEMA)

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(str(self.db_path), timeout=5)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _row(r: sqlite3.Row) -> ApiKey:
        return ApiKey(id=r["id"], sub=r["sub"], name=r["name"], hint=r["hint"], created_at=r["created_at"],
                      last_used_at=r["last_used_at"], revoked_at=r["revoked_at"])

    def create(self, sub: str, name: str) -> tuple[ApiKey, str]:
        """生成一把, 返回 (记录, 钥匙原文)。原文只这一次。"""
        sub = sub.strip().lower()
        name = (name or "").strip()[:60] or "Claude Code"
        if len(self.list(sub)) >= MAX_ACTIVE:
            raise TooManyKeys(f"每人最多 {MAX_ACTIVE} 把, 先吊销不用的")
        key = PREFIX + secrets.token_urlsafe(32)
        rec = ApiKey(id=secrets.token_hex(8), sub=sub, name=name, hint=key[-4:], created_at=int(time.time()))
        with self._conn() as c:
            c.execute("INSERT INTO api_keys (id, key_hash, sub, name, hint, created_at) VALUES (?,?,?,?,?,?)",
                      (rec.id, hash_key(key), rec.sub, rec.name, rec.hint, rec.created_at))
        logger.info("api key created: sub=%s id=%s", rec.sub, rec.id)
        return rec, key

    def list(self, sub: str) -> list[ApiKey]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM api_keys WHERE sub = ? AND revoked_at IS NULL ORDER BY created_at DESC",
                             (sub.strip().lower(),)).fetchall()
        return [self._row(r) for r in rows]

    def revoke(self, sub: str, key_id: str) -> bool:
        with self._conn() as c:
            cur = c.execute("UPDATE api_keys SET revoked_at = ? WHERE id = ? AND sub = ? AND revoked_at IS NULL",
                            (int(time.time()), key_id, sub.strip().lower()))
        ok = cur.rowcount > 0
        if ok:
            logger.info("api key revoked: sub=%s id=%s", sub, key_id)
        return ok

    def lookup(self, key: str) -> Optional[ApiKey]:
        """有效的钥匙 → 记录 (顺手记最后使用时间); 不认识 / 吊销了 → None。"""
        if not key.startswith(PREFIX):
            return None
        with self._conn() as c:
            r = c.execute("SELECT * FROM api_keys WHERE key_hash = ? AND revoked_at IS NULL", (hash_key(key),)).fetchone()
            if r is None:
                return None
            rec = self._row(r)
            now = int(time.time())
            if not rec.last_used_at or now - rec.last_used_at >= _TOUCH_EVERY_SECS:
                c.execute("UPDATE api_keys SET last_used_at = ? WHERE id = ?", (now, rec.id))
                rec.last_used_at = now
        return rec
