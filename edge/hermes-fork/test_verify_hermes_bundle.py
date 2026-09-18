"""9/18: 离线装不上的真因 —— 构建期把 uv.lock 弄失效了。

# 实测对照 (uv 0.12.3, 即 .uv-version 钉定的那个版本)

拿真实的 hermes-agent-bundle.tar.gz 里的 pyproject.toml + uv.lock::

    原样:                       uv lock --check --offline → exit 0
                                "Resolved 255 packages in 1ms"
    删掉 exclude-newer 那行后:  uv lock --check --offline → exit 1
                                "Resolving despite existing lockfile due to
                                 removal of global exclude newer"

再用 Tier-0 的真实命令 `uv sync --extra all --locked --offline` 交叉验证,
两边的失败原因不同, 这才坐实了因果::

    原样:    "requested data wasn't found in the cache"   ← lock 被接受, 只缺包
    删掉后:  "No solution found ..."                      ← 卡在解析, 根本没到装包

老的 patch_hermes_bundle.py 做的就是"删掉那行", 理由写在它 docstring 里:
"uv expects an absolute date there, not a duration"。这个前提不成立 ——
uv.lock 自己的注释就写着 "backwards compatibility when using relative
exclude-newer values", 而 0.12.3 实测接受 "14 days"。

后果不只是"慢": install.ps1 的 Tier-0 是**唯一**带 SHA256 校验的安装路径,
它失败后退到 `uv pip install` 三个 tier, 那几个从 PyPI 现场重解析、不校验
哈希。所以这个 patch 既破坏了断网安装, 也降低了供应链姿态。

# 这个文件钉什么

  ① 相对窗口 → span 的换算
  ② 两边一致 → 放行
  ③ pyproject 少了 exclude-newer 而 lock 还记着 span → 必须拦 (就是老 patch
     造成的那个状态)
  ④ 绝对日期形式也要能对上
  ⑤ 段内 key 解析不被注释和兄弟段干扰 (pyproject 里
     `[tool.uv.exclude-newer-package]` 紧挨着, 注释里也大量出现该字样)
  ⑥ 端到端: 真归档放行, 被老 patch 处理过的归档拦下
"""
from __future__ import annotations

import copy
import io
import re
import sys
import tarfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from verify_hermes_bundle import (  # noqa: E402
    check_consistency,
    duration_to_span,
    section_value,
    verify_archive,
)

_REAL_BUNDLE = (
    Path(__file__).resolve().parents[1]
    / "companion-app/src-tauri/resources/mac-aarch64/hermes-agent-bundle.tar.gz"
)

_PYPROJECT = """\
[project]
name = "hermes-agent"

[tool.uv]
override-dependencies = ["pynacl>=1.6,<1.7"]
exclude-newer = "14 days"
# 注释里也会出现 exclude-newer = "这不该被解析"
[tool.uv.exclude-newer-package]
aiohttp = false
"""

_LOCK = """\
version = 1
revision = 3

[options]
exclude-newer = "0001-01-01T00:00:00Z" # This has no effect and is included for backwards compatibility when using relative exclude-newer values.
exclude-newer-span = "P14D"

[options.exclude-newer-package]
aiohttp = false
"""


# ─────────────────────────────────────────────────────────────
# ① 换算
# ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,want",
    [("14 days", "P14D"), ("1 day", "P1D"), ("2 weeks", "P14D"), ("6 hours", "PT6H")],
)
def test_duration_to_span(raw, want):
    assert duration_to_span(raw) == want


@pytest.mark.parametrize("raw", ["2026-08-17", "2026-08-17T00:00:00Z", "随便写的"])
def test_absolute_dates_are_not_durations(raw):
    assert duration_to_span(raw) is None


# ─────────────────────────────────────────────────────────────
# ⑤ 段内取值
# ─────────────────────────────────────────────────────────────


