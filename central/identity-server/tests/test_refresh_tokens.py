"""RefreshTokenStore 单测 (BL-IDENTITY-REFRESH-TOKEN, 5/15 凌晨).

跑法: cd central/identity-server && PYTHONPATH=src python -m pytest tests/test_refresh_tokens.py -q

覆盖:
  - issue / find / revoke / cleanup_expired
  - rotation chain (parent_token)
  - revoke_all_for_sub (admin 锁 user)
  - 过期判断 + revoked 判断
  - sqlite 持久化 (重新打开 DB 数据还在)
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from catfish_identity.refresh_tokens import (
    RefreshTokenRecord,
    RefreshTokenStore,
)


@pytest.fixture
def store(tmp_path) -> RefreshTokenStore:
    """每个 test 独立 sqlite 文件"""
    db_path = tmp_path / "refresh.db"
    return RefreshTokenStore(db_path=db_path)


# ─── issue + find ──────────────────────────────────────────


def test_issue_creates_record(store):
    rt = store.issue(sub="alice@x.com", client_id="hermes-cli", scope="openid email")
    assert rt.token  # 非空
    assert rt.sub == "alice@x.com"
    assert rt.client_id == "hermes-cli"
    assert rt.scope == "openid email"
    assert rt.parent_token is None  # 第一次签, 没 parent
    assert rt.revoked_at is None  # 新签的没 revoke
    assert rt.expires_at > rt.issued_at  # 30 天 TTL


def test_find_returns_issued(store):
    rt = store.issue(sub="alice@x.com", client_id="hermes-cli", scope="x")
    found = store.find(rt.token)
    assert found is not None
    assert found.token == rt.token
    assert found.sub == "alice@x.com"


def test_find_unknown_returns_none(store):
    assert store.find("nonexistent_token") is None


def test_find_empty_token_returns_none(store):
    assert store.find("") is None


# ─── revoke ─────────────────────────────────────────────────


def test_revoke_marks_token(store):
    rt = store.issue(sub="alice@x.com", client_id="hermes-cli", scope="x")
    assert store.revoke(rt.token) is True
    found = store.find(rt.token)
    assert found.revoked_at is not None
    assert found.is_revoked() is True


def test_revoke_already_revoked_returns_false(store):
    rt = store.issue(sub="alice@x.com", client_id="hermes-cli", scope="x")
    store.revoke(rt.token)
    # 再 revoke 同一个 → False (已 revoke)
    assert store.revoke(rt.token) is False


def test_revoke_unknown_returns_false(store):
    assert store.revoke("nonexistent") is False


def test_revoke_all_for_sub(store):
    """admin 锁 user 时, user 所有活 refresh_token 一并 revoke"""
    rt1 = store.issue(sub="alice@x.com", client_id="hermes-cli", scope="x")
    rt2 = store.issue(sub="alice@x.com", client_id="hermes-cli", scope="x")
    rt3 = store.issue(sub="bob@x.com", client_id="hermes-cli", scope="x")
    n = store.revoke_all_for_sub("alice@x.com")
    assert n == 2
    assert store.find(rt1.token).is_revoked()
    assert store.find(rt2.token).is_revoked()
    assert not store.find(rt3.token).is_revoked()  # bob 没受影响


# ─── rotation chain ───────────────────────────────────────


def test_rotation_chain_via_parent_token(store):
    """新 token 携 parent_token 反查链"""
    rt1 = store.issue(sub="alice@x.com", client_id="hermes-cli", scope="x")
    rt2 = store.issue(sub="alice@x.com", client_id="hermes-cli", scope="x", parent_token=rt1.token)
    assert rt2.parent_token == rt1.token

    found = store.find(rt2.token)
    assert found.parent_token == rt1.token


# ─── 过期 ────────────────────────────────────────────────


def test_is_expired_for_future_token(store):
    rt = store.issue(sub="alice@x.com", client_id="x", scope="x")
    assert rt.is_expired() is False


def test_is_expired_for_past_token():
    """造一个 expires_at 在过去的 record (不通过 store.issue)"""
    record = RefreshTokenRecord(
        token="x", sub="x", client_id="x", scope="x",
        issued_at=int(time.time()) - 100,
        expires_at=int(time.time()) - 10,
    )
    assert record.is_expired() is True


def test_cleanup_expired_keeps_recent(store, monkeypatch):
    """cleanup 留 7 天内的过期 token 给 audit, 删更老的"""
    rt_recent = store.issue(sub="x", client_id="c", scope="s")
    # 手动 update 一条到 7 天前过期 (sqlite 直接改)
    with store._conn() as conn:
        old_expires = int(time.time()) - 8 * 86400
        conn.execute(
            "INSERT INTO refresh_tokens (token, sub, client_id, scope, issued_at, expires_at) "
            "VALUES ('old_token', 'x', 'c', 's', ?, ?)",
            (old_expires - 86400, old_expires),
        )
    n = store.cleanup_expired()
    assert n == 1  # 删了一条
    # 老的不在了
    assert store.find("old_token") is None
    # 新的还在
    assert store.find(rt_recent.token) is not None


# ─── 持久化 ─────────────────────────────────────────────


def test_sqlite_persists_across_instances(tmp_path):
    db_path = tmp_path / "shared.db"
    store1 = RefreshTokenStore(db_path=db_path)
    rt = store1.issue(sub="alice@x.com", client_id="hermes-cli", scope="x")

    # 新 instance 打开同一个文件
    store2 = RefreshTokenStore(db_path=db_path)
    found = store2.find(rt.token)
    assert found is not None
    assert found.sub == "alice@x.com"


def test_len(store):
    assert len(store) == 0
    store.issue(sub="a@x.com", client_id="c", scope="s")
    store.issue(sub="b@x.com", client_id="c", scope="s")
    assert len(store) == 2


# ─── env 配置 ────────────────────────────────────────────


def test_ttl_env_override(tmp_path, monkeypatch):
    """CATFISH_REFRESH_TOKEN_TTL_DAYS env 改默认 30 天"""
    monkeypatch.setenv("CATFISH_REFRESH_TOKEN_TTL_DAYS", "1")  # 改成 1 天
    store = RefreshTokenStore(db_path=tmp_path / "t.db")
    rt = store.issue(sub="x", client_id="c", scope="s")
    diff = rt.expires_at - rt.issued_at
    # 应该 = 1 天 = 86400 秒
    assert 86395 <= diff <= 86405
