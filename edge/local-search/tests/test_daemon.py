"""daemon 分发器测试：mock 平台名验证路由到对应模块。

不真的调 launchctl / schtasks / systemctl，只验证分发路由 + 模板渲染。
"""
from __future__ import annotations

from unittest import mock

from catfish_search import daemon, daemon_linux, daemon_macos, daemon_windows


def test_dispatch_darwin():
    with mock.patch("platform.system", return_value="Darwin"):
        mod = daemon._backend()
        assert mod.__name__.endswith("daemon_macos")


def test_dispatch_windows():
    with mock.patch("platform.system", return_value="Windows"):
        mod = daemon._backend()
        assert mod.__name__.endswith("daemon_windows")


def test_dispatch_linux():
    with mock.patch("platform.system", return_value="Linux"):
        mod = daemon._backend()
        assert mod.__name__.endswith("daemon_linux")


def test_dispatch_unknown_raises():
    with mock.patch("platform.system", return_value="Plan9"):
        try:
            daemon._backend()
        except RuntimeError as e:
            assert "Plan9" in str(e)
            return
        raise AssertionError("unknown 平台应该抛 RuntimeError")


def test_macos_plist_template_contains_essentials():
    plist = daemon_macos._render_plist()
    assert "ai.catfish.search.watcher" in plist
    assert "catfish_search.cli" in plist
    assert "<key>KeepAlive</key>" in plist
    assert "<key>RunAtLoad</key>" in plist


def test_windows_wrapper_bat_has_loop():
    bat = daemon_windows._render_wrapper()
    assert "goto loop" in bat
    assert "timeout /t 10" in bat
    assert "catfish_search.cli" in bat
    # 换行必须是 Windows 的 CRLF。
    assert "\r\n" in bat


def test_linux_service_unit_valid():
    unit = daemon_linux._render_service()
    assert "[Service]" in unit
    assert "Restart=always" in unit
    assert "catfish_search.cli" in unit
    assert "WantedBy=default.target" in unit
