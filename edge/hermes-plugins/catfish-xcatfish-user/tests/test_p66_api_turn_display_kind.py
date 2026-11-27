"""P66: api 排队那一轮的员工消息别存成 internal_notification (见 plugin_api_turn._patch_p66_api_turn_display_kind)。

用真 hermes 的 response_filters / session_api_turn / session_display: 没有 hermes (CI) 就跳过。
"""
from types import SimpleNamespace

import pytest

rf = pytest.importorskip("gateway.response_filters")
session_api_turn = pytest.importorskip("gateway.session_api_turn")
session_display = pytest.importorskip("gateway.session_display")

import plugin_api_turn  # noqa: E402


@pytest.fixture()
def patched(monkeypatch):
    from gateway import run_turn
    monkeypatch.setattr(rf, "display_kind_for_event", rf.display_kind_for_event)
    monkeypatch.setattr(run_turn, "display_kind_for_event", run_turn.display_kind_for_event)
    plugin_api_turn._patch_p66_api_turn_display_kind()
    return rf.display_kind_for_event


def _event(mid="adm-1"):
    return SimpleNamespace(internal=True, message_id=mid, metadata={})


def test_没打补丁时_api那一轮被记成内部通知():
    # 钉住上游现状: 哪天上游修了, 这条会红, P66 可以删
    fn = getattr(rf.display_kind_for_event, "__wrapped__", rf.display_kind_for_event)
    ev = _event()
    tok = session_api_turn.api_execution.set({"content": []})
    try:
        with session_display.submission_turn_scope("adm-1", "chat:x:1"):
            assert fn(ev) == "internal_notification"
    finally:
        session_api_turn.api_execution.reset(tok)


def test_api那一轮存成员工的话(patched):
    ev = _event()
    tok = session_api_turn.api_execution.set({"content": []})
    try:
        with session_display.submission_turn_scope("adm-1", "chat:x:1"):
            assert patched(ev) is None
    finally:
        session_api_turn.api_execution.reset(tok)


def test_其它内部事件照旧(patched):
    # 不在 api 那一轮里 (后台通知 / 微信的网关重启提示)
    assert patched(_event()) == "internal_notification"
    # 在 api 那一轮里, 但不是这次提交的那条 (同一轮里冒出来的别的内部事件)
    tok = session_api_turn.api_execution.set({"content": []})
    try:
        with session_display.submission_turn_scope("adm-1", "chat:x:1"):
            assert patched(_event("other")) == "internal_notification"
    finally:
        session_api_turn.api_execution.reset(tok)


def test_run_turn里from_import的也换了_重复打不叠(patched):
    from gateway import run_turn
    assert run_turn.display_kind_for_event is patched
    plugin_api_turn._patch_p66_api_turn_display_kind()
    assert rf.display_kind_for_event is patched
