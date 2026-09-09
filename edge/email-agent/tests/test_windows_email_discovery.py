"""Windows 邮件来源发现的跨平台契约测试。"""

from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

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
    monkeypatch.setattr(discovery, "_discover_isolated", discovery._discover_client)
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


def test_isolated_outlook_timeout_is_diagnostic_not_empty_success(monkeypatch):
    def run(args, **kwargs):
        assert kwargs["timeout"] == 15
        assert "creationflags" in kwargs
        raise subprocess.TimeoutExpired(args, 15)
    monkeypatch.setattr(discovery.subprocess, "run", run)
    result = discovery._discover_isolated("outlook-win")
    assert result.status == "unavailable"
    assert "15 秒" in result.reason


def test_isolated_foxmail_validates_worker_result(monkeypatch):
    monkeypatch.setattr(discovery.subprocess, "run", lambda *a, **k: SimpleNamespace(
        returncode=0, stdout='[]', stderr=''))
    assert discovery._discover_isolated("foxmail-win").status == "unavailable"
    payload = discovery.EmailSource("foxmail-win", "ready", [{"name": "work"}], root="E:/mail")
    monkeypatch.setattr(discovery.subprocess, "run", lambda *a, **k: SimpleNamespace(
        returncode=0, stdout=json.dumps(payload.as_json()), stderr=''))
    assert discovery._discover_isolated("foxmail-win") == payload


def test_windows_probes_both_clients_independently(monkeypatch):
    monkeypatch.setattr(discovery.platform, "system", lambda: "Windows")
    monkeypatch.setattr(discovery, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(discovery, "_discover_isolated", lambda client:
        discovery.EmailSource(client, "unavailable" if client == "outlook-win" else "ready", []))
    assert [source.status for source in discovery.discover_sources()] == ["unavailable", "ready"]
