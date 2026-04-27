"""skill_watcher 单测。

注: 不测 _watcher_loop / _wait_quiet 全流程 (会跑很久 + 调 os._exit). 测纯函数:
    - _signature: 不同 .md 状态生成的签名是否能区分
    - mark_dispatch: 标记机制工作不工作
后台线程死循环 + os._exit 这种"集成行为"留给手工真机验证。
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from catfish_tool_bridge import skill_watcher


# ============================================================
# _signature 测试
# ============================================================


def test_signature_empty_dir(tmp_path: Path) -> None:
    """空目录 → 空签名"""
    assert skill_watcher._signature(tmp_path) == ()


def test_signature_nonexistent_dir(tmp_path: Path) -> None:
    """目录不存在 → 空签名, 不抛"""
    assert skill_watcher._signature(tmp_path / "no_such_dir") == ()


def test_signature_detects_skill_file(tmp_path: Path) -> None:
    """.../<ns>/<skill>/SKILL.md 落盘 → 签名包含此条目"""
    skill_dir = tmp_path / "productivity" / "my-skill"
    skill_dir.mkdir(parents=True)
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text("---\nname: my-skill\n---\nbody")

    sig = skill_watcher._signature(tmp_path)
    assert len(sig) == 1
    path, mtime = sig[0]
    assert path.endswith("SKILL.md")
    assert mtime > 0


def test_signature_changes_on_file_addition(tmp_path: Path) -> None:
    """加新 skill → 签名变 (检测增加场景)"""
    (tmp_path / "ns" / "a").mkdir(parents=True)
    (tmp_path / "ns" / "a" / "SKILL.md").write_text("a")
    sig_before = skill_watcher._signature(tmp_path)

    (tmp_path / "ns" / "b").mkdir(parents=True)
    (tmp_path / "ns" / "b" / "SKILL.md").write_text("b")
    sig_after = skill_watcher._signature(tmp_path)

    assert sig_before != sig_after
    assert len(sig_after) == len(sig_before) + 1


def test_signature_changes_on_file_modification(tmp_path: Path) -> None:
    """修改 skill 内容 → mtime 变 → 签名变"""
    p = tmp_path / "ns" / "s"
    p.mkdir(parents=True)
    md = p / "SKILL.md"
    md.write_text("v1")
    sig_before = skill_watcher._signature(tmp_path)

    # 等 1.1s 让 mtime 真变 (POSIX 文件系统 mtime 精度 1s)
    time.sleep(1.1)
    md.write_text("v2 — 内容改了")

    sig_after = skill_watcher._signature(tmp_path)
    assert sig_before != sig_after


def test_signature_changes_on_file_deletion(tmp_path: Path) -> None:
    """删 skill → 签名变"""
    p = tmp_path / "ns" / "s"
    p.mkdir(parents=True)
    md = p / "SKILL.md"
    md.write_text("x")
    sig_before = skill_watcher._signature(tmp_path)

    md.unlink()
    sig_after = skill_watcher._signature(tmp_path)
    assert sig_before != sig_after
    assert sig_after == ()


def test_signature_ignores_non_skill_md(tmp_path: Path) -> None:
    """目录里有别的 .md 但不是 SKILL.md 不算"""
    p = tmp_path / "ns" / "s"
    p.mkdir(parents=True)
    (p / "README.md").write_text("readme")
    (p / "SKILL.md").write_text("real skill")

    sig = skill_watcher._signature(tmp_path)
    assert len(sig) == 1
    assert sig[0][0].endswith("SKILL.md")


def test_signature_recursive(tmp_path: Path) -> None:
    """skills/<namespace>/<skill>/SKILL.md 这种二层结构能被找到"""
    for ns in ["productivity", "research"]:
        for s in ["a", "b"]:
            d = tmp_path / ns / s
            d.mkdir(parents=True)
            (d / "SKILL.md").write_text(f"{ns}/{s}")

    sig = skill_watcher._signature(tmp_path)
    assert len(sig) == 4


def test_signature_stable_order(tmp_path: Path) -> None:
    """同样的状态多次调 _signature 返回同样结果 (deterministic)"""
    for s in ["c", "a", "b"]:
        d = tmp_path / "ns" / s
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(s)

    sig1 = skill_watcher._signature(tmp_path)
    sig2 = skill_watcher._signature(tmp_path)
    assert sig1 == sig2
    # 排序后应是 a < b < c
    paths = [p for p, _ in sig1]
    assert paths == sorted(paths)


# ============================================================
# mark_dispatch 测试
# ============================================================


def test_mark_dispatch_updates_timestamp() -> None:
    before = skill_watcher._last_dispatch_ts
    time.sleep(0.01)
    skill_watcher.mark_dispatch()
    after = skill_watcher._last_dispatch_ts
    assert after > before


def test_mark_dispatch_thread_safe() -> None:
    """模拟多线程并发调 mark_dispatch — 应该不抛 / 时间戳合理增长"""
    import threading

    skill_watcher.mark_dispatch()  # baseline
    start_ts = skill_watcher._last_dispatch_ts

    def hammer():
        for _ in range(100):
            skill_watcher.mark_dispatch()

    threads = [threading.Thread(target=hammer) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    end_ts = skill_watcher._last_dispatch_ts
    # 总跑了 500 次 mark, 应该比 baseline 晚
    assert end_ts >= start_ts


# ============================================================
# 配置常量 sanity check
# ============================================================


def test_constants_make_sense() -> None:
    """默认值应该平衡"""
    assert 5 <= skill_watcher.POLL_INTERVAL_SEC <= 60
    assert skill_watcher.QUIET_PERIOD_SEC >= 10
    assert skill_watcher.MAX_QUIET_WAIT_SEC > skill_watcher.QUIET_PERIOD_SEC


# ============================================================
# start() smoke
# ============================================================


def test_start_returns_daemon_thread(tmp_path: Path) -> None:
    """start() 应该返回 daemon 线程, 主进程退出时自动清理"""
    t = skill_watcher.start(skills_dir=tmp_path)
    assert t.daemon is True
    assert t.is_alive()
    assert "skill-watcher" in t.name
    # daemon 线程不需要 join, 主进程退它跟着退
