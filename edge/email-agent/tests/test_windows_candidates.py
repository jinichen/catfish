"""Windows 取数来源: 配了 IMAP 就不再碰客户端 (9/26)。"""
from __future__ import annotations

import pytest

from catfish_email import inbox


@pytest.fixture
def env(monkeypatch):
    for var in ("CATFISH_IMAP_HOST", "CATFISH_IMAP_USER", "CATFISH_IMAP_PASSWORD"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(inbox, "_eml_dir_configured", lambda: False)
    return monkeypatch


def test_imap_configured_uses_imap_only(env):
    env.setattr(inbox, "_imap_configured", lambda: True)
    assert inbox._windows_candidates() == ["imap"]


def test_imap_plus_explicit_eml_dir_keeps_the_folder_after_imap(env):
    env.setattr(inbox, "_imap_configured", lambda: True)
    env.setattr(inbox, "_eml_dir_configured", lambda: True)
    assert inbox._windows_candidates() == ["imap", "eml-dir"]


def test_without_imap_old_machines_keep_auto_detection(env):
    env.setattr(inbox, "_imap_configured", lambda: False)
    assert inbox._windows_candidates() == ["outlook-win", "eml-dir"]


def test_get_all_adapters_on_windows_never_builds_outlook_when_imap_configured(env):
    env.setattr(inbox, "_imap_configured", lambda: True)
    env.setattr(inbox.platform, "system", lambda: "Windows")
    built = []
    env.setattr(inbox, "_get_adapter_explicit", lambda c: built.append(c) or c)
    inbox.get_all_adapters()
    assert built == ["imap"]
