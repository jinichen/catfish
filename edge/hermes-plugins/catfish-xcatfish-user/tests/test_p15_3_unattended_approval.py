"""P15.3: Companion 的交互审批不能被 Hermes unattended gate 截断。"""
from __future__ import annotations

import sys
import types

import plugin_approval


def _fake_approval_module(
    monkeypatch,
    *,
    unattended: bool,
    callback=None,
    session_key: str = "companion-session",
):
    """构造只包含 P15.3 所需契约的 Hermes approval 模块。"""
    approval = types.ModuleType("tools.approval")
    original = lambda: unattended
    approval._is_unattended_platform_approval_context = original
    approval._get_approval_mode = lambda: "smart"
    approval.get_current_session_key = lambda: session_key
    approval._gateway_notify_cbs = (
        {session_key: callback} if callback is not None else {}
    )

    tools_package = types.ModuleType("tools")
    tools_package.__path__ = []
    monkeypatch.setitem(sys.modules, "tools", tools_package)
    monkeypatch.setitem(sys.modules, "tools.approval", approval)
    return approval, original


def test_companion_session_reaches_interactive_approval(monkeypatch):
    callback = lambda _event: None
    approval, original = _fake_approval_module(
        monkeypatch,
        unattended=True,
        callback=callback,
    )

    plugin_approval._patch_p15_3_unattended_companion_approval()

    assert approval._is_unattended_platform_approval_context() is False
    assert approval._is_unattended_platform_approval_context._catfish_p15_3 is True
    assert approval._is_unattended_platform_approval_context._catfish_p15_3_original is original


def test_unattended_session_without_companion_callback_stays_denied(monkeypatch):
    approval, _original = _fake_approval_module(
        monkeypatch,
        unattended=True,
        callback=None,
    )

    plugin_approval._patch_p15_3_unattended_companion_approval()

    assert approval._is_unattended_platform_approval_context() is True


def test_non_unattended_context_is_unchanged(monkeypatch):
    approval, _original = _fake_approval_module(
        monkeypatch,
        unattended=False,
        callback=lambda _event: None,
    )

    plugin_approval._patch_p15_3_unattended_companion_approval()

    assert approval._is_unattended_platform_approval_context() is False


def test_wrong_session_callback_does_not_open_gate(monkeypatch):
    approval, _original = _fake_approval_module(
        monkeypatch,
        unattended=True,
        callback=None,
        session_key="current-session",
    )
    approval._gateway_notify_cbs["other-session"] = lambda _event: None

    plugin_approval._patch_p15_3_unattended_companion_approval()

    assert approval._is_unattended_platform_approval_context() is True


def test_callback_inspection_failure_fails_closed(monkeypatch):
    approval, _original = _fake_approval_module(
        monkeypatch,
        unattended=True,
        callback=lambda _event: None,
    )

    def _broken_session_key():
        raise RuntimeError("context unavailable")

    approval.get_current_session_key = _broken_session_key
    plugin_approval._patch_p15_3_unattended_companion_approval()

    assert approval._is_unattended_platform_approval_context() is True


def test_patch_is_idempotent(monkeypatch):
    approval, _original = _fake_approval_module(
        monkeypatch,
        unattended=True,
        callback=lambda _event: None,
    )

    plugin_approval._patch_p15_3_unattended_companion_approval()
    patched = approval._is_unattended_platform_approval_context
    plugin_approval._patch_p15_3_unattended_companion_approval()

    assert approval._is_unattended_platform_approval_context is patched


def test_companion_session_forces_manual_mode(monkeypatch):
    callback = lambda _event: None
    approval, _original = _fake_approval_module(
        monkeypatch,
        unattended=False,
        callback=callback,
    )

    plugin_approval._patch_p15_4_companion_manual_approval()

    assert approval._get_approval_mode() == "manual"
    assert approval._get_approval_mode._catfish_p15_4 is True


def test_non_companion_session_keeps_configured_mode(monkeypatch):
    approval, _original = _fake_approval_module(
        monkeypatch,
        unattended=False,
        callback=None,
    )

    plugin_approval._patch_p15_4_companion_manual_approval()

    assert approval._get_approval_mode() == "smart"
