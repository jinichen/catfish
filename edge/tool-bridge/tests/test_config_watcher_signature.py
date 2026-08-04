"""签名必须只对内容敏感 —— touch 不算变化。

8/4: 老实现签名是 (mtime, size)。Companion 的 hermes_jwt_sync 定期重写
config.yaml 刷 JWT, JWT 长度固定 → 内容等长、mtime 变 → config_watcher
判定"变了" → os._exit(0) → watchdog respawn → 循环。

实测 ~100 次/天, 累计 7381 次, 而且日志里没有任何异常痕迹 (它是正常退出的)。
"""
from __future__ import annotations

import os
import time
from pathlib import Path

from catfish_tool_bridge.config_watcher import _signature


def test_touch_alone_is_not_a_change(tmp_path: Path):
    """★ 这条就是那个 bug 的钉子。"""
    p = tmp_path / "config.yaml"
    p.write_text("model:\n  api_key: aaa\n", encoding="utf-8")
    before = _signature(p)
    os.utime(p, (time.time() + 60, time.time() + 60))  # 只动 mtime
    assert _signature(p) == before, "只 touch 不改内容, 不该判定为变化"


def test_same_length_rewrite_is_not_a_change(tmp_path: Path):
    """JWT 刷新的真实形态: 等长重写。老签名 (mtime,size) 在这里也会误判。"""
    p = tmp_path / "config.yaml"
    p.write_text("model:\n  api_key: aaa\n", encoding="utf-8")
    before = _signature(p)
    time.sleep(0.01)
    p.write_text("model:\n  api_key: aaa\n", encoding="utf-8")  # 内容一样, mtime 新
    assert _signature(p) == before


def test_real_content_change_is_detected(tmp_path: Path):
    """别矫枉过正 —— 内容真变了必须能发现, 否则 cdp_url 失效没人知道。"""
    p = tmp_path / "config.yaml"
    p.write_text("model:\n  api_key: aaa\n", encoding="utf-8")
    before = _signature(p)
    p.write_text("model:\n  api_key: bbb\n", encoding="utf-8")  # 等长但内容不同
    assert _signature(p) != before, "等长的内容变化也必须被发现"


def test_missing_file_is_empty_tuple(tmp_path: Path):
    assert _signature(tmp_path / "nope.yaml") == ()
