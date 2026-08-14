"""patch_install_ps1_offline.py 单测 (W1 BL-CATFISH-OFFLINE-INSTALL).

覆盖 4 类断言:
    1. patched 输出每个 patch 各留 1 处 marker + 3 处 -Offline* 参数
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
    """patched install.ps1 含 marker + 3 个 -Offline* 参数 (条数走 ANCHORS)."""
    patched = apply_patches(upstream_install_ps1)
    verify_patched(patched)  # 内部 assert marker 条数 (len(ANCHORS)) + 参数


def test_每个_patch_各留一处_marker(upstream_install_ps1: str):
    """条数跟着 ANCHORS 走, **不写死**。

    8/14 之前这条写的是 `== 4`, 而补丁早就长到 7 条 (加了 tar / npm 离线 /
    chromium 三条)。同一个事实当时有三份副本: 这里的 4、verify_patched 里的 7、
    还有两个 docstring 里的"4 处"。加一条 patch 要记得改三处, 漏了就是一条红
    测试挂在那儿 —— 而红测试放久了, 下次真出事也会被当成"又是那个老的"。

    现在三处都走 len(ANCHORS)。
    """
    patched = apply_patches(upstream_install_ps1)
    assert patched.count(MARKER) == len(ANCHORS)


def test_anchors_每条替换文本里恰好一处_marker():
    """★ 上面那条只数总数, 数对了不代表"每 patch 一处"。

    两条 patch 一条塞两个 marker、另一条一个都不塞, 总数照样对得上 ——
    而那时 detect_already_patched 会漏判没带 marker 的那条。
    这条直接在 ANCHORS 上查, 不用跑补丁。
    """
    for name, (before, after) in ANCHORS.items():
        assert before.count(MARKER) == 0, f"anchor {name} 的**原文**里就带 marker 了"
        assert after.count(MARKER) == 1, (
            f"anchor {name} 的替换文本里有 {after.count(MARKER)} 处 marker, 应当恰好 1 处"
        )


def test_marker_落在七个不同的行上(upstream_install_ps1: str):
    """★ 防"两条 patch 挤进同一行" —— 那样总数对、每条也对, 但补丁其实叠了。"""
    patched = apply_patches(upstream_install_ps1)
    lines = [i for i, l in enumerate(patched.splitlines()) if MARKER in l]
    assert len(lines) == len(ANCHORS)
    assert len(set(lines)) == len(lines)


def test_apply_patches_adds_three_offline_params(upstream_install_ps1: str):
    patched = apply_patches(upstream_install_ps1)
    # 每个参数 1 处定义 + 3 处使用 (Install-Uv/Test-Python/Install-Repository)
    # = 4 处 mention. 上限 4, 下限 3 (Install-Repository 只用 $OfflineSourceDir).
    for param in ("$OfflineSourceDir", "$OfflineUvExe", "$OfflinePythonZip"):
        count = patched.count(param)
        assert count >= 2, f"参数 {param} 应至少 2 处 (def + use), 实际 {count}"


def test_行数增量跟_anchors_算出来的完全一致(upstream_install_ps1: str):
    """原来写的是"预期加 60-120 行" —— 两个魔数, 每加一条 patch 就过期一次
    (实际早就是 211 行了)。

    而这两个数字本来就是**算得出来的**: 每条 anchor 用 after 换掉 before,
    增量就是两者行数之差。所以这里不猜区间, 直接算, 要求**严格相等**。

    严格相等比区间强的地方: 它同时钉住了"替换恰好发生了这些、没多没少"。
    某个 anchor 命中两次被换了两遍, 或者哪条 patch 悄悄没生效, 区间断言
    多半照样绿, 这条会当场红。
    """
    expected_delta = sum(
        (after.count("\n") + 1) - (before.count("\n") + 1)
        for before, after in ANCHORS.values()
    )
    patched = apply_patches(upstream_install_ps1)
    actual_delta = (patched.count("\n") + 1) - (upstream_install_ps1.count("\n") + 1)
    assert actual_delta == expected_delta, (
        f"ANCHORS 算出来该加 {expected_delta} 行, 实际加了 {actual_delta} 行"
    )


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
        f"重新 audit ANCHORS 里每条 anchor, 更新脚本顶部 UPSTREAM_SHA256."
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


def test_全部_anchor_都登记了():
    """ANCHORS 表必须正好是这 7 处 —— 多一处少一处都要有人来改这行。

    8/8 修: 原名 `test_all_four_anchors_registered`, 断言只有 4 个 anchor。
    7/17 加 npm_global / npm_local_helper / playwright_chromium 时没人动它,
    于是这条从 7/17 起**一直红着**没人发现 (跟这个脚本里 UPSTREAM_COMMIT
    要解决的是同一类病: 改了 A 忘了同步 B, 而 B 的失败没人看)。

    这里不写 `len(ANCHORS) == 7` —— 那样改名字不会红, 起不到"逼人复核"的作用。
    写成集合相等: 加/删/改名任何一个 anchor 都会红, 红了就得回来确认
    verify_patched 的 marker_count 和 CLI 输出里的处数也一起改了。
    """
    assert set(ANCHORS.keys()) == {
        "param",
        "install_uv",
        "test_python",
        "install_repository",
        "npm_global",             # BL-WIN-INSTALL-NPM-OFFLINE (7/17)
        "npm_local_helper",       # BL-WIN-INSTALL-NPM-OFFLINE (7/17)
        "playwright_chromium",    # BL-WIN-INSTALL-CHROMIUM-BUNDLE (7/17)
    }


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


# ─── 产物编码: Windows PowerShell 5.1 读得懂吗 (8/14) ──────────
#
# 8/14 CircleCI 现场: 打完补丁的 install.ps1 被 CI 那步语法检查判了
# **24 处 parse error** (行 781/795/1611/…/3075), 一行都执行不了。
#
# 真因不在补丁内容, 在**编码**:
#
#   · 补丁往 install.ps1 里插了 900+ 个非 ASCII 字符 —— 71 行中文注释,
#     外加 14 处中文在 Write-Info / Write-Warn 的字符串里
#   · 产物写的是**无 BOM 的 UTF-8**
#   · 而 Windows PowerShell 5.1 (装机现场 + CircleCI job 的 shell 都是它)
#     读无 BOM 文件按 ANSI / cp1252 解释 → 中文字节解成乱码, 里面混进引号和
#     括号 → 整个文件 parse 就挂
#
# 为什么本地怎么试都是好的: pwsh 7 (mac/Linux) 对无 BOM 文件默认 UTF-8。
# 这个坑**只在 Windows PowerShell 5.1 上现形**。
#
# 下面两条钉的就是这个 —— 不用等 CircleCI 跑 40 分钟才知道。


def _patch_to(tmp_path, content: str) -> bytes:
    """跑一遍真脚本, 返回产物的**原始字节** (不是 str —— 这两条测的就是编码)。"""
    src = tmp_path / "in.ps1"
    src.write_text(content, encoding="utf-8")
    out = tmp_path / "out.ps1"
    r = subprocess.run(
        [sys.executable, str(_HERE / "patch_install_ps1_offline.py"),
         "--input", str(src), "--output", str(out)],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, f"补丁脚本挂了: {r.stderr}"
    return out.read_bytes()


def test_产物必须带_utf8_bom(upstream_install_ps1: str, tmp_path):
    """★★★ 无 BOM = Windows PowerShell 5.1 按 cp1252 读 = 装机必挂。"""
    raw = _patch_to(tmp_path, upstream_install_ps1)
    assert raw[:3] == b"\xef\xbb\xbf", (
        f"产物没有 UTF-8 BOM (前 3 字节 {raw[:3].hex()})。"
        "Windows PowerShell 5.1 会按 cp1252 读, 中文注释解成乱码后 parse 直接挂 —— "
        "8/14 CircleCI 就是这么报的 24 处语法错。"
    )


def test_按cp1252重解会毁掉中文_证明bom不是可选项(upstream_install_ps1: str, tmp_path):
    """★★ 反证: 说明上面那条不是形式主义。

    直接复现 5.1 的行为 —— 同样的字节按 cp1252 解, 中文全成乱码。
    判据取"中文还在不在"而不是跑 PowerShell: 测试机不一定有 pwsh, 而两者是
    同一个因 (字节被按错的编码解了), 中文没了就等于 parse 会挂。
    """
    raw = _patch_to(tmp_path, upstream_install_ps1)
    body = raw[3:] if raw[:3] == b"\xef\xbb\xbf" else raw

    def has_cjk(t: str) -> bool:
        return any(0x4E00 <= ord(c) <= 0x9FFF for c in t)

    assert has_cjk(body.decode("utf-8")), (
        "产物里没有中文了 —— 那这两条测试的前提要重审 (补丁不再插中文的话, "
        "BOM 就不是硬需求了)"
    )
    assert not has_cjk(body.decode("cp1252", errors="replace")), (
        "cp1252 重解之后中文居然还在 —— 反证的前提不对, 重新想"
    )
