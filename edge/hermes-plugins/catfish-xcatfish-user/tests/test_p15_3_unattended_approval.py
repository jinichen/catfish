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
    platform: str = "api_server",
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

    # v0.22: 判断本体在 tools.approval_context, approval.py 是 from-import 的一份; 两份都要换
    approval_context = types.ModuleType("tools.approval_context")
    approval_context._is_unattended_platform_approval_context = original
    approval_context._get_session_platform = lambda: platform

    tools_package = types.ModuleType("tools")
    tools_package.__path__ = []
    monkeypatch.setitem(sys.modules, "tools", tools_package)
    monkeypatch.setitem(sys.modules, "tools.approval", approval)
    monkeypatch.setitem(sys.modules, "tools.approval_context", approval_context)
    approval._fake_context = approval_context
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
    # approval_context 那份 (写文件保护 / Codex / _is_gateway_approval_context 用的) 也换了
    assert approval._fake_context._is_unattended_platform_approval_context() is False


def test_webhook_callback_does_not_open_gate(monkeypatch):
    """webhook 每轮都注册回调, 但没有能回答审批的界面 —— 不能因为有回调就放开。"""
    approval, _original = _fake_approval_module(
        monkeypatch,
        unattended=True,
        callback=lambda _event: None,
        platform="webhook",
    )

    plugin_approval._patch_p15_3_unattended_companion_approval()

    assert approval._is_unattended_platform_approval_context() is True


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


# P15.4 (Companion 会话强制 manual) 10/14 随 hermes v0.22 升级退役: 审批改用 v0.22 的 smart 模式
# (AI 先判断, 拿不准再问员工), 见 docs/HERMES-022-UPGRADE-ASSESSMENT.md「审批模式」。
