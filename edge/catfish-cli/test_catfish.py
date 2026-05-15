"""catfish CLI 单测 (BL-CATFISH-LOGIN, 5/14 凌晨).

跑法: cd edge/catfish-cli && python3 -m pytest test_catfish.py -q

不真打 OAuth (那要起 catfish-identity), 只测:
  - TokenStore 序列化 / 反序列化
  - is_expired / expires_in_secs 时间逻辑
  - save_token / load_token / delete_token 文件操作
  - _decode_jwt_payload 解 JWT (不验签)
  - hermes config patcher (yaml 改写)
  - cmd_status / cmd_logout / cmd_token 简单命令
"""
from __future__ import annotations

import base64
import json
import os
import sys
import time
from pathlib import Path

import pytest

# 把 catfish.py 加到 path (单文件结构, 不是 package)
sys.path.insert(0, str(Path(__file__).parent))

import catfish  # noqa: E402


# ─── fixtures ──────────────────────────────────────────────


@pytest.fixture
def tmp_auth_dir(tmp_path, monkeypatch):
    """让 token.json 写到 tmp_path 不污染真用户 ~/.catfish"""
    monkeypatch.setenv("CATFISH_AUTH_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
def fake_token():
    return catfish.TokenStore(
        access_token="eyJfake.token.string",
        expires_at=int(time.time()) + 3600,
        issuer="http://localhost:8998",
        client_id="hermes-cli",
        scope="openid email chat.completions",
        user_email="alice@x.com",
        user_sub="alice@x.com",
        saved_at=int(time.time()),
    )


# ─── TokenStore + storage ────────────────────────────────


def test_token_store_round_trip(fake_token):
    """to_dict → from_dict 双向无损"""
    d = fake_token.to_dict()
    restored = catfish.TokenStore.from_dict(d)
    assert restored.access_token == fake_token.access_token
    assert restored.expires_at == fake_token.expires_at
    assert restored.scope == fake_token.scope
    assert restored.user_email == "alice@x.com"


def test_save_and_load_token(tmp_auth_dir, fake_token):
    """写盘读回内容一致 + chmod 600"""
    catfish.save_token(fake_token)
    p = tmp_auth_dir / "token.json"
    assert p.exists()
    # 权限检查 (只看 user 的 rw 位)
    mode = oct(p.stat().st_mode & 0o777)
    assert mode == "0o600", f"token.json 应该 chmod 600, 实际 {mode}"

    loaded = catfish.load_token()
    assert loaded is not None
    assert loaded.access_token == fake_token.access_token
    assert loaded.user_email == "alice@x.com"


def test_load_no_file(tmp_auth_dir):
    """文件不存在返 None"""
    assert catfish.load_token() is None


def test_load_corrupted_returns_none(tmp_auth_dir):
    """token.json 内容坏 (非 JSON) 返 None 不挂"""
    (tmp_auth_dir / "token.json").write_text("not json {{{")
    assert catfish.load_token() is None


def test_load_missing_required_fields_returns_none(tmp_auth_dir):
    """缺 access_token 这种必填字段返 None"""
    (tmp_auth_dir / "token.json").write_text(json.dumps({"foo": "bar"}))
    assert catfish.load_token() is None


def test_delete_token(tmp_auth_dir, fake_token):
    """logout 删文件返 True, 没文件返 False"""
    assert catfish.delete_token() is False  # 没文件
    catfish.save_token(fake_token)
    assert catfish.delete_token() is True
    assert not (tmp_auth_dir / "token.json").exists()
    assert catfish.delete_token() is False  # 删过了


def test_save_atomic_rename(tmp_auth_dir, fake_token):
    """save_token 用 .tmp 过渡防写一半 — .tmp 不应该残留"""
    catfish.save_token(fake_token)
    tmp = tmp_auth_dir / "token.json.tmp"
    assert not tmp.exists()


# ─── 过期逻辑 ────────────────────────────────────────────


def test_is_expired_future_token(fake_token):
    """1h 后过期的 token, 现在不算过期"""
    assert fake_token.is_expired(buffer=0) is False


def test_is_expired_past_token():
    """已过期的 token (exp 在过去)"""
    t = catfish.TokenStore(
        access_token="x", expires_at=int(time.time()) - 100,
        issuer="x", client_id="x",
    )
    assert t.is_expired(buffer=0) is True


def test_is_expired_with_buffer():
    """buffer=120s, token 60s 后过期 → 视为已过期"""
    t = catfish.TokenStore(
        access_token="x", expires_at=int(time.time()) + 60,
        issuer="x", client_id="x",
    )
    assert t.is_expired(buffer=120) is True
    assert t.is_expired(buffer=30) is False


def test_expires_in_secs():
    t = catfish.TokenStore(
        access_token="x", expires_at=int(time.time()) + 100,
        issuer="x", client_id="x",
    )
    secs = t.expires_in_secs()
    assert 95 <= secs <= 100


# ─── JWT 解 ──────────────────────────────────────────────


def _make_jwt(payload: dict) -> str:
    """造一个未签名 JWT (header.payload.fake_sig) 给 _decode 解"""
    header = base64.urlsafe_b64encode(b'{"alg":"none"}').rstrip(b"=").decode()
    payload_b64 = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    sig = "fake_signature_just_for_tests"
    return f"{header}.{payload_b64}.{sig}"


def test_decode_jwt_payload():
    payload = {"sub": "alice@x.com", "exp": 1234567890, "scope": "openid email"}
    tok = _make_jwt(payload)
    decoded = catfish._decode_jwt_payload(tok)
    assert decoded == payload


def test_decode_jwt_payload_garbage_returns_empty():
    """坏 token 返空 dict 不挂"""
    assert catfish._decode_jwt_payload("garbage") == {}
    assert catfish._decode_jwt_payload("a.b") == {}  # 段数不对
    assert catfish._decode_jwt_payload("a.notbase64@@@.c") == {}


# ─── hermes config patcher ───────────────────────────────


def test_patch_hermes_config_updates_api_key(tmp_path, monkeypatch):
    """hermes config 已有 catfish provider, patcher 更新 api_key + 备份原文件"""
    yaml = pytest.importorskip("yaml")
    cfg_path = tmp_path / "hermes_config.yaml"
    cfg_path.write_text(yaml.safe_dump({
        "custom_providers": {
            "Local (localhost:8999)": {
                "base_url": "http://localhost:8999/v1",
                "api_key": "OLD_TOKEN",
                "model": "catfish-public-deepseek-flash",
            },
        },
        "other_field": "preserved",
    }, allow_unicode=True))
    monkeypatch.setenv("HERMES_CONFIG", str(cfg_path))

    result = catfish._patch_hermes_config("NEW_TOKEN_xyz")
    assert result == "Local (localhost:8999)"

    # 验改了
    new_cfg = yaml.safe_load(cfg_path.read_text())
    assert new_cfg["custom_providers"]["Local (localhost:8999)"]["api_key"] == "NEW_TOKEN_xyz"
    # 别的字段没动
    assert new_cfg["other_field"] == "preserved"
    assert new_cfg["custom_providers"]["Local (localhost:8999)"]["base_url"] == "http://localhost:8999/v1"

    # 备份文件存在
    backup = cfg_path.with_suffix(".yaml.bak")
    assert backup.exists()


def test_patch_hermes_config_finds_by_base_url(tmp_path, monkeypatch):
    """provider name 不匹配 'Local (localhost:8999)' 但 base_url 含 localhost:8999 也能找到"""
    yaml = pytest.importorskip("yaml")
    cfg_path = tmp_path / "hermes_config.yaml"
    cfg_path.write_text(yaml.safe_dump({
        "custom_providers": {
            "MyCatfish": {  # 自定义名
                "base_url": "http://localhost:8999/v1",
                "api_key": "OLD",
            },
        },
    }))
    monkeypatch.setenv("HERMES_CONFIG", str(cfg_path))

    result = catfish._patch_hermes_config("NEW")
    assert result == "MyCatfish"


def test_patch_hermes_config_no_file(tmp_path, monkeypatch):
    """hermes config 不存在返 None 不挂"""
    monkeypatch.setenv("HERMES_CONFIG", str(tmp_path / "nope.yaml"))
    assert catfish._patch_hermes_config("any") is None


def test_patch_hermes_config_no_custom_providers(tmp_path, monkeypatch):
    """hermes config 存在但没 custom_providers 段返 None"""
    yaml = pytest.importorskip("yaml")
    cfg_path = tmp_path / "hermes.yaml"
    cfg_path.write_text(yaml.safe_dump({"display": {"skin": "mono"}}))
    monkeypatch.setenv("HERMES_CONFIG", str(cfg_path))
    assert catfish._patch_hermes_config("any") is None


def test_patch_hermes_config_no_catfish_provider(tmp_path, monkeypatch):
    """有 custom_providers 但没 catfish (没 localhost:8999) 返 None 不动"""
    yaml = pytest.importorskip("yaml")
    cfg_path = tmp_path / "hermes.yaml"
    cfg_path.write_text(yaml.safe_dump({
        "custom_providers": {
            "Other": {"base_url": "https://api.other.com", "api_key": "x"}
        }
    }))
    monkeypatch.setenv("HERMES_CONFIG", str(cfg_path))
    assert catfish._patch_hermes_config("new") is None
    # 验老的也没动
    cfg = yaml.safe_load(cfg_path.read_text())
    assert cfg["custom_providers"]["Other"]["api_key"] == "x"


def test_patch_hermes_config_list_format(tmp_path, monkeypatch):
    """5/15 鸿波端到端测发现 — hermes 0.13 实际是 list-of-dict 格式, 不是 dict"""
    yaml = pytest.importorskip("yaml")
    cfg_path = tmp_path / "hermes.yaml"
    cfg_path.write_text(yaml.safe_dump({
        "custom_providers": [
            {
                "name": "Local (localhost:8999)",
                "base_url": "http://localhost:8999/v1",
                "api_key": "OLD_TOKEN",
                "model": "catfish-public-deepseek-flash",
            },
        ],
        "other_field": "preserved",
    }))
    monkeypatch.setenv("HERMES_CONFIG", str(cfg_path))

    result = catfish._patch_hermes_config("NEW_TOKEN")
    assert result == "Local (localhost:8999)"

    new_cfg = yaml.safe_load(cfg_path.read_text())
    assert isinstance(new_cfg["custom_providers"], list)  # 还是 list
    assert new_cfg["custom_providers"][0]["api_key"] == "NEW_TOKEN"
    assert new_cfg["custom_providers"][0]["base_url"] == "http://localhost:8999/v1"
    assert new_cfg["other_field"] == "preserved"


def test_patch_hermes_config_also_patches_model_api_key(tmp_path, monkeypatch):
    """5/15 早鸿波端到端测发现 — hermes 0.13 active 配置在顶层 'model:' 段, 不只 custom_providers.
    catfish login 必须**同时** patch model.api_key 才真生效, 否则 hermes 用顶层 model.api_key
    (老 token) 调 gateway 401."""
    yaml = pytest.importorskip("yaml")
    cfg_path = tmp_path / "hermes.yaml"
    cfg_path.write_text(yaml.safe_dump({
        "model": {
            "default": "catfish-public-deepseek-flash",
            "provider": "custom",
            "base_url": "http://localhost:8999/v1",
            "api_key": "OLD_MODEL_TOKEN",
        },
        "custom_providers": [
            {
                "name": "Local (localhost:8999)",
                "base_url": "http://localhost:8999/v1",
                "api_key": "OLD_PROVIDER_TOKEN",
                "model": "catfish-public-deepseek-flash",
            },
        ],
    }))
    monkeypatch.setenv("HERMES_CONFIG", str(cfg_path))

    result = catfish._patch_hermes_config("NEW_TOKEN")
    assert result is not None  # 找到 catfish provider
    new_cfg = yaml.safe_load(cfg_path.read_text())
    # 两层都更新
    assert new_cfg["model"]["api_key"] == "NEW_TOKEN", "顶层 model.api_key 必须 patch"
    assert new_cfg["custom_providers"][0]["api_key"] == "NEW_TOKEN", "custom_providers 也 patch"


def test_patch_hermes_config_skips_model_section_for_other_provider(tmp_path, monkeypatch):
    """model.base_url 不指 catfish gateway → 不动 model.api_key (防破坏别的 provider 配置)"""
    yaml = pytest.importorskip("yaml")
    cfg_path = tmp_path / "hermes.yaml"
    cfg_path.write_text(yaml.safe_dump({
        "model": {
            "provider": "openrouter",
            "base_url": "https://openrouter.ai/api/v1",
            "api_key": "SHOULD_NOT_TOUCH",
        },
        "custom_providers": [
            {
                "name": "Local (localhost:8999)",
                "base_url": "http://localhost:8999/v1",
                "api_key": "OLD",
            },
        ],
    }))
    monkeypatch.setenv("HERMES_CONFIG", str(cfg_path))
    result = catfish._patch_hermes_config("NEW")
    assert result is not None
    cfg = yaml.safe_load(cfg_path.read_text())
    assert cfg["model"]["api_key"] == "SHOULD_NOT_TOUCH"  # 没动
    assert cfg["custom_providers"][0]["api_key"] == "NEW"  # patch 了


def test_patch_hermes_config_list_finds_by_base_url(tmp_path, monkeypatch):
    """list 格式下, name 不匹配但 base_url 含 localhost:8999 也能找到"""
    yaml = pytest.importorskip("yaml")
    cfg_path = tmp_path / "hermes.yaml"
    cfg_path.write_text(yaml.safe_dump({
        "custom_providers": [
            {"name": "MyCatfish", "base_url": "http://localhost:8999/v1", "api_key": "OLD"},
            {"name": "Other", "base_url": "https://api.other.com", "api_key": "x"},
        ],
    }))
    monkeypatch.setenv("HERMES_CONFIG", str(cfg_path))
    result = catfish._patch_hermes_config("NEW")
    assert result == "MyCatfish"
    cfg = yaml.safe_load(cfg_path.read_text())
    assert cfg["custom_providers"][0]["api_key"] == "NEW"
    assert cfg["custom_providers"][1]["api_key"] == "x"  # Other 没动


# ─── 命令 (status / logout / token) ─────────────────────


def test_cmd_status_not_logged_in(tmp_auth_dir, capsys):
    rc = catfish.cmd_status(args=None)
    out = capsys.readouterr().out
    assert rc == 1
    assert "未登录" in out


def test_cmd_status_logged_in_valid(tmp_auth_dir, fake_token, capsys):
    catfish.save_token(fake_token)
    rc = catfish.cmd_status(args=None)
    out = capsys.readouterr().out
    assert rc == 0
    assert "已登录" in out
    assert "alice@x.com" in out


def test_cmd_status_logged_in_expired(tmp_auth_dir, capsys):
    """过期 token status 返 1"""
    expired = catfish.TokenStore(
        access_token="x", expires_at=int(time.time()) - 100,
        issuer="x", client_id="hermes-cli",
    )
    catfish.save_token(expired)
    rc = catfish.cmd_status(args=None)
    out = capsys.readouterr().out
    assert rc == 1
    assert "已过期" in out


def test_cmd_logout(tmp_auth_dir, fake_token, capsys):
    catfish.save_token(fake_token)
    rc = catfish.cmd_logout(args=None)
    out = capsys.readouterr().out
    assert rc == 0
    assert "已登出" in out
    assert not (tmp_auth_dir / "token.json").exists()


def test_cmd_logout_when_not_logged_in(tmp_auth_dir, capsys):
    rc = catfish.cmd_logout(args=None)
    out = capsys.readouterr().out
    assert rc == 0  # 不是 error
    assert "本来就没登录" in out


def test_cmd_token_outputs_access_token(tmp_auth_dir, fake_token, capsys):
    catfish.save_token(fake_token)
    rc = catfish.cmd_token(args=None)
    captured = capsys.readouterr()
    assert rc == 0
    assert captured.out.strip() == fake_token.access_token


def test_cmd_token_when_not_logged_in(tmp_auth_dir, capsys):
    rc = catfish.cmd_token(args=None)
    captured = capsys.readouterr()
    assert rc == 1
    assert "未登录" in captured.err


def test_cmd_token_when_expired_no_refresh(tmp_auth_dir, capsys):
    """过期 + 没 refresh_token → 让员工重 login"""
    expired = catfish.TokenStore(
        access_token="x", expires_at=int(time.time()) - 100,
        issuer="x", client_id="hermes-cli",
        # 注意: refresh_token=None
    )
    catfish.save_token(expired)
    rc = catfish.cmd_token(args=None)
    captured = capsys.readouterr()
    assert rc == 1
    assert "过期" in captured.err
    assert "refresh_token" in captured.err or "login" in captured.err


def test_cmd_token_auto_refresh_when_expired(tmp_auth_dir, monkeypatch, capsys):
    """BL-IDENTITY-REFRESH (5/15): 过期 + 有 refresh_token → 自动调 _do_refresh 续"""
    expired = catfish.TokenStore(
        access_token="OLD",
        expires_at=int(time.time()) - 100,
        issuer="http://test:8998",
        client_id="hermes-cli",
        refresh_token="OLD_REFRESH",
        user_email="alice@x.com",
        user_sub="alice@x.com",
    )
    catfish.save_token(expired)

    # mock _do_refresh 返新 token
    refreshed = catfish.TokenStore(
        access_token="NEW_ACCESS_TOKEN",
        expires_at=int(time.time()) + 3600,
        issuer="http://test:8998",
        client_id="hermes-cli",
        refresh_token="NEW_REFRESH",
        user_email="alice@x.com",
        user_sub="alice@x.com",
    )
    monkeypatch.setattr(catfish, "_do_refresh", lambda store: refreshed)
    monkeypatch.setattr(catfish, "_patch_hermes_config", lambda token: None)

    rc = catfish.cmd_token(args=None)
    captured = capsys.readouterr()
    assert rc == 0
    assert captured.out.strip() == "NEW_ACCESS_TOKEN"
    # 也验真存盘了
    new_store = catfish.load_token()
    assert new_store.access_token == "NEW_ACCESS_TOKEN"
    assert new_store.refresh_token == "NEW_REFRESH"


def test_cmd_token_refresh_failure_returns_error(tmp_auth_dir, monkeypatch, capsys):
    """过期 + refresh 失败 (e.g. refresh_token 也过期了) → 返 1, 提示重 login"""
    expired = catfish.TokenStore(
        access_token="OLD",
        expires_at=int(time.time()) - 100,
        issuer="http://test:8998",
        client_id="hermes-cli",
        refresh_token="EXPIRED_REFRESH",
    )
    catfish.save_token(expired)

    def _fail_refresh(store):
        raise RuntimeError("refresh_token 已过期")
    monkeypatch.setattr(catfish, "_do_refresh", _fail_refresh)

    rc = catfish.cmd_token(args=None)
    captured = capsys.readouterr()
    assert rc == 1
    assert "refresh 失败" in captured.err or "login" in captured.err
