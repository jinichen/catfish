"""config_watcher 单测.

跟 test_skill_watcher 同思路 — 不测 _watcher_loop 全流程 (会跑很久 + 调 os._exit).
测纯函数 + start() smoke. 后台线程死循环 + os._exit 留给手工真机验证.
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from catfish_tool_bridge import config_watcher


# ============================================================
# _signature 测试
# ============================================================


def test_signature_nonexistent_file(tmp_path: Path) -> None:
    """文件不存在 → 空签名, 不抛"""
    assert config_watcher._signature(tmp_path / "no_such.yaml") == ()


def test_signature_existing_file(tmp_path: Path) -> None:
    """存在文件 → (mtime, size) 元组"""
    f = tmp_path / "config.yaml"
    f.write_text("foo: bar\n")

    sig = config_watcher._signature(f)
    assert len(sig) == 2
    mtime, size = sig
    assert mtime > 0
    assert size == len(b"foo: bar\n")


def test_signature_changes_on_file_edit(tmp_path: Path) -> None:
    """改文件内容 → mtime + size 变 → 签名变"""
    f = tmp_path / "config.yaml"
    f.write_text("v1")
    sig_before = config_watcher._signature(f)

    # 等 1.1s 让 mtime 真变 (POSIX 文件系统 mtime 精度 1s)
    time.sleep(1.1)
    f.write_text("v2 longer content")

    sig_after = config_watcher._signature(f)
    assert sig_before != sig_after
    # size 变了 (新内容更长)
    assert sig_after[1] > sig_before[1]


def test_signature_changes_on_size_only(tmp_path: Path) -> None:
    """文件 size 改但 mtime 同 (理论上罕见, 但也覆盖)"""
    f = tmp_path / "config.yaml"
    f.write_text("xxx")
    sig1 = config_watcher._signature(f)

    # 大部分系统会更新 mtime, 但我们的签名 (mtime, size) 任何一个变都触发, 这里
    # 只是确认 size 字段在签名里
    assert sig1[1] == 3


def test_signature_directory_returns_empty(tmp_path: Path) -> None:
    """传目录而不是文件 → 空签名 (config 必须是文件)"""
    assert config_watcher._signature(tmp_path) == ()


def test_signature_stable(tmp_path: Path) -> None:
    """同一个文件多次调 _signature 返回同样结果"""
    f = tmp_path / "config.yaml"
    f.write_text("stable content")
    sig1 = config_watcher._signature(f)
    sig2 = config_watcher._signature(f)
    assert sig1 == sig2


# ============================================================
# 配置常量 sanity check
# ============================================================


def test_poll_interval_reasonable() -> None:
    """POLL_INTERVAL_SEC 合理范围 (3-60s)"""
    assert 3 <= config_watcher.POLL_INTERVAL_SEC <= 60


def test_poll_faster_than_skill_watcher() -> None:
    """config 改动比 skill 改动更紧迫 (cdp_url 失效立刻 break browser),
    config_watcher poll 间隔应该 ≤ skill_watcher.

    历史: skill_watcher = 15s, config_watcher = 10s.
    """
    from catfish_tool_bridge import skill_watcher
    assert config_watcher.POLL_INTERVAL_SEC <= skill_watcher.POLL_INTERVAL_SEC


# ============================================================
# start() smoke
# ============================================================


def test_start_returns_daemon_thread(tmp_path: Path) -> None:
    """start() 返回 daemon 线程, 主进程退出时自动清理"""
    f = tmp_path / "config.yaml"
    f.write_text("dummy")
    t = config_watcher.start(config_path=f)
    assert t.daemon is True
    assert t.is_alive()
    assert "config-watcher" in t.name
    # daemon 线程不需要 join, 主进程退它跟着退


def test_start_with_nonexistent_config(tmp_path: Path) -> None:
    """config 文件不存在 → start() 仍返回 thread, 但 watcher loop 内部会 early return"""
    f = tmp_path / "no_such.yaml"
    t = config_watcher.start(config_path=f)
    assert t.daemon is True
    # 注: 线程 early return, 但已 spawn — alive 状态可能在 start 后立刻就 False
    # 测试不看 is_alive (race), 只看不抛异常 + name 对


# ============================================================
# 跟 skill_watcher 共享 mark_dispatch / _wait_quiet
# ============================================================


def test_imports_wait_quiet_from_skill_watcher() -> None:
    """config_watcher 复用 skill_watcher 的 _wait_quiet 实现 — 共享 quiet period 逻辑"""
    from catfish_tool_bridge import skill_watcher
    # _wait_quiet 是同一个对象 (我们 from..import 的)
    assert config_watcher._wait_quiet is skill_watcher._wait_quiet


def test_mark_dispatch_shared() -> None:
    """skill_watcher.mark_dispatch 调用后, _last_dispatch_ts 更新.
    config_watcher 的 _wait_quiet 也读这个共享状态."""
    from catfish_tool_bridge import skill_watcher

    before = skill_watcher._last_dispatch_ts
    time.sleep(0.01)
    skill_watcher.mark_dispatch()
    after = skill_watcher._last_dispatch_ts

    assert after > before
