"""pytest fixtures (BL-D3 Phase 1+2).

P3.4.1 (6/13 hb): 删 secret_client_mock — 中央不再持 token, app 启动也不再
建 secret_client httpx 客户端.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# 默认 mock 模式 OAuth, 不真接外部 (Phase 2 单测专用)
os.environ.setdefault("CATFISH_MCP_OAUTH_MODE", "mock")

from catfish_mcp_registry.app import app  # noqa: E402
from catfish_mcp_registry.db import SubscriptionDB  # noqa: E402
from catfish_mcp_registry.loader import ManifestRegistry  # noqa: E402


@pytest.fixture
def manifests_dir() -> Path:
    """真 manifests/ 目录 — 让单测跑真 4 个 yaml."""
    return (Path(__file__).resolve().parent.parent / "manifests").resolve()


@pytest.fixture
def registry(manifests_dir: Path) -> ManifestRegistry:
    r = ManifestRegistry(manifests_dir)
    r.load_all()
    return r


@pytest.fixture
def db(tmp_path: Path) -> SubscriptionDB:
    """每测一个独立 sqlite db (单测不依赖 PG, fallback 走 sqlite).

    C5 (6/6 鸿波 CI matrix audit): 原本用 :memory: 不行 — _sqlite_conn 用
    contextmanager `with` 每次创建/关闭 connection, :memory: db 是 per-connection,
    _init_sqlite_schema 建的表在 conn1 关闭后丢, 后续 test 拿新 conn2 看不到表 →
    "no such table" 失败. fix: 用 tmp file (pytest tmp_path), 多 connection 共享.

    集成测试 (真 PG) 在 tests/integration/ 单独跑, 需 docker postgres + alembic.
    """
    return SubscriptionDB(backend="sqlite", sqlite_path=tmp_path / "test.db")


@pytest.fixture
def client(
    registry: ManifestRegistry,
    db: SubscriptionDB,
):
    """FastAPI TestClient — registry/db 都挂 app.state, 跳 lifespan.

    P3.4.1: 不再挂 secret_client (中央已不持员工 token).
    """
    app.state.registry = registry
    app.state.db = db
    return TestClient(app)


@pytest.fixture
def auth_headers() -> dict[str, str]:
    """gateway 注入的员工身份 (单测模拟)."""
    return {
        "X-Catfish-User-Sub": "alice@catfish.dev",
        "X-Catfish-User-Dept": "engineering",
    }
