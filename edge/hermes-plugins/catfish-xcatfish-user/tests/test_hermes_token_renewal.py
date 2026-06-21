"""P3.5.44 — hermes_token_renewal 单测.

跑法 (从 edge/hermes-plugins/catfish-xcatfish-user):
  python3 -m pytest tests/test_hermes_token_renewal.py -q
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
import time
from pathlib import Path
from unittest import mock

import pytest

# 加载 plugin dir 进 sys.path (跟 memory_enforce test 同款)
PLUGIN_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLUGIN_DIR))

import hermes_token_renewal as renewal  # noqa: E402


# ── decode_jwt_exp ───────────────────────────────────────────────


def _make_jwt(payload: dict) -> str:
    """造一个不验签的 JWT (header.payload.signature, signature 随意)."""
    header = base64.urlsafe_b64encode(b'{"alg":"none","typ":"JWT"}').decode().rstrip("=")
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    sig = "fake-sig"
    return f"{header}.{body}.{sig}"


def test_decode_jwt_exp_extracts_int():
    token = _make_jwt({"sub": "client:hermes-cli", "exp": 1781760061, "iat": 1779168061})
    assert renewal.decode_jwt_exp(token) == 1781760061


def test_decode_jwt_exp_handles_float():
    token = _make_jwt({"exp": 1781760061.5})
    assert renewal.decode_jwt_exp(token) == 1781760061


def test_decode_jwt_exp_returns_none_for_garbage():
    assert renewal.decode_jwt_exp("") is None
    assert renewal.decode_jwt_exp("not-a-jwt") is None
    assert renewal.decode_jwt_exp("only.one") is None
    assert renewal.decode_jwt_exp("a.bad-base64!.c") is None


def test_decode_jwt_exp_returns_none_when_no_exp_field():
    token = _make_jwt({"sub": "x", "iat": 123})
    assert renewal.decode_jwt_exp(token) is None


# ── should_renew ─────────────────────────────────────────────────


def test_should_renew_true_when_expired():
    token = _make_jwt({"exp": int(time.time()) - 100})  # 100 秒前过期
    assert renewal.should_renew(token) is True


def test_should_renew_true_when_almost_expired():
    """剩余 < 5 天 (default threshold) 该续."""
    token = _make_jwt({"exp": int(time.time()) + 4 * 86400})  # 4 天
    assert renewal.should_renew(token) is True


def test_should_renew_false_when_plenty_of_time():
    token = _make_jwt({"exp": int(time.time()) + 25 * 86400})  # 25 天
    assert renewal.should_renew(token) is False


def test_should_renew_true_for_non_jwt():
    """保险: 不是 JWT 的不认, 强制 renew (符合 should_renew 文档约定)."""
    assert renewal.should_renew("static-token-not-jwt") is True


def test_should_renew_custom_threshold():
    """1 天 threshold, 剩 12 小时该 renew."""
    token = _make_jwt({"exp": int(time.time()) + 12 * 3600})
    assert renewal.should_renew(token, threshold_secs=86400) is True


# ── persist_to_env_file ──────────────────────────────────────────


def test_persist_creates_file_when_missing(tmp_path: Path):
    env_path = tmp_path / ".env"
    assert renewal.persist_to_env_file(env_path, "HERMES_SERVICE_TOKEN", "new-val") is True
    text = env_path.read_text(encoding="utf-8")
    assert "HERMES_SERVICE_TOKEN=new-val" in text


def test_persist_updates_existing_key(tmp_path: Path):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "FOO=bar\nHERMES_SERVICE_TOKEN=old-jwt\nBAZ=qux\n", encoding="utf-8",
    )
    assert renewal.persist_to_env_file(env_path, "HERMES_SERVICE_TOKEN", "new-jwt") is True
    text = env_path.read_text(encoding="utf-8")
    assert "HERMES_SERVICE_TOKEN=new-jwt" in text
    assert "HERMES_SERVICE_TOKEN=old-jwt" not in text
    assert "FOO=bar" in text
    assert "BAZ=qux" in text


def test_persist_preserves_comments(tmp_path: Path):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "# 重要注释\n# 不该被动\nHERMES_SERVICE_TOKEN=old\n", encoding="utf-8",
    )
    renewal.persist_to_env_file(env_path, "HERMES_SERVICE_TOKEN", "new")
    text = env_path.read_text(encoding="utf-8")
    assert "# 重要注释" in text
    assert "# 不该被动" in text


def test_persist_handles_export_prefix(tmp_path: Path):
    env_path = tmp_path / ".env"
    env_path.write_text("export HERMES_SERVICE_TOKEN=old\n", encoding="utf-8")
    renewal.persist_to_env_file(env_path, "HERMES_SERVICE_TOKEN", "new")
    text = env_path.read_text(encoding="utf-8")
    assert "HERMES_SERVICE_TOKEN=new" in text


def test_persist_appends_when_key_missing(tmp_path: Path):
    env_path = tmp_path / ".env"
    env_path.write_text("FOO=bar\n", encoding="utf-8")
    renewal.persist_to_env_file(env_path, "HERMES_SERVICE_TOKEN", "new")
    text = env_path.read_text(encoding="utf-8")
    assert "FOO=bar" in text
    assert "HERMES_SERVICE_TOKEN=new" in text


def test_persist_atomic_file_perms(tmp_path: Path):
    """写完 mode 0600 (token 是 secret)."""
    env_path = tmp_path / ".env"
    renewal.persist_to_env_file(env_path, "HERMES_SERVICE_TOKEN", "x")
    mode = env_path.stat().st_mode & 0o777
    assert mode == 0o600


# ── get_fresh_service_token ──────────────────────────────────────


@pytest.fixture
def reset_env(monkeypatch):
    """每个测重置 env + token state."""
    monkeypatch.delenv("HERMES_SERVICE_TOKEN", raising=False)
    monkeypatch.delenv("CATFISH_HERMES_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("HERMES_ENV_PATH", raising=False)
    # 重置 lock (每个测全新)
    renewal._RENEW_LOCK = None


@pytest.fixture
def event_loop():
    """pytest-asyncio fallback: 老风格 event_loop fixture."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


