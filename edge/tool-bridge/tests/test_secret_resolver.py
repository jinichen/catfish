"""secret_resolver 单测.

覆盖:
  - resolve_secret 各 scheme (env / keychain / wincred)
  - 错误格式 / 空 / 不识别 scheme
  - keychain 平台限制 (非 macOS 报错)
  - is_secret_ref 判定
  - mock subprocess 测 keychain 路径 (找到 / 找不到 / 超时)
"""
from __future__ import annotations

import platform
import subprocess
from unittest import mock

import pytest

from catfish_tool_bridge import secret_resolver


# ============================================================
# resolve_secret 入参校验
# ============================================================


def test_resolve_empty_string() -> None:
    with pytest.raises(secret_resolver.SecretResolveError, match="不能为空"):
        secret_resolver.resolve_secret("")


def test_resolve_no_scheme() -> None:
    with pytest.raises(secret_resolver.SecretResolveError, match="必须是"):
        secret_resolver.resolve_secret("just_a_name")


def test_resolve_empty_name() -> None:
    with pytest.raises(secret_resolver.SecretResolveError, match="name 部分为空"):
        secret_resolver.resolve_secret("env://")


def test_resolve_unknown_scheme() -> None:
    with pytest.raises(secret_resolver.SecretResolveError, match="不支持的 scheme"):
        secret_resolver.resolve_secret("vault://eis_password")


# ============================================================
# env:// scheme
# ============================================================


def test_resolve_env_var_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EIS_PASSWORD", "jiniaA1+")
    val = secret_resolver.resolve_secret("env://EIS_PASSWORD")
    assert val == "jiniaA1+"


def test_resolve_env_var_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NONEXISTENT_KEY", raising=False)
    with pytest.raises(secret_resolver.SecretResolveError, match="没设"):
        secret_resolver.resolve_secret("env://NONEXISTENT_KEY")


def test_resolve_env_var_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EMPTY_VAR", "")
    with pytest.raises(secret_resolver.SecretResolveError, match="空字符串"):
        secret_resolver.resolve_secret("env://EMPTY_VAR")


# ============================================================
# keychain:// scheme (macOS only, mock subprocess)
# ============================================================


@mock.patch.object(secret_resolver, "platform")
@mock.patch.object(secret_resolver, "shutil")
@mock.patch.object(secret_resolver, "subprocess")
def test_keychain_success(
    mock_sub: mock.MagicMock,
    mock_shutil: mock.MagicMock,
    mock_platform: mock.MagicMock,
) -> None:
    """macOS 上 security 命令成功 → 返回密码"""
    mock_platform.system.return_value = "Darwin"
    mock_shutil.which.return_value = "/usr/bin/security"
    mock_sub.run.return_value = mock.MagicMock(
        returncode=0,
        stdout="my_secret_password\n",
        stderr="",
    )
    mock_sub.TimeoutExpired = subprocess.TimeoutExpired

    val = secret_resolver.resolve_secret("keychain://eis_password")
    assert val == "my_secret_password"

    # 验证命令调用对了
    cmd_used = mock_sub.run.call_args[0][0]
    assert cmd_used[0] == "security"
    assert "find-generic-password" in cmd_used
    assert "-s" in cmd_used
    assert "eis_password" in cmd_used
    assert "-w" in cmd_used


@mock.patch.object(secret_resolver, "platform")
def test_keychain_non_macos_rejected(mock_platform: mock.MagicMock) -> None:
    mock_platform.system.return_value = "Linux"
    with pytest.raises(secret_resolver.SecretResolveError, match="只在 macOS 支持"):
        secret_resolver.resolve_secret("keychain://eis_password")


@mock.patch.object(secret_resolver, "platform")
@mock.patch.object(secret_resolver, "shutil")
def test_keychain_no_security_binary(
    mock_shutil: mock.MagicMock,
    mock_platform: mock.MagicMock,
) -> None:
    """macOS 但找不到 security 命令 (系统残缺) → friendly error"""
    mock_platform.system.return_value = "Darwin"
    mock_shutil.which.return_value = None
    with pytest.raises(secret_resolver.SecretResolveError, match="security"):
        secret_resolver.resolve_secret("keychain://eis_password")


@mock.patch.object(secret_resolver, "platform")
@mock.patch.object(secret_resolver, "shutil")
@mock.patch.object(secret_resolver, "subprocess")
def test_keychain_not_found(
    mock_sub: mock.MagicMock,
    mock_shutil: mock.MagicMock,
    mock_platform: mock.MagicMock,
) -> None:
    """keychain 里没这个 entry → returncode=44, friendly error 提示怎么存"""
    mock_platform.system.return_value = "Darwin"
    mock_shutil.which.return_value = "/usr/bin/security"
    mock_sub.run.return_value = mock.MagicMock(
        returncode=44,
        stdout="",
        stderr="security: SecKeychainSearchCopyNext: The specified item could not be found in the keychain.",
    )
    mock_sub.TimeoutExpired = subprocess.TimeoutExpired

    with pytest.raises(secret_resolver.SecretResolveError, match="add-generic-password"):
        secret_resolver.resolve_secret("keychain://eis_password")


@mock.patch.object(secret_resolver, "platform")
@mock.patch.object(secret_resolver, "shutil")
@mock.patch.object(secret_resolver, "subprocess")
def test_keychain_timeout(
    mock_sub: mock.MagicMock,
    mock_shutil: mock.MagicMock,
    mock_platform: mock.MagicMock,
) -> None:
    """security 命令卡住 → friendly timeout error"""
    mock_platform.system.return_value = "Darwin"
    mock_shutil.which.return_value = "/usr/bin/security"
    mock_sub.TimeoutExpired = subprocess.TimeoutExpired
    mock_sub.run.side_effect = subprocess.TimeoutExpired(cmd="security", timeout=5)

    with pytest.raises(secret_resolver.SecretResolveError, match="超时"):
        secret_resolver.resolve_secret("keychain://eis_password")


# ============================================================
# wincred:// scheme (Phase 1 末尾批量做)
# ============================================================


def test_wincred_not_implemented_yet() -> None:
    with pytest.raises(secret_resolver.SecretResolveError, match="还没实现"):
        secret_resolver.resolve_secret("wincred://eis_password")


# ============================================================
# is_secret_ref helper
# ============================================================


def test_is_secret_ref_true_cases() -> None:
    assert secret_resolver.is_secret_ref("env://NAME")
    assert secret_resolver.is_secret_ref("keychain://name")
    assert secret_resolver.is_secret_ref("wincred://name")


def test_is_secret_ref_false_cases() -> None:
    assert not secret_resolver.is_secret_ref("just_a_password")
    assert not secret_resolver.is_secret_ref("")
    assert not secret_resolver.is_secret_ref(None)
    assert not secret_resolver.is_secret_ref(12345)
    assert not secret_resolver.is_secret_ref("http://example.com")
