"""PG 集成测试 — 五一 sprint 5/4 (BL-D17).

跑法:
    # 鸿波本机 PG:
    export CATFISH_TEST_DB_URL=postgresql://catfish:7954672aA%21@localhost:5432/catfish
    pytest tests/test_pg_integration.py -v

    # 没 PG 全跳过 (CI / 老部署兼容)
    pytest tests/test_pg_integration.py  # 全部 skip

注意: 测试前会 DROP TABLE users / registry_agents / quota_events 重建.
不要跑在生产 PG 上!
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

PG_URL = os.environ.get("CATFISH_TEST_DB_URL", "").strip()
pytestmark = pytest.mark.skipif(
    not PG_URL,
    reason="CATFISH_TEST_DB_URL 未设, 跳过 PG 集成测试 (yaml fallback 路径已在 test_users 覆盖)",
)


@pytest.fixture
async def pg_clean(monkeypatch):
    """每个测试: 设 CATFISH_DB_URL → drop + create schema → 跑测试 → 清理."""
    monkeypatch.setenv("CATFISH_DB_URL", PG_URL)

    # reset module-level pool
    from catfish_identity import db as db_module
    db_module._POOL = None

    from catfish_identity.db import get_pool, init_schema
    pool = await get_pool()
    if pool is None:
        pytest.skip("PG 连接失败, 跳过")

    # 清表 (drop 后 init_schema create)
    async with pool.acquire() as conn:
        await conn.execute("DROP TABLE IF EXISTS users")
        await conn.execute("DROP TABLE IF EXISTS registry_agents")
        await conn.execute("DROP TABLE IF EXISTS quota_events")

    await init_schema()
    yield pool

    # 测试后清干净
    async with pool.acquire() as conn:
        await conn.execute("DROP TABLE IF EXISTS users")
        await conn.execute("DROP TABLE IF EXISTS registry_agents")
        await conn.execute("DROP TABLE IF EXISTS quota_events")

    from catfish_identity.db import close_pool
    await close_pool()


# ── users PG ──────────────────────────────────────────────


async def test_seed_yaml_to_pg(tmp_path: Path, pg_clean) -> None:
    """yaml 有用户 + PG 空 → 自动 seed 到 PG."""
    from catfish_identity.users import UserRegistry, hash_password

    yaml_path = tmp_path / "users.yaml"
    yaml_path.write_text(f"""
users:
  - email: alice@x.com
    password_hash: {hash_password("p")}
    name: Alice
    department: 研发部
    role: manager
    managed_departments:
      - 研发部
""", encoding="utf-8")

    reg = UserRegistry(users_path=yaml_path)
    assert len(reg) == 1  # yaml 加载

    seeded = await reg.seed_pg_from_yaml_if_empty()
    assert seeded == 1

    # 第二次 seed 应该跳过 (PG 已有)
    seeded2 = await reg.seed_pg_from_yaml_if_empty()
    assert seeded2 == 0


async def test_reload_from_pg_overrides_yaml(tmp_path: Path, pg_clean) -> None:
    """PG 有数据 → reload_from_pg 覆盖 yaml 加载的内存 dict."""
    from catfish_identity.users import UserRegistry, hash_password
    import json

    # 1. yaml 一个用户
    yaml_path = tmp_path / "users.yaml"
    yaml_path.write_text(f"""
users:
  - email: alice@x.com
    password_hash: {hash_password("p")}
    name: Alice (yaml)
""", encoding="utf-8")

    reg = UserRegistry(users_path=yaml_path)
    assert reg.find("alice@x.com").name == "Alice (yaml)"

    # 2. PG 直接插一条不同的
    async with pg_clean.acquire() as conn:
        await conn.execute(
            "INSERT INTO users (email, password_hash, name, department, "
            "tier, role, managed_departments) VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb)",
            "bob@x.com", hash_password("p"), "Bob (PG)", "销售部",
            "employee", "employee", json.dumps([]),
        )

    # 3. reload from PG → alice 消失, bob 出现
    ok = await reg.reload_from_pg()
    assert ok
    assert reg.find("alice@x.com") is None
    assert reg.find("bob@x.com").name == "Bob (PG)"


# ── registry_agents PG ────────────────────────────────────


async def test_registry_save_load_pg(pg_clean) -> None:
    """registry register → 写 PG → load 拿回来."""
    from catfish_identity.registry import (
        RegistryEntry,
        _load_registry_async,
        _save_registry_async,
    )

    entries = {
        "alice@x.com": RegistryEntry(
            sub="alice@x.com",
            catfish_endpoint="http://alice:8999",
            jwks_uri="http://alice:8998/jwks",
            public_pem="-----BEGIN PUBLIC KEY-----\nFAKE\n-----END PUBLIC KEY-----\n",
            department="研发部",
            capabilities=["a2a.ask", "a2a.skill_share"],
            last_seen_iso="2026-05-04T10:00:00+00:00",
        ),
    }

    wrote_pg = await _save_registry_async(entries)
    assert wrote_pg, "应该写到 PG, 不是 fallback yaml"

    loaded = await _load_registry_async()
    assert "alice@x.com" in loaded
    assert loaded["alice@x.com"].catfish_endpoint == "http://alice:8999"
    assert loaded["alice@x.com"].department == "研发部"
    assert "a2a.ask" in loaded["alice@x.com"].capabilities