def _run(coro):
    """方便老 sync test 调 async helper."""
    return asyncio.get_event_loop().run_until_complete(coro)


def test_get_fresh_returns_none_when_no_token(reset_env):
    """env 完全没设 → None (caller fail-silent)."""
    result = _run(renewal.get_fresh_service_token())
    assert result is None


def test_get_fresh_returns_current_when_fresh(reset_env, monkeypatch):
    """token 还剩 > 5 天 → 直接返, 不 mint."""
    fresh_token = _make_jwt({"exp": int(time.time()) + 25 * 86400})
    monkeypatch.setenv("HERMES_SERVICE_TOKEN", fresh_token)
    # 即使配了 secret 也不该调 mint
    monkeypatch.setenv("CATFISH_HERMES_CLIENT_SECRET", "secret-xyz")

    called = {}

    async def _spy(*a, **kw):
        called["mint"] = True
        return "should-not-be-returned"
    monkeypatch.setattr(renewal, "mint_service_token", _spy)

    result = _run(renewal.get_fresh_service_token())
    assert result == fresh_token
    assert "mint" not in called


def test_get_fresh_mints_when_almost_expired(reset_env, monkeypatch, tmp_path):
    """剩 < 5 天 + 有 secret → 调 mint, 写盘 + env."""
    old_token = _make_jwt({"exp": int(time.time()) + 1 * 86400})  # 剩 1 天
    monkeypatch.setenv("HERMES_SERVICE_TOKEN", old_token)
    monkeypatch.setenv("CATFISH_HERMES_CLIENT_SECRET", "secret-xyz")
    env_file = tmp_path / "hermes.env"
    env_file.write_text(f"HERMES_SERVICE_TOKEN={old_token}\n", encoding="utf-8")
    monkeypatch.setenv("HERMES_ENV_PATH", str(env_file))

    new_token = _make_jwt({"exp": int(time.time()) + 30 * 86400})

    async def _mint(identity_url, client_id, client_secret, timeout_secs=8.0):
        assert client_secret == "secret-xyz"
        return new_token
    monkeypatch.setattr(renewal, "mint_service_token", _mint)

    result = _run(renewal.get_fresh_service_token())
    assert result == new_token
    # env 已更新
    assert os.environ["HERMES_SERVICE_TOKEN"] == new_token
    # 盘也写了
    assert f"HERMES_SERVICE_TOKEN={new_token}" in env_file.read_text()


