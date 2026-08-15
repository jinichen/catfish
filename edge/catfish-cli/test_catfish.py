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
# 8/15: catfish.py 从 1875 行拆成 6 个模块。下面这两个不是"新功能", 是原来
# 就在 catfish.py 里的两组函数搬了家。
#
# 为什么测试必须直接 import 它们、而不是继续走 catfish 的 re-export:
# monkeypatch 改的是**某一个模块对象上的绑定**, 而函数体查自由变量查的是
# **定义它的那个模块**的 globals。调用者搬走了, patch 还打在 catfish 上,
# 就成了打空 —— 不报错, 只是测试从此测的是真货 (真连代理端口、真写
# ~/.hermes/config.yaml、真发网络请求)。
import catfish_hermes  # noqa: E402
import catfish_proxy  # noqa: E402


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

    result = catfish_hermes._patch_hermes_config("NEW_TOKEN_xyz")
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

    result = catfish_hermes._patch_hermes_config("NEW")
    assert result == "MyCatfish"


def test_patch_hermes_config_no_file(tmp_path, monkeypatch):
    """hermes config 不存在返 None 不挂"""
    monkeypatch.setenv("HERMES_CONFIG", str(tmp_path / "nope.yaml"))
    assert catfish_hermes._patch_hermes_config("any") is None


def test_patch_hermes_config_no_custom_providers(tmp_path, monkeypatch):
    """hermes config 存在但没 custom_providers 段返 None"""
    yaml = pytest.importorskip("yaml")
    cfg_path = tmp_path / "hermes.yaml"
    cfg_path.write_text(yaml.safe_dump({"display": {"skin": "mono"}}))
    monkeypatch.setenv("HERMES_CONFIG", str(cfg_path))
    assert catfish_hermes._patch_hermes_config("any") is None


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
    assert catfish_hermes._patch_hermes_config("new") is None
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

    result = catfish_hermes._patch_hermes_config("NEW_TOKEN")
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

    result = catfish_hermes._patch_hermes_config("NEW_TOKEN")
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
    result = catfish_hermes._patch_hermes_config("NEW")
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
    result = catfish_hermes._patch_hermes_config("NEW")
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
    monkeypatch.setattr(catfish_hermes, "_patch_hermes_config", lambda token: None)

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


# ─── BL-EDGE-TOOL-KEY (5/24): _patch_hermes_env_file ───────────────


def test_patch_hermes_env_file_creates_new_file(tmp_path, monkeypatch):
    """没有 ~/.hermes/.env → 创建, chmod 600, key 落入."""
    env_path = tmp_path / ".env"
    monkeypatch.setenv("HERMES_DOTENV", str(env_path))

    n = catfish_hermes._patch_hermes_env_file({"TAVILY_API_KEY": "tvly-secret-abc"})
    assert n == 1
    assert env_path.exists()
    body = env_path.read_text(encoding="utf-8")
    assert "TAVILY_API_KEY=tvly-secret-abc" in body
    # 权限 600 (跟 token.json 同标准)
    mode = oct(env_path.stat().st_mode & 0o777)
    assert mode == "0o600", f".env 应 chmod 600, 实际 {mode}"


def test_patch_hermes_env_file_preserves_unrelated_lines(tmp_path, monkeypatch):
    """已有 FIRECRAWL_API_KEY 不动, 注释和空行不动, 仅追加 TAVILY_API_KEY."""
    env_path = tmp_path / ".env"
    env_path.write_text(
        "# 员工手贴的, 不要动\n"
        "FIRECRAWL_API_KEY=fc-old-key\n"
        "\n"
        "# 别的注释\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_DOTENV", str(env_path))

    n = catfish_hermes._patch_hermes_env_file({"TAVILY_API_KEY": "tvly-new"})
    assert n == 1
    body = env_path.read_text(encoding="utf-8")
    # 旧内容全保留
    assert "FIRECRAWL_API_KEY=fc-old-key" in body
    assert "# 员工手贴的, 不要动" in body
    assert "# 别的注释" in body
    # 新 key 追加 (带 catfish marker)
    assert "TAVILY_API_KEY=tvly-new" in body
    assert "catfish-cli 同步" in body


