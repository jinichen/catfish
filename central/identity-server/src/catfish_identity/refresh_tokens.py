"""Refresh token store + grant 实现 (BL-IDENTITY-REFRESH-TOKEN, 5/15 凌晨).

# 目的

access_token TTL 1h, 过期员工要重 catfish login. 不能这么用. RFC 6749 §6
refresh_token grant: client 拿 refresh_token 换新 access_token, 不需要员工重登.

# 存储

sqlite 单文件 (~/.catfish/identity-server/refresh_tokens.db). 用 sqlite 不用 PG 因为:
  - 单 catfish-identity 实例 (不像 gateway 多实例), 没并发问题
  - 数据量小 (= 活跃员工数 × 平均设备数, 几百条)
  - 备份简单 (cp 一文件)
  - 部署简单 (零额外服务依赖)

# Schema

  refresh_tokens (
    token         TEXT PRIMARY KEY,            -- 32 字节随机 hex
    sub           TEXT NOT NULL,               -- user.email 或 client:<id>
    client_id     TEXT NOT NULL,               -- OAuth client (hermes-cli 等)
    scope         TEXT NOT NULL,
    issued_at     INTEGER NOT NULL,            -- unix seconds
    expires_at    INTEGER NOT NULL,            -- unix seconds (issued_at + 30 天)
    revoked_at    INTEGER,                     -- NULL = 活, 非空 = 吊销
    parent_token  TEXT                         -- rotation 时上一个 token (审计/回溯)
  )
  INDEX idx_refresh_sub ON (sub)
  INDEX idx_refresh_client ON (client_id)
  INDEX idx_refresh_expires ON (expires_at)

# Rotation 策略

每次 refresh_token grant:
  1. 验旧 refresh_token (exists / not revoked / not expired)
  2. 标旧 token revoked_at = now (一次性使用)
  3. 签新 access_token + 签新 refresh_token (新 token 跟旧的 chain via parent_token)
  4. 返新 access_token + 新 refresh_token

被回放检测: 旧 token 已 revoked, 攻击者拿旧 token 调 → 拒. 真员工新 token 也跟 chain
对应, 万一员工新 token 也被偷, 我们能从 chain 反查到泄密点 (Phase 2 加 admin 仪表盘).

# 默认 TTL

  refresh_token: 30 天 (跟 GitHub / Google 同量级)
  access_token: 1h (不变, gateway 验签开销大不能太短)

env CATFISH_REFRESH_TOKEN_TTL_DAYS 覆盖.

# 不在 MVP 的 (Phase 2)

- 部门统一吊销 (admin 锁某个 user → 该 user 所有 refresh_token revoke)
- 跨设备列表 (员工看"哪些设备登过")
- 设备 fingerprint 绑定 (refresh_token + 设备指纹双因素)
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

logger = logging.getLogger("catfish.identity.refresh_tokens")


_DEFAULT_TTL_DAYS = 30


# P3.5.150 (6/30 鸿波 catch "用一阵就弹"): rotation race grace period.
# 场景: silent refresh 已 200 OK, 服务端 commit (revoke parent + 写 child),
# response 在 wire 中网络瞬断 / TCP RST / App Nap → Companion 没收到 child →
# 本地仍是 parent token. 下次 silent refresh 用 parent → 老逻辑 400 invalid_grant
# → Companion 删本地 refresh → 弹浏览器.
#
# Grace period 修法 (跟 GitHub / Google 同套路): parent revoked 后 60 秒内, 如果
# Companion retry 用 parent, 服务端查 parent → 找 child → 复用 child (幂等).
# 60 秒覆盖 99% 网络抖动场景. replay attack 风险: 攻击者得在 60 秒内拿到 parent
# + 抢在 Companion retry 前 race, 极小.
#
# env CATFISH_REFRESH_TOKEN_GRACE_SECS 覆盖. 0 = 关 (回 strict rotation).
_DEFAULT_GRACE_PERIOD_SECS = 60


def _ttl_seconds() -> int:
    """从 env 读 TTL 天数, 默认 30 天."""
    days = int(os.environ.get("CATFISH_REFRESH_TOKEN_TTL_DAYS", _DEFAULT_TTL_DAYS))
    return max(1, days) * 86400


def _grace_period_secs() -> int:
    """P3.5.150: rotation race grace period 秒数, 默认 60s. env 覆盖.

    0 = 关 grace, 回 strict rotation (老行为).
    """
    return max(0, int(os.environ.get(
        "CATFISH_REFRESH_TOKEN_GRACE_SECS", _DEFAULT_GRACE_PERIOD_SECS
    )))


def _default_db_path() -> Path:
    """refresh_tokens.db 默认位置. env CATFISH_REFRESH_TOKEN_DB 覆盖."""
    if env := os.environ.get("CATFISH_REFRESH_TOKEN_DB"):
        return Path(env).expanduser()
    home = os.environ.get("HOME") or os.environ.get("USERPROFILE") or "."
    return Path(home) / ".catfish" / "identity-server" / "refresh_tokens.db"


_SCHEMA = """
CREATE TABLE IF NOT EXISTS refresh_tokens (
    token         TEXT PRIMARY KEY,
    sub           TEXT NOT NULL,
    client_id     TEXT NOT NULL,
    scope         TEXT NOT NULL,
    issued_at     INTEGER NOT NULL,
    expires_at    INTEGER NOT NULL,
    revoked_at    INTEGER,
    parent_token  TEXT
);
CREATE INDEX IF NOT EXISTS idx_refresh_sub ON refresh_tokens(sub);
CREATE INDEX IF NOT EXISTS idx_refresh_client ON refresh_tokens(client_id);
CREATE INDEX IF NOT EXISTS idx_refresh_expires ON refresh_tokens(expires_at);
"""


@dataclass
class RefreshTokenRecord:
    token: str
    sub: str
    client_id: str
    scope: str
    issued_at: int
    expires_at: int
    revoked_at: Optional[int] = None
    parent_token: Optional[str] = None

    def is_expired(self) -> bool:
        return time.time() >= self.expires_at

    def is_revoked(self) -> bool:
        return self.revoked_at is not None

    def is_in_grace_period(self) -> bool:
        """P3.5.150: revoked 后 grace period 秒内仍可被 retry 复用 child.

        老 token 的 revoke 时刻 + grace_period > now 才返 True.
        grace = 0 时永远 False (关 grace).
        """
        if self.revoked_at is None:
            return False
        grace = _grace_period_secs()
        if grace <= 0:
            return False
        return (self.revoked_at + grace) > int(time.time())


class RefreshTokenStore:
    """sqlite 后端的 refresh_token 持久化 + rotation 实现.

    线程安全: sqlite3.Connection 默认非线程安全, 我们每次 op 开闭 connection
    (短连接). FastAPI handler 也是 async, 不会有 race.
    """

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self.db_path = db_path or _default_db_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._conn() as conn:
            conn.executescript(_SCHEMA)
        logger.info("refresh_tokens.db 初始化: %s", self.db_path)

    def issue(
        self,
        *,
        sub: str,
        client_id: str,
        scope: str,
        parent_token: Optional[str] = None,
    ) -> RefreshTokenRecord:
        """签新 refresh_token. parent_token 非空 = rotation (从老 token 派生)."""
        token = secrets.token_urlsafe(32)
        now = int(time.time())
        expires_at = now + _ttl_seconds()
        record = RefreshTokenRecord(
            token=token,
            sub=sub,
            client_id=client_id,
            scope=scope,
            issued_at=now,
            expires_at=expires_at,
            parent_token=parent_token,
        )
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO refresh_tokens "
                "(token, sub, client_id, scope, issued_at, expires_at, revoked_at, parent_token) "
                "VALUES (?, ?, ?, ?, ?, ?, NULL, ?)",
                (
                    record.token, record.sub, record.client_id, record.scope,
                    record.issued_at, record.expires_at, record.parent_token,
                ),
            )
        logger.info(
            "refresh_token issued: sub=%s client=%s parent=%s expires=%d",
            sub, client_id, parent_token[:8] + "…" if parent_token else "(none)",
            expires_at,
        )
        return record

    def find(self, token: str) -> Optional[RefreshTokenRecord]:
        if not token:
            return None
        with self._conn() as conn:
            row = conn.execute(
                "SELECT token, sub, client_id, scope, issued_at, expires_at, "
                "revoked_at, parent_token FROM refresh_tokens WHERE token = ?",
                (token,),
            ).fetchone()
        if not row:
            return None
        return RefreshTokenRecord(
            token=row["token"],
            sub=row["sub"],
            client_id=row["client_id"],
            scope=row["scope"],
            issued_at=row["issued_at"],
            expires_at=row["expires_at"],
            revoked_at=row["revoked_at"],
            parent_token=row["parent_token"],
        )

    def find_child(self, parent_token: str) -> Optional[RefreshTokenRecord]:
        """P3.5.150: 查给定 parent_token 通过 rotation 签出的 child token.

        正常 rotation 后一个 parent 对应唯一一条 child (parent 一旦 revoked 就不能
        再 rotation 出第二条). 这条 helper 给 grace period replay 用 — Companion
        网络瞬断 retry 旧 token 时, 服务端拿出 child 重新返给 Companion, 让本地链
        状态跟服务端最终对齐.

        返 None 的两种情况:
        - parent 还没有 rotation 过 (没有任何 token 把它当 parent — 当前 parent
          仍然活着, 不该走 grace 路径)
        - chain 已经断 (child 也被 cleanup_expired 删了, 此时 grace 救不回)
        """
        if not parent_token:
            return None
        with self._conn() as conn:
            row = conn.execute(
                "SELECT token, sub, client_id, scope, issued_at, expires_at, "
                "revoked_at, parent_token FROM refresh_tokens "
                "WHERE parent_token = ? LIMIT 1",
                (parent_token,),
            ).fetchone()
        if not row:
            return None
        return RefreshTokenRecord(
            token=row["token"],
            sub=row["sub"],
            client_id=row["client_id"],
            scope=row["scope"],
            issued_at=row["issued_at"],
            expires_at=row["expires_at"],
            revoked_at=row["revoked_at"],
            parent_token=row["parent_token"],
        )

    def revoke(self, token: str) -> bool:
        """标 token revoked_at = now. 返 True 如果真改了, False 如果 token 不存在."""
        now = int(time.time())
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE refresh_tokens SET revoked_at = ? WHERE token = ? AND revoked_at IS NULL",
                (now, token),
            )
            return cur.rowcount > 0

    def revoke_all_for_sub(self, sub: str) -> int:
        """admin: 锁 user 时把 user 所有活 refresh_token 一并 revoke. 返 revoke 了几条."""
        now = int(time.time())
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE refresh_tokens SET revoked_at = ? WHERE sub = ? AND revoked_at IS NULL",
                (now, sub),
            )
            return cur.rowcount

    def cleanup_expired(self) -> int:
        """删过期 ≥ 7 天的 token (留 7 天给 audit). 返删了几条.

        生产用 cron / launchd 定期跑. dev 不调也无害.
        """
        cutoff = int(time.time()) - 7 * 86400
        with self._conn() as conn:
            cur = conn.execute(
                "DELETE FROM refresh_tokens WHERE expires_at < ?", (cutoff,)
            )
            n = cur.rowcount
        if n:
            logger.info("cleanup_expired: 删了 %d 条 7 天前过期 refresh_token", n)
        return n

    def __len__(self) -> int:
        with self._conn() as conn:
            row = conn.execute("SELECT COUNT(*) FROM refresh_tokens").fetchone()
        return row[0]


__all__ = [
    "RefreshTokenRecord",
    "RefreshTokenStore",
]
