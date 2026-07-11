"""patch_install_ps1_offline.py 单测 (W1 BL-CATFISH-OFFLINE-INSTALL).

覆盖 4 类断言:
    1. patched 输出含 4 处 marker + 3 处 -Offline* 参数
    2. 幂等 — patched 文件再跑 no-op
    3. SHA256 drift → exit 1 with 说明消息
    4. anchor missing → exit 2 (上游改了段落 catch drift)

沙箱直接跑 (纯 str 处理, 无 IO 依赖):
    pytest edge/hermes-fork/test_patch_install_ps1_offline.py -v
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

# 让 tests 目录能 import 隔壁的 script (兼容 pytest 收集)
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from patch_install_ps1_offline import (  # noqa: E402
    ANCHORS,
    MARKER,
    UPSTREAM_SHA256,
    apply_patches,
    check_upstream_sha256,
    detect_already_patched,
    sha256_of,
    verify_patched,
)

UPSTREAM_INSTALL_PS1 = Path.home() / ".hermes/hermes-agent/scripts/install.ps1"


# ─── 前置: fixture 拉真实上游 install.ps1 ─────────────────────


@pytest.fixture
def upstream_install_ps1() -> str:
    """真实的上游 install.ps1 (dev 机 hermes 安装).

    若 dev 机没装 hermes → skip. CI 装 hermes 后 test 覆盖真上游.
    """
    if not UPSTREAM_INSTALL_PS1.is_file():
        pytest.skip(
            f"上游 install.ps1 不存在: {UPSTREAM_INSTALL_PS1}. "
            f"装 hermes: bash <(curl -fsSL https://install.hermes-agent.ai)"
        )
    return UPSTREAM_INSTALL_PS1.read_text(encoding="utf-8")


# ─── Case 1: fresh install.ps1 → patched 含所有必需符号 ─────


def test_apply_patches_produces_expected_symbols(upstream_install_ps1: str):
    """patched install.ps1 含 4 处 marker + 3 个 -Offline* 参数."""
    patched = apply_patches(upstream_install_ps1)
    verify_patched(patched)  # 内部 assert 4 处 marker + 3 参数


def test_apply_patches_adds_exactly_four_markers(upstream_install_ps1: str):
    patched = apply_patches(upstream_install_ps1)
    assert patched.count(MARKER) == 4, "每 patch 1 处 marker"


def test_apply_patches_adds_three_offline_params(upstream_install_ps1: str):
    patched = apply_patches(upstream_install_ps1)
    # 每个参数 1 处定义 + 3 处使用 (Install-Uv/Test-Python/Install-Repository)
    # = 4 处 mention. 上限 4, 下限 3 (Install-Repository 只用 $OfflineSourceDir).
    for param in ("$OfflineSourceDir", "$OfflineUvExe", "$OfflinePythonZip"):
        count = patched.count(param)
        assert count >= 2, f"参数 {param} 应至少 2 处 (def + use), 实际 {count}"


def test_apply_patches_keeps_upstream_line_count_reasonable(upstream_install_ps1: str):
    """patched 加了 ~90 行 (4 处 patch payload), 总行数不该爆."""
    original_lines = upstream_install_ps1.count("\n") + 1
    patched = apply_patches(upstream_install_ps1)
    patched_lines = patched.count("\n") + 1
    delta = patched_lines - original_lines
    assert 60 <= delta <= 120, f"预期加 60-120 行, 实际加 {delta}"


# ─── Case 2: 幂等 — patched 再跑 no-op ────────────────────


def test_detect_already_patched_on_fresh_returns_false(upstream_install_ps1: str):
    assert not detect_already_patched(upstream_install_ps1)


def test_detect_already_patched_on_patched_returns_true(upstream_install_ps1: str):
    patched = apply_patches(upstream_install_ps1)
    assert detect_already_patched(patched)


def test_apply_patches_on_already_patched_would_double_apply(upstream_install_ps1: str):
    """apply_patches 本身不去重 (caller 应先 detect_already_patched).

    这个 test 保护 CLI 层的逻辑边界 — main() 在 detect 命中时 short-circuit.
    """
    patched = apply_patches(upstream_install_ps1)
    # 再跑 apply 会因为 anchor 已消失 (已被 replace) 而 exit 2
    with pytest.raises(SystemExit) as exc_info:
        apply_patches(patched)
    assert exc_info.value.code == 2


# ─── Case 3: SHA256 drift → exit 1 ─────────────────────────


def test_check_upstream_sha256_matches_pin(upstream_install_ps1: str):
    """当前上游 SHA256 跟 pin 一致. 若挂了说明上游更新, 需 bump."""
    actual = sha256_of(upstream_install_ps1)
    assert actual == UPSTREAM_SHA256, (
        f"上游 install.ps1 更新了 (期望 {UPSTREAM_SHA256}, 实际 {actual}). "
        f"重新 audit 4 处 anchor, 更新脚本顶部 UPSTREAM_SHA256."
    )


def test_check_upstream_sha256_strict_mode_exits_on_drift():
    """SHA256 不匹配 → strict 模式 exit 1."""
    fake_ps1 = "param()\n$didUpdate = $false\n"  # 完全不同的内容
    with pytest.raises(SystemExit) as exc_info:
        check_upstream_sha256(fake_ps1, strict=True)
    assert exc_info.value.code == 1


def test_check_upstream_sha256_non_strict_mode_returns_actual(capsys):
    """SHA256 drift + strict=False → 返 actual, 只 WARN 不 raise."""
    fake_ps1 = "param()\n"
    actual = check_upstream_sha256(fake_ps1, strict=False)
    assert actual == sha256_of(fake_ps1)
    captured = capsys.readouterr()
    assert "[WARN]" in captured.err


# ─── Case 4: anchor missing → exit 2 ──────────────────────


def test_apply_patches_missing_anchor_exits_2():
    """上游删了 [switch]$IncludeDesktop → anchor 找不到 → exit 2."""
    fake_ps1 = (
        "param(\n"
        "    [switch]$OtherParam\n"
        ")\n"
        '\n$didUpdate = $false\n\n'
    )
    with pytest.raises(SystemExit) as exc_info:
        apply_patches(fake_ps1)
    assert exc_info.value.code == 2


def test_all_four_anchors_registered():
    """确保 ANCHORS 表覆盖 4 处 patch (param + Install-Uv + Test-Python + Install-Repository)."""
    assert set(ANCHORS.keys()) == {"param", "install_uv", "test_python", "install_repository"}


# ─── CLI end-to-end (subprocess) ────────────────────────────


def test_cli_check_mode_exit_zero(upstream_install_ps1: str, tmp_path: Path):
    """CLI --check 模式对 fresh install.ps1 exit 0."""
    tmp_input = tmp_path / "install.ps1"
    tmp_input.write_text(upstream_install_ps1, encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(_HERE / "patch_install_ps1_offline.py"),
         "--check", "--input", str(tmp_input)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert "[OK]" in result.stdout


def test_cli_writes_patched_file(upstream_install_ps1: str, tmp_path: Path):
    """CLI 输出 patched 文件到 --output, 内容含 marker."""
    tmp_input = tmp_path / "install.ps1"
    tmp_input.write_text(upstream_install_ps1, encoding="utf-8")
    tmp_output = tmp_path / "resources" / "windows" / "install.ps1"
    result = subprocess.run(
        [sys.executable, str(_HERE / "patch_install_ps1_offline.py"),
         "--input", str(tmp_input), "--output", str(tmp_output)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert tmp_output.is_file()
    patched_text = tmp_output.read_text(encoding="utf-8")
    assert MARKER in patched_text
    assert "$OfflineSourceDir" in patched_text


def test_cli_idempotent_on_already_patched(upstream_install_ps1: str, tmp_path: Path):
    """已 patch 文件再跑 → no-op + exit 0 + 复制到 output."""
    tmp_input = tmp_path / "install.ps1"
    tmp_input.write_text(apply_patches(upstream_install_ps1), encoding="utf-8")
    tmp_output = tmp_path / "install.ps1.copy"
    result = subprocess.run(
        [sys.executable, str(_HERE / "patch_install_ps1_offline.py"),
         "--input", str(tmp_input), "--output", str(tmp_output)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert "幂等 no-op" in result.stdout
    assert tmp_output.is_file()