def test_patch_hermes_env_file_updates_existing_key_in_place(tmp_path, monkeypatch):
    """已有 TAVILY_API_KEY (员工自己手贴) → 原位覆盖, 不追加新行."""
    env_path = tmp_path / ".env"
    env_path.write_text(
        "TAVILY_API_KEY=tvly-OLD\n"
        "FIRECRAWL_API_KEY=fc-keep\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_DOTENV", str(env_path))

    n = catfish_hermes._patch_hermes_env_file({"TAVILY_API_KEY": "tvly-NEW"})
    assert n == 1
    body = env_path.read_text(encoding="utf-8")
    assert "TAVILY_API_KEY=tvly-NEW" in body
    assert "TAVILY_API_KEY=tvly-OLD" not in body  # 旧值真覆盖了
    assert "FIRECRAWL_API_KEY=fc-keep" in body
    # 没有重复追加 (只出现 1 次)
    assert body.count("TAVILY_API_KEY=") == 1


def test_patch_hermes_env_file_empty_input_no_op(tmp_path, monkeypatch):
    """env_vars={} → 不创建文件, 返 0."""
    env_path = tmp_path / ".env"
    monkeypatch.setenv("HERMES_DOTENV", str(env_path))
    n = catfish_hermes._patch_hermes_env_file({})
    assert n == 0
    assert not env_path.exists()


def test_patch_hermes_env_file_makes_backup_before_overwrite(tmp_path, monkeypatch):
    """改写前应该 cp 原文件到 .env.bak (跟 _patch_hermes_config 同款防误删)."""
    env_path = tmp_path / ".env"
    env_path.write_text("OLD=value\n", encoding="utf-8")
    monkeypatch.setenv("HERMES_DOTENV", str(env_path))

    catfish_hermes._patch_hermes_env_file({"TAVILY_API_KEY": "tvly-x"})
    backup = env_path.with_suffix(".env.bak")
    assert backup.exists()
    assert "OLD=value" in backup.read_text(encoding="utf-8")


# ─── _patch_hermes_config_yaml_blocks ────────────────────────────


def test_patch_yaml_blocks_creates_new_top_key(tmp_path, monkeypatch):
    """config.yaml 没 'web' 段 → 创建一个 (其他段保留)."""
    yaml = pytest.importorskip("yaml")
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(yaml.safe_dump({
        "model": {"default": "deepseek-flash"},  # 不能动
    }))
    monkeypatch.setenv("HERMES_CONFIG", str(cfg_path))

    n = catfish_hermes._patch_hermes_config_yaml_blocks([{"web": {"backend": "tavily"}}])
    assert n == 1
    new_cfg = yaml.safe_load(cfg_path.read_text())
    assert new_cfg["web"] == {"backend": "tavily"}
    assert new_cfg["model"] == {"default": "deepseek-flash"}  # sibling 保留


def test_patch_yaml_blocks_merges_existing_sub_keys(tmp_path, monkeypatch):
    """已有 web: {search_backend: searxng} → 加 web: {backend: tavily} 应该 merge 不覆盖."""
    yaml = pytest.importorskip("yaml")
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(yaml.safe_dump({
        "web": {"search_backend": "searxng"},
    }))
    monkeypatch.setenv("HERMES_CONFIG", str(cfg_path))

    n = catfish_hermes._patch_hermes_config_yaml_blocks([{"web": {"backend": "tavily"}}])
    assert n == 1
    new_cfg = yaml.safe_load(cfg_path.read_text())
    # 两个 sub-key 都在
    assert new_cfg["web"]["search_backend"] == "searxng"
    assert new_cfg["web"]["backend"] == "tavily"


def test_patch_yaml_blocks_skips_when_no_config_file(tmp_path, monkeypatch):
    """config.yaml 不存在 → 跳过, 返 0 (不抛). .env 那条已经够了, hermes auto-detect."""
    cfg_path = tmp_path / "config.yaml"
    assert not cfg_path.exists()
    monkeypatch.setenv("HERMES_CONFIG", str(cfg_path))

    n = catfish_hermes._patch_hermes_config_yaml_blocks([{"web": {"backend": "tavily"}}])
    assert n == 0


