"""Windows 邮件来源发现的跨平台契约测试。"""

from __future__ import annotations

import json

import catfish_email.discovery as discovery


def test_payload_contains_independent_client_status(monkeypatch):
    class FakeAccount:
        name = "工作"
        address = "work@example.com"
        is_default = True

    class FakeOutlook:
        name = "outlook_win"

        def list_accounts(self):
            return [FakeAccount()]

    class FakeFoxmail:
        name = "foxmail_win"
        profiles_dir = r"E:\mail\Storage"

        def list_accounts(self):
            raise discovery.DataNotFoundError("Storage 不可读")

    monkeypatch.setattr(discovery.platform, "system", lambda: "Windows")
    monkeypatch.setattr(
        discovery,
        "_get_adapter_explicit",
        lambda client: FakeOutlook() if client == "outlook-win" else FakeFoxmail(),
    )

    payload = discovery.discover_payload()

    assert payload["ready_client"] == "outlook-win"
    assert payload["sources"][0]["status"] == "ready"
    assert payload["sources"][0]["accounts"][0]["address"] == "work@example.com"
    assert payload["sources"][1]["status"] == "unavailable"
    assert "Storage 不可读" in payload["sources"][1]["reason"]
    assert "password" not in json.dumps(payload).lower()


def test_non_windows_is_explicitly_unsupported(monkeypatch):
    monkeypatch.setattr(discovery.platform, "system", lambda: "Darwin")

    payload = discovery.discover_payload()

    assert payload["sources"][0]["status"] == "unsupported"
    assert payload["ready_client"] is None
