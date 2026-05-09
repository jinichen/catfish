"""sqlite 持久化 — Phase 2 (5/9 ship). dev MVP, prod 切 PG (BL-D3 Phase 4).

schema:
    mcp_subscriptions:
        id (str pk, "sub_" + 16 hex)
        user_sub (str, idx)
        connector_id (str, idx)
        status (str: pending_oauth / active / revoked)
        oauth_token_ref (str | null, 指向 secret-broker 的 ref)
        subscribed_at (iso 时间戳)
        oauth_state (str | null, OAuth flow 临时 state, callback 用)
    mcp_audit:
        id (autoincr)
        user_sub (str)
        connector_id (str)
        action (subscribe / unsubscribe / oauth_complete / oauth_failed)
        ts (iso)
        meta (json text, 可选)

dev 默认 ~/.catfish/mcp_registry.db, env CATFISH_MCP_DB_PATH 覆盖.

并发: sqlite3 module 自带连接池 + WAL mode 启 (高并发写不阻塞读). 单测用
:memory: 隔离.
"""
from __future__ import annotations

import contextlib
import json
import logging
import os
import secrets
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("catfish.mcp_registry.db")


def _default_db_path() -> Path:
    env = os.environ.get("CATFISH_MCP_DB_PATH")
    if env:
        if env == ":memory:":
            return Path(":memory:")
        return Path(env).expanduser().resolve()
    return (Path.home() / ".catfish" / "mcp_registry.db").resolve()


_SCHEMA = """
CREATE TABLE IF NOT EXISTS mcp_subscriptions (
    id              TEXT PRIMARY KEY,
    user_sub        TEXT NOT NULL,
    connector_id    TEXT NOT NULL,
    status          TEXT NOT NULL,
    oauth_token_ref TEXT,
    subscribed_at   TEXT NOT NULL,
    oauth_state     TEXT,
    UNIQUE(user_sub, connector_id)
);
CREATE INDEX IF NOT EXISTS idx_sub_user ON mcp_subscriptions(user_sub);
CREATE INDEX IF NOT EXISTS idx_sub_connector ON mcp_subscriptions(connector_id);

CREATE TABLE IF NOT EXISTS mcp_audit (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_sub        TEXT NOT NULL,
    connector_id    TEXT,
    action          TEXT NOT NULL,
    ts              TEXT NOT NULL,
    meta            TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_user ON mcp_audit(user_sub);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON mcp_audit(ts);
"""


class SubscriptionDB:
    """sqlite 持久化层 — 单例, 线程安全 (sqlite3 自带 BEGIN/COMMIT)."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._lock = threading.Lock()
        self._init_schema()

    @contextlib.contextmanager
    def _conn(self):
        conn = sqlite3.connect(
            str(self.db_path),
            check_same_thread=False,
            isolation_level=None,  # autocommit
        )
        conn.row_factory = sqlite3.Row
        # WAL 提升并发 (内存 db 不支持 WAL, 跳过)
        if str(self.db_path) != ":memory:":
            try:
                conn.execute("PRAGMA journal_mode=WAL")
            except sqlite3.OperationalError:
                pass
        try:
            yield conn
        finally:
            conn.close()

    def _init_schema(self) -> None:
        if str(self.db_path) != ":memory:":
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.executescript(_SCHEMA)
        logger.info("mcp_registry db init: %s", self.db_path)

    # ── subscriptions ─────────────────────────────────────────────

    def get_subscription(self, user_sub: str, connector_id: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM mcp_subscriptions WHERE user_sub=? AND connector_id=?",
                (user_sub, connector_id),
            ).fetchone()
            return dict(row) if row else None

    def get_subscription_by_id(self, sub_id: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM mcp_subscriptions WHERE id=?", (sub_id,),
            ).fetchone()
            return dict(row) if row else None

    def list_user_subscriptions(
        self, user_sub: str, status: str | None = None,
    ) -> list[dict[str, Any]]:
        with self._conn() as conn:
            if status:
                rows = conn.execute(
                    "SELECT * FROM mcp_subscriptions "
                    "WHERE user_sub=? AND status=? ORDER BY subscribed_at DESC",
                    (user_sub, status),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM mcp_subscriptions "
                    "WHERE user_sub=? ORDER BY subscribed_at DESC",
                    (user_sub,),
                ).fetchall()
            return [dict(r) for r in rows]

    def count_subscribers(self, connector_id: str) -> int:
        """统计订阅了某 connector 且 active 的员工数, registry 列表展示用."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM mcp_subscriptions "
                "WHERE connector_id=? AND status='active'",
                (connector_id,),
            ).fetchone()
            return int(row["n"]) if row else 0

    def create_subscription(
        self,
        user_sub: str,
        connector_id: str,
        auth_required: bool,
    ) -> dict[str, Any]:
        """创建订阅. 已存在则原值返 (idempotent).

        auth_required 决定初始 status:
          True  → pending_oauth (需走 OAuth flow)
          False → active        (auth_type=none / path_allowlist)
        """
        existing = self.get_subscription(user_sub, connector_id)
        if existing:
            return existing

        sub_id = "sub_" + secrets.token_hex(8)
        oauth_state = secrets.token_urlsafe(24) if auth_required else None
        status = "pending_oauth" if auth_required else "active"
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO mcp_subscriptions"
                "(id, user_sub, connector_id, status, oauth_token_ref, subscribed_at, oauth_state)"
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (sub_id, user_sub, connector_id, status, None, now, oauth_state),
            )
        return self.get_subscription_by_id(sub_id)  # type: ignore[return-value]

    def mark_active(
        self, sub_id: str, oauth_token_ref: str,
    ) -> dict[str, Any] | None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE mcp_subscriptions "
                "SET status='active', oauth_token_ref=?, oauth_state=NULL "
                "WHERE id=?",
                (oauth_token_ref, sub_id),
            )
        return self.get_subscription_by_id(sub_id)

    def revoke(self, sub_id: str) -> bool:
        """unsubscribe — 标记 revoked 留审计, 不真删 (员工再次订阅时复用 id)."""
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE mcp_subscriptions SET status='revoked' WHERE id=?",
                (sub_id,),
            )
            return cur.rowcount > 0

    def find_by_oauth_state(self, state: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM mcp_subscriptions WHERE oauth_state=?", (state,),
            ).fetchone()
            return dict(row) if row else None

    # ── audit ─────────────────────────────────────────────────────

    def write_audit(
        self,
        user_sub: str,
        action: str,
        connector_id: str | None = None,
        meta: dict | None = None,
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO mcp_audit(user_sub, connector_id, action, ts, meta) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    user_sub,
                    connector_id,
                    action,
                    datetime.now(timezone.utc).isoformat(),
                    json.dumps(meta, ensure_ascii=False) if meta else None,
                ),
            )

    def list_audit(self, user_sub: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        with self._conn() as conn:
            if user_sub:
                rows = conn.execute(
                    "SELECT * FROM mcp_audit WHERE user_sub=? ORDER BY ts DESC LIMIT ?",
                    (user_sub, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM mcp_audit ORDER BY ts DESC LIMIT ?", (limit,),
                ).fetchall()
            out = []
            for r in rows:
                d = dict(r)
                if d.get("meta"):
                    try:
                        d["meta"] = json.loads(d["meta"])
                    except json.JSONDecodeError:
                        pass
                out.append(d)
            return out


def make_db(db_path: Path | None = None) -> SubscriptionDB:
    return SubscriptionDB(db_path or _default_db_path())
