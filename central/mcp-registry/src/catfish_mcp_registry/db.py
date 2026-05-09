"""mcp-registry 数据库 — PostgreSQL 主存储, sqlite fallback (5/9 改).

# 跟 catfish-gateway / catfish-identity 一致

5/9 鸿波: "MCP 也是中央端, 为什么数据库不统一到 PG, 还要自己一套?". 改 — 共享
同一个 PG 实例 + 表前缀防冲突 (跟 gateway 同模式):

    catfish-identity:    users, registry_agents
    catfish-gateway:     quota_events, gateway_audit
    catfish-mcp-registry: mcp_subscriptions, mcp_audit (本模块)

# 切换逻辑 (跟 gateway 同)

读 env `CATFISH_DB_URL` (跟 gateway / identity 共享, 让运维只配一处):
- 有值 (例 `postgresql://catfish:xxx@localhost:5432/catfish`) → 走 PG (asyncpg)
- 无值 → sqlite fallback (~/.catfish/mcp_registry.db) — dev / 单机模式 / 单测

# Schema migration

PG: alembic (跟 gateway 同). 启动时不 CREATE TABLE, 部署前 `alembic upgrade head`.
sqlite fallback: idempotent CREATE TABLE (本模块内嵌, dev 兼容).

# 单测策略

跑单测时不需要真 PG. 默认 sqlite :memory: (CATFISH_DB_URL 未设).
集成测试要 PG 时 docker run postgres + alembic upgrade.
"""
from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import secrets
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("catfish.mcp_registry.db")


# ── 配置 ─────────────────────────────────────────────────────────────


def db_url() -> str | None:
    """env CATFISH_DB_URL → PG, 否则 None (走 sqlite fallback)."""
    return os.environ.get("CATFISH_DB_URL", "").strip() or None


def _default_sqlite_path() -> Path:
    env = os.environ.get("CATFISH_MCP_DB_PATH")
    if env:
        if env == ":memory:":
            return Path(":memory:")
        return Path(env).expanduser().resolve()
    return (Path.home() / ".catfish" / "mcp_registry.db").resolve()


def _mask_url(url: str) -> str:
    return re.sub(r"(://[^:]+:)([^@]+)(@)", r"\1***\3", url)


# ── DDL — 双方言, PG 主, sqlite fallback ─────────────────────────────


# PG schema (alembic 跑这条; 单测不用 PG, 跳过)
_PG_SCHEMA = """
CREATE TABLE IF NOT EXISTS mcp_subscriptions (
    id              TEXT PRIMARY KEY,
    user_sub        TEXT NOT NULL,
    connector_id    TEXT NOT NULL,
    status          TEXT NOT NULL,
    oauth_token_ref TEXT,
    subscribed_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    oauth_state     TEXT,
    UNIQUE(user_sub, connector_id)
);
CREATE INDEX IF NOT EXISTS idx_mcp_sub_user ON mcp_subscriptions(user_sub);
CREATE INDEX IF NOT EXISTS idx_mcp_sub_connector ON mcp_subscriptions(connector_id);

CREATE TABLE IF NOT EXISTS mcp_audit (
    id              BIGSERIAL PRIMARY KEY,
    user_sub        TEXT NOT NULL,
    connector_id    TEXT,
    action          TEXT NOT NULL,
    ts              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    meta            JSONB
);
CREATE INDEX IF NOT EXISTS idx_mcp_audit_user ON mcp_audit(user_sub);
CREATE INDEX IF NOT EXISTS idx_mcp_audit_ts ON mcp_audit(ts DESC);
"""

_SQLITE_SCHEMA = """
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
CREATE INDEX IF NOT EXISTS idx_mcp_sub_user ON mcp_subscriptions(user_sub);
CREATE INDEX IF NOT EXISTS idx_mcp_sub_connector ON mcp_subscriptions(connector_id);

CREATE TABLE IF NOT EXISTS mcp_audit (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_sub        TEXT NOT NULL,
    connector_id    TEXT,
    action          TEXT NOT NULL,
    ts              TEXT NOT NULL,
    meta            TEXT
);
CREATE INDEX IF NOT EXISTS idx_mcp_audit_user ON mcp_audit(user_sub);
CREATE INDEX IF NOT EXISTS idx_mcp_audit_ts ON mcp_audit(ts);
"""