def test_patch_yaml_blocks_empty_input_no_op(tmp_path, monkeypatch):
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text("model: {default: x}\n")
    monkeypatch.setenv("HERMES_CONFIG", str(cfg_path))

    n = catfish_hermes._patch_hermes_config_yaml_blocks([])
    assert n == 0


# ─── _sync_hermes_edge_tool_configs (orchestrator, mock gateway) ───


def test_sync_edge_tool_dedupes_writes_by_group(tmp_path, monkeypatch):
    """3 个 web tool 共 group → fetch 3 次 (RBAC per-tool), 但 .env/yaml 只写 1 份.

    RBAC 必须 per-tool 因为 admin 理论上可以放 web_search 但不放 web_extract
    给某部门, 所以网络得发 3 个 GET. 但写盘是 dedupe by tool_group, 不重复
    写 .env / yaml.
    """
    yaml = pytest.importorskip("yaml")
    env_path = tmp_path / ".env"
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text("model: {default: x}\n")
    monkeypatch.setenv("HERMES_DOTENV", str(env_path))
    monkeypatch.setenv("HERMES_CONFIG", str(cfg_path))

    monkeypatch.setattr(
        catfish_hermes, "_fetch_edge_tool_list",
        lambda url, tok: ["web_search", "web_extract", "web_crawl"],
    )
    call_count = {"n": 0}

    def fake_fetch(url, tok, name):
        call_count["n"] += 1
        return {
            "tool_name": name,
            "tool_group": "web",
            "provider": "tavily",
            "env_vars": {"TAVILY_API_KEY": "tvly-mocked"},
            "yaml_block": {"web": {"backend": "tavily"}},
        }

    monkeypatch.setattr(catfish_hermes, "_fetch_edge_tool_config", fake_fetch)

    env_n, yaml_n = catfish_hermes._sync_hermes_edge_tool_configs("http://gw", "TOKEN")

    # 网络层: 每个 tool 都打过 endpoint (RBAC 必须 per-tool 判)
    assert call_count["n"] == 3
    # 写盘层: env 和 yaml 都只更新一份 (dedupe by tool_group="web")
    assert env_n == 1
    assert yaml_n == 1
    body = env_path.read_text()
    assert body.count("TAVILY_API_KEY=") == 1, ".env 只能有一行 TAVILY_API_KEY"
    assert "TAVILY_API_KEY=tvly-mocked" in body
    new_cfg = yaml.safe_load(cfg_path.read_text())
    assert new_cfg["web"]["backend"] == "tavily"


def test_sync_edge_tool_handles_empty_tool_list(tmp_path, monkeypatch):
    """gateway 返空 list (老版本 404 fallback) → return (0, 0), 不写文件."""
    monkeypatch.setattr(catfish_hermes, "_fetch_edge_tool_list", lambda u, t: [])
    env_n, yaml_n = catfish_hermes._sync_hermes_edge_tool_configs("http://gw", "TOKEN")
    assert (env_n, yaml_n) == (0, 0)


def test_sync_edge_tool_skips_failed_fetch(tmp_path, monkeypatch):
    """某个 tool fetch 失败 (RBAC/503 返 None) → 跳过它, 别的继续."""
    yaml = pytest.importorskip("yaml")
    env_path = tmp_path / ".env"
    monkeypatch.setenv("HERMES_DOTENV", str(env_path))
    # 不设 HERMES_CONFIG → yaml_blocks 那步 skip (返 0)

    monkeypatch.setattr(
        catfish_hermes, "_fetch_edge_tool_list",
        lambda u, t: ["web_search", "image_generate"],
    )

    def fake_fetch(url, tok, name):
        if name == "image_generate":
            return None  # 模拟 RBAC 403 / 503
        return {
            "tool_name": "web_search",
            "tool_group": "web",
            "provider": "tavily",
            "env_vars": {"TAVILY_API_KEY": "tvly-x"},
            "yaml_block": {"web": {"backend": "tavily"}},
        }

    monkeypatch.setattr(catfish_hermes, "_fetch_edge_tool_config", fake_fetch)
    env_n, _ = catfish_hermes._sync_hermes_edge_tool_configs("http://gw", "TOKEN")
    assert env_n == 1
    assert "TAVILY_API_KEY=tvly-x" in env_path.read_text()