def test_section_value_ignores_comments_and_sibling_sections():
    assert section_value(_PYPROJECT, "tool.uv", "exclude-newer") == "14 days"
    # 兄弟段 [tool.uv.exclude-newer-package] 不该被当成 [tool.uv]
    assert section_value(_PYPROJECT, "tool.uv", "aiohttp") is None


def test_section_value_returns_none_for_missing_section():
    assert section_value("[project]\nname = 'x'\n", "tool.uv", "exclude-newer") is None


def test_lock_options_are_read():
    assert section_value(_LOCK, "options", "exclude-newer-span") == "P14D"
    assert section_value(_LOCK, "options", "exclude-newer") == "0001-01-01T00:00:00Z"


# ─────────────────────────────────────────────────────────────
# ② ③ ④ 一致性
# ─────────────────────────────────────────────────────────────


def test_matching_relative_window_passes():
    assert check_consistency(_PYPROJECT, _LOCK) == []


def test_removing_exclude_newer_is_rejected():
    """老 patch 干的就是这件事 —— 必须拦住。"""
    stripped = _PYPROJECT.replace('exclude-newer = "14 days"\n', "")
    problems = check_consistency(stripped, _LOCK)
    assert len(problems) == 1
    assert "P14D" in problems[0]
    assert "装不上" in problems[0], "错误文案要说清后果, 不能只说不一致"


def test_span_mismatch_is_rejected():
    problems = check_consistency(
        _PYPROJECT, _LOCK.replace('"P14D"', '"P7D"')
    )
    assert problems and "P7D" in problems[0]


def test_absolute_date_must_match_the_lock():
    pyproject = _PYPROJECT.replace('"14 days"', '"2026-08-17T00:00:00Z"')
    lock_ok = _LOCK.replace(
        f'exclude-newer = "0001-01-01T00:00:00Z"',
        'exclude-newer = "2026-08-17T00:00:00Z"',
    ).replace('exclude-newer-span = "P14D"\n', "")
    assert check_consistency(pyproject, lock_ok) == []
    assert check_consistency(pyproject, _LOCK)  # 绝对日期 vs 相对 span → 不一致


def test_both_absent_is_consistent():
    pyproject = _PYPROJECT.replace('exclude-newer = "14 days"\n', "")
    lock = _LOCK.replace('exclude-newer-span = "P14D"\n', "")
    assert check_consistency(pyproject, lock) == []


# ─────────────────────────────────────────────────────────────
# ⑥ 端到端
# ─────────────────────────────────────────────────────────────

_DURATION_LINE = re.compile(
    r"(?m)^[ \t]*exclude-newer[ \t]*=[ \t]*([\"'])\d+ days\1[ \t]*(?:\r?\n|$)"
)


def _rewrite_bundle(src: Path, dest: Path) -> None:
    """复刻老 patch_hermes_bundle.py 的效果, 用来证明校验器逮得住它。"""
    with tarfile.open(src, "r:gz") as source, tarfile.open(dest, "w:gz") as out:
        for member in source.getmembers():
            if member.name == "hermes-agent-src/pyproject.toml":
                text = source.extractfile(member).read().decode("utf-8")
                payload = _DURATION_LINE.sub("", text, count=1).encode("utf-8")
                member = copy.copy(member)
                member.size = len(payload)
                out.addfile(member, io.BytesIO(payload))
            else:
                out.addfile(
                    member, source.extractfile(member) if member.isfile() else None
                )


@pytest.mark.skipif(not _REAL_BUNDLE.is_file(), reason="本机没有构建好的归档")
def test_real_bundle_passes():
    verify_archive(_REAL_BUNDLE)  # 不抛就是通过


@pytest.mark.skipif(not _REAL_BUNDLE.is_file(), reason="本机没有构建好的归档")
def test_old_patch_output_is_caught(tmp_path: Path):
    patched = tmp_path / "patched.tar.gz"
    _rewrite_bundle(_REAL_BUNDLE, patched)
    with pytest.raises(ValueError, match="装不上"):
        verify_archive(patched)
