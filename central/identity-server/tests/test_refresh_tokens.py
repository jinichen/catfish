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


# ─── P3.5.150 grace period ───────────────────────────────


def test_is_in_grace_period_false_when_not_revoked(store):
    """活的 token 不在 grace period 内 (没 revoke 没 grace 概念)"""
    rt = store.issue(sub="x", client_id="c", scope="s")
    assert rt.is_in_grace_period() is False


def test_is_in_grace_period_true_within_window(store):
    """刚 revoke 的 token 仍在 grace period 内"""
    rt = store.issue(sub="x", client_id="c", scope="s")
    store.revoke(rt.token)
    refreshed = store.find(rt.token)
    assert refreshed.is_revoked() is True
    assert refreshed.is_in_grace_period() is True


def test_is_in_grace_period_false_after_window(store):
    """revoke 超过 grace period 的 token 不再在 grace 内"""
    rt = store.issue(sub="x", client_id="c", scope="s")
    # 手动改 revoked_at 到 grace + 10 秒前
    grace_secs = 60  # 默认 GRACE_PERIOD_SECS
    past = int(time.time()) - grace_secs - 10
    with store._conn() as conn:
        conn.execute(
            "UPDATE refresh_tokens SET revoked_at = ? WHERE token = ?",
            (past, rt.token),
        )
    refreshed = store.find(rt.token)
    assert refreshed.is_revoked() is True
    assert refreshed.is_in_grace_period() is False


def test_is_in_grace_period_false_when_grace_zero(store, monkeypatch):
    """env CATFISH_REFRESH_TOKEN_GRACE_SECS=0 关 grace, 回 strict rotation"""
    monkeypatch.setenv("CATFISH_REFRESH_TOKEN_GRACE_SECS", "0")
    rt = store.issue(sub="x", client_id="c", scope="s")
    store.revoke(rt.token)
    refreshed = store.find(rt.token)
    assert refreshed.is_revoked() is True
    # grace = 0, 即使刚 revoked 也不在 grace 内
    assert refreshed.is_in_grace_period() is False


def test_grace_env_override(store, monkeypatch):
    """CATFISH_REFRESH_TOKEN_GRACE_SECS env 覆盖默认 60s"""
    monkeypatch.setenv("CATFISH_REFRESH_TOKEN_GRACE_SECS", "5")
    rt = store.issue(sub="x", client_id="c", scope="s")
    # 手动改 revoked_at 到 6 秒前 (env grace = 5, 已超)
    with store._conn() as conn:
        conn.execute(
            "UPDATE refresh_tokens SET revoked_at = ? WHERE token = ?",
            (int(time.time()) - 6, rt.token),
        )
    refreshed = store.find(rt.token)
    assert refreshed.is_in_grace_period() is False


def test_find_child_returns_child(store):
    """find_child 拿到 rotation 后的 child token"""
    parent = store.issue(sub="x", client_id="c", scope="s")
    child = store.issue(sub="x", client_id="c", scope="s", parent_token=parent.token)
    found = store.find_child(parent.token)
    assert found is not None
    assert found.token == child.token
    assert found.parent_token == parent.token


def test_find_child_returns_none_when_no_child(store):
    """rotation 没发生过, find_child 返 None"""
    parent = store.issue(sub="x", client_id="c", scope="s")
    # parent 没被 rotation 出 child
    assert store.find_child(parent.token) is None


def test_find_child_empty_parent_returns_none(store):
    assert store.find_child("") is None