# ─── BL-EDGE-TOOL-PROXY (5/25): 死代理检测 + auto restart ──────────
#
# 8/15: 这一段的 `catfish.` 全部改成 `catfish_proxy.`。
#
# 那天 catfish.py 从 1875 行拆开, 这一组 6 个函数整组搬去了 catfish_proxy.py。
# catfish.py 那边有 re-export, 所以 `catfish._handle_proxy_cleanup(...)` **照样
# 调得通** —— 但 `monkeypatch.setattr(catfish, "_check_proxy_alive", ...)` 就
# **打空了**: 函数体里的自由变量在定义它的模块 (catfish_proxy) 的 globals 里查,
# 改 catfish 那个绑定影响不到。
#
# 后果不是红, 是**真去连 127.0.0.1:7890 / 真跑 `hermes gateway restart`**。
# 所以 patch 和调用都得指到 catfish_proxy 上。tests/test_split_layering.py
# 里有一条守卫钉住"这个文件不许再用 catfish.<代理组名字>"。


def test_check_proxy_alive_returns_false_for_empty():
    assert catfish_proxy._check_proxy_alive("") is False
    assert catfish_proxy._check_proxy_alive("   ") is False


def test_check_proxy_alive_returns_false_for_unreachable_port():
    """65000+ 高位端口大概率没人监听 → 死."""
    # 用 127.0.0.1:1 (reserved port, 几乎确定没人监听)
    assert catfish_proxy._check_proxy_alive("http://127.0.0.1:1", timeout=0.5) is False


def test_check_proxy_alive_returns_true_for_listening_port(tmp_path):
    """起一个临时 TCP listener, 验证检测说"活"."""
    import socket
    import threading

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    port = sock.getsockname()[1]

    # 在背景 accept 一下连接然后立刻关 (TCP probe 不读写, accept 一下就够)
    stop = threading.Event()

    def _accept_once():
        sock.settimeout(2.0)
        try:
            conn, _ = sock.accept()
            conn.close()
        except OSError:
            pass

    t = threading.Thread(target=_accept_once, daemon=True)
    t.start()

    try:
        assert catfish_proxy._check_proxy_alive(f"http://127.0.0.1:{port}") is True
    finally:
        stop.set()
        sock.close()


def test_check_proxy_alive_handles_bare_url():
    """没 scheme 也行 — 我们自动加 http://."""
    # 127.0.0.1:1 仍然死, 但应该走 parse 不挂
    assert catfish_proxy._check_proxy_alive("127.0.0.1:1", timeout=0.5) is False


def test_detect_dead_proxy_vars_empty_when_no_env(monkeypatch):
    for v in catfish_proxy._PROXY_ENV_VARS:
        monkeypatch.delenv(v, raising=False)
    assert catfish_proxy._detect_dead_proxy_vars() == []


def test_detect_dead_proxy_vars_finds_dead(monkeypatch):
    """HTTPS_PROXY 指向死端口 → 出现在返回列表里."""
    for v in catfish_proxy._PROXY_ENV_VARS:
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")  # 1 号端口必死

    # 强制 _check_proxy_alive 返 False (单测里不真 TCP probe, 防止真有进程占 1 端口)
    monkeypatch.setattr(catfish_proxy, "_check_proxy_alive", lambda url, **k: False)

    dead = catfish_proxy._detect_dead_proxy_vars()
    assert len(dead) == 1
    assert dead[0] == ("HTTPS_PROXY", "http://127.0.0.1:1")


def test_detect_dead_proxy_vars_dedupes_url_probe(monkeypatch):
    """HTTPS_PROXY 和 https_proxy 指同一 URL → 只 probe 1 次, 但都报死."""
    for v in catfish_proxy._PROXY_ENV_VARS:
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("https_proxy", "http://127.0.0.1:1")  # 同 URL

    probe_calls = []

    def fake_probe(url, **k):
        probe_calls.append(url)
        return False

    monkeypatch.setattr(catfish_proxy, "_check_proxy_alive", fake_probe)

    dead = catfish_proxy._detect_dead_proxy_vars()
    assert len(dead) == 2  # 两个 env var 都出现
    assert len(probe_calls) == 1, "同 URL 只 probe 一次"


