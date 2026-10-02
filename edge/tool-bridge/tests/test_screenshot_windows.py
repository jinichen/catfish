"""Windows 截图: 框选 / 选窗口走系统截图框, 不再退成全屏 (10/2)。

原来 Windows 上 interactive / window 一律拍全屏 —— 员工想只给一块, 整个屏幕都发出去了。
这里在 mac / Linux 上模拟: 剪贴板序号、打开截图框、取剪贴板图都换成假的。
"""
from __future__ import annotations

import sys
import types

import pytest

from catfish_tool_bridge import catfish_tools_today as today

PIL = pytest.importorskip("PIL")
from PIL import Image  # noqa: E402


class _FakeClipboard:
    def __init__(self, image_after_polls: int | None):
        self.seq = 100
        self.polls = 0
        self.opened = 0
        self.image_after = image_after_polls
        self.image = Image.new("RGB", (40, 30), "red")

    def seq_fn(self):
        self.polls += 1
        if self.image_after is not None and self.polls > self.image_after:
            return 101
        return self.seq

    def open_fn(self):
        self.opened += 1


@pytest.fixture
def fake_win(monkeypatch):
    def install(image_after_polls):
        clip = _FakeClipboard(image_after_polls)
        monkeypatch.setattr(today, "_win_clipboard_seq", clip.seq_fn)
        monkeypatch.setattr(today, "_win_open_snip_overlay", clip.open_fn)
        monkeypatch.setattr(today.time, "sleep", lambda s: None)
        fake_grab = types.SimpleNamespace(
            grabclipboard=lambda: clip.image,
            grab=lambda: Image.new("RGB", (800, 600), "blue"),
        )
        monkeypatch.setitem(sys.modules, "PIL.ImageGrab", fake_grab)
        monkeypatch.setattr(PIL, "ImageGrab", fake_grab, raising=False)
        return clip
    return install


def test_interactive_waits_for_snip_and_saves_clipboard_image(fake_win, tmp_path):
    clip = fake_win(image_after_polls=3)
    out = tmp_path / "shot.png"
    ok, err = today._screencapture_windows("interactive", out)
    assert (ok, err) == (True, None)
    assert clip.opened == 1
    assert Image.open(out).size == (40, 30), "存的是框选的那块, 不是全屏"


def test_cancel_times_out_as_cancelled(fake_win, tmp_path, monkeypatch):
    fake_win(image_after_polls=None)  # 员工按 Esc: 剪贴板永远不变
    monkeypatch.setattr(today, "_WIN_SNIP_TIMEOUT_S", 0.05)
    ok, err = today._screencapture_windows("window", tmp_path / "x.png")
    assert ok is False and "取消" in err
    assert not (tmp_path / "x.png").exists()


def test_fullscreen_does_not_open_overlay(fake_win, tmp_path):
    clip = fake_win(image_after_polls=0)
    out = tmp_path / "full.png"
    assert today._screencapture_windows("fullscreen", out) == (True, None)
    assert clip.opened == 0
    assert Image.open(out).size == (800, 600)


def test_capture_screenshot_keeps_mode_on_windows(fake_win, monkeypatch):
    fake_win(image_after_polls=1)
    monkeypatch.setattr(today.platform, "system", lambda: "Windows")
    r = today.capture_screenshot({"mode": "window", "reason": "看报错"})
    assert r["type"] == "image"
    assert r["mode"] == "window", "不再偷偷改成 fullscreen"
    assert "窗口" in r["platform_note"]