def test_get_fresh_falls_back_to_old_when_mint_fails(reset_env, monkeypatch):
    """mint 失败 (网络挂 / IdP 不可达) → 返当前 token (即使快过期)."""
    old_token = _make_jwt({"exp": int(time.time()) + 1 * 86400})
    monkeypatch.setenv("HERMES_SERVICE_TOKEN", old_token)
    monkeypatch.setenv("CATFISH_HERMES_CLIENT_SECRET", "secret-xyz")

    async def _mint_fails(*a, **kw):
        return None
    monkeypatch.setattr(renewal, "mint_service_token", _mint_fails)

    result = _run(renewal.get_fresh_service_token())
    assert result == old_token  # fallback


def test_get_fresh_no_secret_no_mint(reset_env, monkeypatch, caplog):
    """快过期但 CLIENT_SECRET 没设 → warn + 返当前 token, 不调 mint."""
    old_token = _make_jwt({"exp": int(time.time()) + 1 * 86400})
    monkeypatch.setenv("HERMES_SERVICE_TOKEN", old_token)
    # 不设 CATFISH_HERMES_CLIENT_SECRET

    called = {}

    async def _spy(*a, **kw):
        called["mint"] = True
        return "x"
    monkeypatch.setattr(renewal, "mint_service_token", _spy)

    with caplog.at_level("WARNING"):
        result = _run(renewal.get_fresh_service_token())
    assert result == old_token
    assert "mint" not in called  # 没调 mint
    # warn 信息提示员工配 secret
    assert any("CATFISH_HERMES_CLIENT_SECRET" in m for m in caplog.messages)


# ── 并发: 多 task 同时进, 只 mint 一次 ────────────────────────────


def test_get_fresh_concurrent_mints_only_once(reset_env, monkeypatch, tmp_path):
    """3 个并发 task 同时进 get_fresh, 应该只有 1 次真 mint (锁保护)."""
    old_token = _make_jwt({"exp": int(time.time()) + 1 * 86400})
    new_token = _make_jwt({"exp": int(time.time()) + 30 * 86400})
    monkeypatch.setenv("HERMES_SERVICE_TOKEN", old_token)
    monkeypatch.setenv("CATFISH_HERMES_CLIENT_SECRET", "s")
    env_file = tmp_path / "e.env"
    env_file.write_text(f"HERMES_SERVICE_TOKEN={old_token}\n", encoding="utf-8")
    monkeypatch.setenv("HERMES_ENV_PATH", str(env_file))

    mint_count = {"n": 0}

    async def _mint(*a, **kw):
        mint_count["n"] += 1
        await asyncio.sleep(0.05)  # 模拟 HTTP 延时, 给其他 task 机会进锁
        return new_token
    monkeypatch.setattr(renewal, "mint_service_token", _mint)

    async def _run_three():
        return await asyncio.gather(
            renewal.get_fresh_service_token(),
            renewal.get_fresh_service_token(),
            renewal.get_fresh_service_token(),
        )

    results = _run(_run_three())
    assert all(r == new_token for r in results)
    assert mint_count["n"] == 1  # 只 mint 一次