def test_detect_dead_proxy_vars_skips_alive(monkeypatch):
    """活代理 → 不进死列表."""
    for v in catfish_proxy._PROXY_ENV_VARS:
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("HTTPS_PROXY", "http://alive-proxy:8888")
    monkeypatch.setattr(catfish_proxy, "_check_proxy_alive", lambda url, **k: True)

    assert catfish_proxy._detect_dead_proxy_vars() == []


def test_build_clean_env_removes_specified_vars(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://x:9")
    monkeypatch.setenv("PATH", "/usr/bin")  # 应保留

    env = catfish_proxy._build_clean_env(["HTTPS_PROXY"])
    assert "HTTPS_PROXY" not in env
    assert env.get("PATH") == "/usr/bin"
    # 不动当前进程
    assert os.environ.get("HTTPS_PROXY") == "http://x:9"


def test_handle_proxy_cleanup_no_dead_proxy_silent(monkeypatch, capsys):
    """无死代理 → 不打字 (避免噪音)."""
    monkeypatch.setattr(catfish_proxy, "_detect_dead_proxy_vars", lambda: [])
    catfish_proxy._handle_proxy_cleanup(auto_restart=False)
    out = capsys.readouterr().out
    assert out == ""


def test_handle_proxy_cleanup_warn_only_when_no_flag(monkeypatch, capsys):
    """死代理 + 无 flag → 警告 + 给手动命令, 不调用 restart."""
    monkeypatch.setattr(
        catfish_proxy, "_detect_dead_proxy_vars",
        lambda: [("HTTPS_PROXY", "http://127.0.0.1:1")],
    )
    called = []
    monkeypatch.setattr(
        catfish_proxy, "_restart_hermes_with_clean_env",
        lambda vars_: called.append(vars_) or 0,
    )

    catfish_proxy._handle_proxy_cleanup(auto_restart=False)

    assert called == [], "没传 flag 不该真调 restart"
    out = capsys.readouterr().out
    assert "检测到死代理" in out
    assert "HTTPS_PROXY=http://127.0.0.1:1" in out
    assert "unset HTTPS_PROXY" in out
    assert "--restart-hermes" in out  # 提示员工可以加 flag


def test_handle_proxy_cleanup_auto_restarts_with_flag(monkeypatch, capsys):
    """死代理 + flag → 自动 restart, 不让员工自己跑."""
    monkeypatch.setattr(
        catfish_proxy, "_detect_dead_proxy_vars",
        lambda: [("HTTPS_PROXY", "http://127.0.0.1:1"),
                 ("HTTP_PROXY", "http://127.0.0.1:1")],
    )
    called = []

    def fake_restart(unset_vars):
        called.append(unset_vars)
        return 0

    monkeypatch.setattr(catfish_proxy, "_restart_hermes_with_clean_env", fake_restart)

    catfish_proxy._handle_proxy_cleanup(auto_restart=True)

    assert len(called) == 1
    # 两个 var 都被 unset (sorted dedupe)
    assert called[0] == ["HTTPS_PROXY", "HTTP_PROXY"] or called[0] == ["HTTP_PROXY", "HTTPS_PROXY"]
    out = capsys.readouterr().out
    assert "自动用 clean env 重启" in out
    assert "✓ hermes 已用 clean env 重启" in out


def test_handle_proxy_cleanup_falls_back_to_manual_on_restart_fail(monkeypatch, capsys):
    """restart 失败 → 退化到打手动命令, 不挂."""
    monkeypatch.setattr(
        catfish_proxy, "_detect_dead_proxy_vars",
        lambda: [("HTTPS_PROXY", "http://127.0.0.1:1")],
    )
    monkeypatch.setattr(catfish_proxy, "_restart_hermes_with_clean_env", lambda v: 127)

    catfish_proxy._handle_proxy_cleanup(auto_restart=True)
    out = capsys.readouterr().out
    assert "hermes restart 失败" in out
    assert "unset HTTPS_PROXY" in out  # 给手动命令