# ── 后端抽象 ────────────────────────────────────────────────────────


class SubscriptionDB:
    """统一接口 — PG (asyncpg 同步包装) + sqlite (兼容).

    选择一个: 启动时根据 CATFISH_DB_URL 决定 backend.
    """

    def __init__(self, *, backend: str, sqlite_path: Path | None = None,
                 pg_url: str | None = None):
        self.backend = backend
        self._lock = threading.Lock()
        if backend == "sqlite":
            assert sqlite_path is not None
            self._sqlite_path = sqlite_path
            self._init_sqlite_schema()
        elif backend == "pg":
            assert pg_url is not None
            self._pg_url = pg_url
            self._init_pg_schema()
        else:
            raise ValueError(f"unknown backend: {backend}")

    # ── sqlite 实现 ─────────────────────────────────────────────────

    @contextlib.contextmanager
    def _sqlite_conn(self):
        conn = sqlite3.connect(
            str(self._sqlite_path),
            check_same_thread=False,
            isolation_level=None,
        )
        conn.row_factory = sqlite3.Row
        if str(self._sqlite_path) != ":memory:":
            try:
                conn.execute("PRAGMA journal_mode=WAL")
            except sqlite3.OperationalError:
                pass
        try:
            yield conn
        finally:
            conn.close()

    def _init_sqlite_schema(self) -> None:
        if str(self._sqlite_path) != ":memory:":
            self._sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        with self._sqlite_conn() as conn:
            conn.executescript(_SQLITE_SCHEMA)
        logger.info("mcp_registry sqlite init: %s", self._sqlite_path)

    def _pg_clean_url(self) -> str:
        """BL-D3 fix4 (5/9): 剥 SQLAlchemy driver prefix 给 psycopg.connect 用.

        env CATFISH_DB_URL 跨 service 共享, alembic (SQLAlchemy) 要的是
        `postgresql+psycopg://...`, 但 psycopg.connect 直接吃不认 driver
        前缀的格式 (它就是 psycopg 自己, 走 libpq 不走 SQLAlchemy 注册表).

        统一逻辑:
          postgresql+psycopg://... → postgresql://...   (SQLAlchemy → libpq)
          postgresql://...         → 原样
          postgres://...           → postgresql://...   (老格式, libpq 兼容)

        这样 alembic env.py 和 db.py 共用同一份 env 变量, 各自做自己的
        归一化, 不互相打架.
        """
        url = self._pg_url
        if url.startswith("postgresql+psycopg://"):
            return "postgresql://" + url[len("postgresql+psycopg://"):]
        if url.startswith("postgres://"):
            return "postgresql://" + url[len("postgres://"):]
        return url

    def _init_pg_schema(self) -> None:
        """PG schema 由 alembic 管. 这里只 ping 一下确认连得上."""
        try:
            import psycopg  # noqa: PLC0415
            with psycopg.connect(self._pg_clean_url(), connect_timeout=5) as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
            logger.info(
                "mcp_registry PG 连通性 ok: %s (schema 由 alembic 管)",
                _mask_url(self._pg_url),
            )
        except Exception as e:
            logger.warning(
                "mcp_registry PG 连通性失败 (%s): %s — 启动可能后续会撞错, "
                "运维确认 alembic upgrade head 跑过 + URL 正确",
                _mask_url(self._pg_url), e,
            )

    @contextlib.contextmanager
    def _pg_conn(self):
        import psycopg  # noqa: PLC0415  延迟 import
        from psycopg.rows import dict_row  # noqa: PLC0415
        # BL-D3 fix4 (5/9): 走 _pg_clean_url 剥 +psycopg driver prefix,
        # psycopg.connect 不认 SQLAlchemy 风格 URL.
        conn = psycopg.connect(
            self._pg_clean_url(), row_factory=dict_row, autocommit=True,
        )
        try:
            yield conn
        finally:
            conn.close()

    def _exec(self, sqlite_sql: str, pg_sql: str | None, params: tuple = ()):
        """统一执行 — sqlite 用 ?, PG 用 %s. 两套 SQL 不一样时分别给."""
        if self.backend == "sqlite":
            with self._sqlite_conn() as conn:
                cur = conn.execute(sqlite_sql, params)
                rows = [dict(r) for r in cur.fetchall()] if cur.description else []
                return rows, cur.rowcount
        else:
            sql = pg_sql if pg_sql else sqlite_sql.replace("?", "%s")
            with self._pg_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, params)
                    rows = list(cur.fetchall()) if cur.description else []
                    return rows, cur.rowcount

    # ── subscriptions ─────────────────────────────────────────────

    def get_subscription(self, user_sub: str, connector_id: str) -> dict[str, Any] | None:
        rows, _ = self._exec(
            "SELECT * FROM mcp_subscriptions WHERE user_sub=? AND connector_id=?",
            None,
            (user_sub, connector_id),
        )
        return self._normalize_sub_row(rows[0]) if rows else None

    def get_subscription_by_id(self, sub_id: str) -> dict[str, Any] | None:
        rows, _ = self._exec(
            "SELECT * FROM mcp_subscriptions WHERE id=?", None, (sub_id,),
        )
        return self._normalize_sub_row(rows[0]) if rows else None

    def list_user_subscriptions(
        self, user_sub: str, status: str | None = None,
    ) -> list[dict[str, Any]]:
        if status:
            rows, _ = self._exec(
                "SELECT * FROM mcp_subscriptions "
                "WHERE user_sub=? AND status=? ORDER BY subscribed_at DESC",
                None,
                (user_sub, status),
            )
        else:
            rows, _ = self._exec(
                "SELECT * FROM mcp_subscriptions "
                "WHERE user_sub=? ORDER BY subscribed_at DESC",
                None,
                (user_sub,),
            )
        return [self._normalize_sub_row(r) for r in rows]

    def count_subscribers(self, connector_id: str) -> int:
        rows, _ = self._exec(
            "SELECT COUNT(*) AS n FROM mcp_subscriptions "
            "WHERE connector_id=? AND status='active'",
            None,
            (connector_id,),
        )
        if not rows:
            return 0
        # PG dict_row + sqlite Row 统一 .get('n')
        r = rows[0]
        n = r.get("n") if isinstance(r, dict) else r["n"]
        return int(n) if n is not None else 0

    def create_subscription(
        self,
        user_sub: str,
        connector_id: str,
        auth_required: bool,
    ) -> dict[str, Any]:
        existing = self.get_subscription(user_sub, connector_id)
        if existing:
            return existing

        sub_id = "sub_" + secrets.token_hex(8)
        oauth_state = secrets.token_urlsafe(24) if auth_required else None
        status = "pending_oauth" if auth_required else "active"
        now = datetime.now(timezone.utc).isoformat()
        if self.backend == "sqlite":
            self._exec(
                "INSERT INTO mcp_subscriptions"
                "(id, user_sub, connector_id, status, oauth_token_ref, subscribed_at, oauth_state)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                None,
                (sub_id, user_sub, connector_id, status, None, now, oauth_state),
            )
        else:
            # PG: subscribed_at 用默认 NOW(), 不显式传
            self._exec(
                "",  # sqlite 形态没用
                "INSERT INTO mcp_subscriptions"
                "(id, user_sub, connector_id, status, oauth_token_ref, oauth_state)"
                " VALUES (%s, %s, %s, %s, %s, %s)",
                (sub_id, user_sub, connector_id, status, None, oauth_state),
            )
        return self.get_subscription_by_id(sub_id)  # type: ignore[return-value]

    def mark_active(
        self, sub_id: str, oauth_token_ref: str,
    ) -> dict[str, Any] | None:
        self._exec(
            "UPDATE mcp_subscriptions "
            "SET status='active', oauth_token_ref=?, oauth_state=NULL "
            "WHERE id=?",
            None,
            (oauth_token_ref, sub_id),
        )
        return self.get_subscription_by_id(sub_id)

    def revoke(self, sub_id: str) -> bool:
        _, rowcount = self._exec(
            "UPDATE mcp_subscriptions SET status='revoked' WHERE id=?",
            None,
            (sub_id,),
        )
        return rowcount > 0

    def find_by_oauth_state(self, state: str) -> dict[str, Any] | None:
        rows, _ = self._exec(
            "SELECT * FROM mcp_subscriptions WHERE oauth_state=?",
            None,
            (state,),
        )
        return self._normalize_sub_row(rows[0]) if rows else None

    # ── audit ─────────────────────────────────────────────────────

    def write_audit(
        self,
        user_sub: str,
        action: str,
        connector_id: str | None = None,
        meta: dict | None = None,
    ) -> None:
        if self.backend == "sqlite":
            self._exec(
                "INSERT INTO mcp_audit(user_sub, connector_id, action, ts, meta) "
                "VALUES (?, ?, ?, ?, ?)",
                None,
                (
                    user_sub,
                    connector_id,
                    action,
                    datetime.now(timezone.utc).isoformat(),
                    json.dumps(meta, ensure_ascii=False) if meta else None,
                ),
            )
        else:
            # PG: ts 用 NOW(), meta 直存 JSONB (psycopg 自动 dict→jsonb)
            self._exec(
                "",
                "INSERT INTO mcp_audit(user_sub, connector_id, action, meta) "
                "VALUES (%s, %s, %s, %s::jsonb)",
                (
                    user_sub,
                    connector_id,
                    action,
                    json.dumps(meta, ensure_ascii=False) if meta else None,
                ),
            )

    def list_audit(self, user_sub: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        if user_sub:
            rows, _ = self._exec(
                "SELECT * FROM mcp_audit WHERE user_sub=? ORDER BY ts DESC LIMIT ?",
                None,
                (user_sub, limit),
            )
        else:
            rows, _ = self._exec(
                "SELECT * FROM mcp_audit ORDER BY ts DESC LIMIT ?",
                None,
                (limit,),
            )
        return [self._normalize_audit_row(r) for r in rows]

    # ── normalize: PG/sqlite 字段类型差异 → 统一字典 ────────────────

    def _normalize_sub_row(self, row: Any) -> dict[str, Any]:
        d = dict(row) if not isinstance(row, dict) else row
        # PG 的 subscribed_at 是 datetime, sqlite 是 str — 统一 iso str
        ts = d.get("subscribed_at")
        if ts and not isinstance(ts, str):
            d["subscribed_at"] = ts.isoformat()
        return d

    def _normalize_audit_row(self, row: Any) -> dict[str, Any]:
        d = dict(row) if not isinstance(row, dict) else row
        ts = d.get("ts")
        if ts and not isinstance(ts, str):
            d["ts"] = ts.isoformat()
        # sqlite meta 是 json text, 解一下; PG 已经是 dict (jsonb)
        meta = d.get("meta")
        if isinstance(meta, str):
            try:
                d["meta"] = json.loads(meta)
            except json.JSONDecodeError:
                pass
        return d


def make_db(sqlite_path: Path | None = None) -> SubscriptionDB:
    """工厂 — 看 CATFISH_DB_URL 选 backend.

    sqlite_path 仅 sqlite fallback 时用 (单测传 :memory:).
    """
    pg_url = db_url()
    if pg_url:
        logger.info("mcp_registry: 使用 PG backend (%s)", _mask_url(pg_url))
        return SubscriptionDB(backend="pg", pg_url=pg_url)
    sqlite_path = sqlite_path or _default_sqlite_path()
    logger.info("mcp_registry: 使用 sqlite fallback (%s, dev mode)", sqlite_path)
    return SubscriptionDB(backend="sqlite", sqlite_path=sqlite_path)
