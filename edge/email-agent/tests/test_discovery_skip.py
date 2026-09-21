"""Windows 上默认不探本机邮件客户端。

鸿波: "WINDOW 就不要去扫描 outlook 和 foxmail 客户端"

# 不是省那点时间

9/21 真机: outlook-win 的探测子进程 import pywin32 → 加载 pythoncom311.dll
→ 同时 bootstrap 在升级 catfish-email, uv 替换不了被占用的 pywin32 →
**整个邮件功能装不上**。而新版 Outlook 根本不提供 COM, 那次探测注定失败。

所以判据不是"快不快", 是"值不值得为一次注定失败的尝试付这个代价"。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from catfish_email import discovery as D


@pytest.fixture
def on_windows(monkeypatch):
    monkeypatch.setattr(D.platform, "system", lambda: "Windows")
    # os.name 保持 posix → 走 _discover_client 那条 (不起子进程), 好断言
    return monkeypatch


def _spy_spawns(monkeypatch):
    """记下每个被真正探测的 client。"""
    probed: list[str] = []

    def fake(client: str):
        probed.append(client)
        return D.EmailSource(client, "ready", [{"address": "a@x"}])

    monkeypatch.setattr(D, "_discover_client", fake)
    monkeypatch.setattr(D, "_discover_isolated", fake)
    return probed


def test_outlook_not_probed_when_com_is_not_registered(on_windows):
    """**这条是全部要点。** 没注册 COM 就连探都不探。

    注意断言的是"没有被探测", 不是"结果是 unavailable"。后者旧实现也满足 ——
    它探了、失败了、报了不可用, 而 DLL 已经加载进去了。代价发生在拿到结论
    之前。
    """
    on_windows.setattr(
        "catfish_email.adapters.outlook_win.classic_outlook_registered", lambda: False
    )
    on_windows.setattr(D, "_eml_dir_configured", lambda: False)
    probed = _spy_spawns(on_windows)

    D.discover_sources()
    assert "outlook-win" not in probed, f"注定失败的探测还是跑了: {probed}"
    assert probed == ["imap"], f"只该探 imap, 实际 {probed}"


def test_eml_dir_not_probed_when_no_directory_configured(on_windows):
    on_windows.setattr(
        "catfish_email.adapters.outlook_win.classic_outlook_registered", lambda: False
    )
    on_windows.setattr(D, "_eml_dir_configured", lambda: False)
    probed = _spy_spawns(on_windows)
    D.discover_sources()
    assert "eml-dir" not in probed


def test_skipped_sources_still_appear_with_a_reason(on_windows):
    """跳过 ≠ 消失。

    "我们没去试" 和 "试了不行" 是两回事: 后者员工无能为力, 前者他点一下
    就能试。静默消失的话, 装着经典 Outlook 的人会以为这软件不支持 Outlook。
    """
    on_windows.setattr(
        "catfish_email.adapters.outlook_win.classic_outlook_registered", lambda: False
    )
    on_windows.setattr(D, "_eml_dir_configured", lambda: False)
    _spy_spawns(on_windows)

    got = {s.client: s for s in D.discover_sources()}
    assert set(got) == {"imap", "outlook-win", "eml-dir"}
    for client in ("outlook-win", "eml-dir"):
        assert got[client].status == D.SKIPPED
        assert got[client].reason, f"{client} 跳过了却没说为什么"
        assert "跳过" in got[client].reason


def test_skipped_reason_says_what_the_employee_can_do(on_windows):
    """光说"跳过了"没用, 得说清楚怎么让它不跳过。"""
    on_windows.setattr(
        "catfish_email.adapters.outlook_win.classic_outlook_registered", lambda: False
    )
    on_windows.setattr(D, "_eml_dir_configured", lambda: False)
    _spy_spawns(on_windows)
    got = {s.client: s.reason for s in D.discover_sources()}
    assert "扫描一次" in got["outlook-win"]
    assert "选择邮件目录" in got["eml-dir"]


def test_force_scan_probes_everything(on_windows):
    """少数派的门: 装着经典 Outlook 的人点「扫描一次」必须能探到。

    默认不探不等于不能探。这条要是坏了, 那些机器上 Outlook 就真的没救了。
    """
    on_windows.setattr(
        "catfish_email.adapters.outlook_win.classic_outlook_registered", lambda: False
    )
    on_windows.setattr(D, "_eml_dir_configured", lambda: False)
    probed = _spy_spawns(on_windows)

    D.discover_sources(force_scan=True)
    assert probed == ["imap", "outlook-win", "eml-dir"], f"实际 {probed}"


def test_registered_outlook_is_probed_without_force(on_windows):
    """真装了经典 Outlook 的机器上, 不该逼人去点「扫描一次」。"""
    on_windows.setattr(
        "catfish_email.adapters.outlook_win.classic_outlook_registered", lambda: True
    )
    on_windows.setattr(D, "_eml_dir_configured", lambda: False)
    probed = _spy_spawns(on_windows)
    D.discover_sources()
    assert "outlook-win" in probed


def test_configured_eml_dir_is_probed_without_force(on_windows):
    on_windows.setattr(
        "catfish_email.adapters.outlook_win.classic_outlook_registered", lambda: False
    )
    on_windows.setattr(D, "_eml_dir_configured", lambda: True)
    probed = _spy_spawns(on_windows)
    D.discover_sources()
    assert "eml-dir" in probed


def test_imap_is_always_probed(on_windows):
    """IMAP 不依赖任何客户端, 没有"值不值得探"的问题, 永远探。"""
    on_windows.setattr(
        "catfish_email.adapters.outlook_win.classic_outlook_registered", lambda: False
    )
    on_windows.setattr(D, "_eml_dir_configured", lambda: False)
    probed = _spy_spawns(on_windows)
    D.discover_sources()
    assert "imap" in probed


def test_eml_dir_check_is_env_only_never_scans_disk(monkeypatch, tmp_path):
    """判据本身必须是零成本的。

    要是 _eml_dir_configured 里去 rglob 找 .eml, 那就是把"不扫描"换成了
    "换个地方扫描" —— 在一个几万封邮件的导出目录上同样要几秒。
    """
    calls = []
    real_isdir = Path.is_dir

    def spy_isdir(self):
        calls.append(str(self))
        return real_isdir(self)

    monkeypatch.setattr(Path, "is_dir", spy_isdir)
    monkeypatch.setenv("CATFISH_EML_DIR", str(tmp_path))
    assert D._eml_dir_configured() is True
    # 只该 stat 那一个目录, 不该逐层走下去
    assert len(calls) <= 3, f"判据里查了太多路径: {calls}"


def test_broken_eml_dir_detection_means_not_configured(monkeypatch):
    """判据本身出错 → 当成没配 → 跳过。拿不准就别探。"""
    monkeypatch.setattr(
        "catfish_email.adapters.eml_dir._detect_root",
        lambda: (_ for _ in ()).throw(RuntimeError("注册表炸了")),
    )
    assert D._eml_dir_configured() is False
