"""config_watcher 单测.

跟 test_skill_watcher 同思路 — 不测 _watcher_loop 全流程 (会跑很久 + 调 os._exit).
测纯函数 + start() smoke. 后台线程死循环 + os._exit 留给手工真机验证.
"""
from __future__ import annotations

import hashlib
import os
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


# 8/4 fe840c9 把 _signature 从 (mtime, size) 改成 (sha256, 字节数), 修的是一个
# 真事故: Companion 的 hermes_jwt_sync 定期重写 config.yaml 刷 JWT, JWT 等长
# 所以 **size 一字节没变、只有 mtime 变**, 老签名判定"配置变了" → tool-bridge
# 主动退出 → watchdog 拉起 → 循环。实测 ~100 次/天, 累计 7381 次。
#
# 那个 commit **只改了源码, 没动这个文件**, 于是下面几条一直在描述旧契约:
#   · test_signature_existing_file 断言 mtime > 0 —— 现在第一位是 hex 字符串,
#     直接 TypeError。它从 8/4 起就是红的。
#   · test_signature_changes_on_file_edit 还留着 sleep(1.1) 等 mtime 变, 现在
#     没有意义 (判据是内容)。
#   · test_signature_changes_on_size_only 现在是**因为别的原因**才过的。
#
# 更要紧的是: 花了 7381 次重启才查出来的那个 bug, 一条回归测试都没有。
# test_signature_ignores_touch 就是补它 —— 谁把判据改回 mtime, 它立刻红。
# (2026-08-15 修)


def test_signature_existing_file(tmp_path: Path) -> None:
    """存在文件 → (内容 sha256, 字节数)"""
    f = tmp_path / "config.yaml"
    f.write_text("foo: bar\n")

    sig = config_watcher._signature(f)
    assert len(sig) == 2
    digest, size = sig
    assert digest == hashlib.sha256(b"foo: bar\n").hexdigest()
    assert size == len(b"foo: bar\n")


def test_signature_ignores_touch(tmp_path: Path) -> None:
    """**只动 mtime、内容不变 → 签名必须不变。**

    8/4 那个事故的最小复现: JWT 轮换写回等长内容, 老实现 (mtime, size) 判定
    变化, tool-bridge 于是每天自杀 ~100 次。判据改回 mtime 的话这条会红。
    """
    f = tmp_path / "config.yaml"
    f.write_text("api_key: AAAA\n")
    sig_before = config_watcher._signature(f)

    st = f.stat()
    os.utime(f, (st.st_atime, st.st_mtime + 600))   # 内容一个字节不动
    assert f.stat().st_mtime != st.st_mtime, "前提: mtime 真的变了"

    assert config_watcher._signature(f) == sig_before


def test_signature_changes_on_same_length_content(tmp_path: Path) -> None:
    """**长度相同但内容不同 → 签名必须变。**

    JWT 轮换正是这种形状 (等长、值不同), 那种情况该重启。8/4 的修法只是不再被
    "纯 touch"骗到, 不是对等长改动也放行。
    """
    f = tmp_path / "config.yaml"
    f.write_text("api_key: AAAA\n")
    sig1 = config_watcher._signature(f)
    f.write_text("api_key: BBBB\n")
    sig2 = config_watcher._signature(f)
    assert sig1[1] == sig2[1], "前提: 两次长度相同"
    assert sig1 != sig2


def test_signature_changes_on_file_edit(tmp_path: Path) -> None:
    """改文件内容 → 签名变 (判据是内容, 不再需要等 mtime 精度)"""
    f = tmp_path / "config.yaml"
    f.write_text("v1")
    sig_before = config_watcher._signature(f)

    f.write_text("v2 longer content")

    sig_after = config_watcher._signature(f)
    assert sig_before != sig_after
    assert sig_after[1] > sig_before[1]


def test_signature_second_field_is_byte_length(tmp_path: Path) -> None:
    """第二位是**字节数**不是字符数 —— 中文配置里两者不同"""
    f = tmp_path / "config.yaml"
    f.write_text("名字: 鲶鱼\n", encoding="utf-8")
    raw = f.read_bytes()
    assert config_watcher._signature(f)[1] == len(raw)
    assert len(raw) > len("名字: 鲶鱼\n")


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
