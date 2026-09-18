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
        name = "eml_dir"
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
    assert discovery._discover_isolated("eml-dir").status == "unavailable"
    payload = discovery.EmailSource("eml-dir", "ready", [{"name": "work"}], root="E:/mail")
    monkeypatch.setattr(discovery.subprocess, "run", lambda *a, **k: SimpleNamespace(
        returncode=0, stdout=json.dumps(payload.as_json()), stderr=''))
    assert discovery._discover_isolated("eml-dir") == payload


def test_windows_probes_both_clients_independently(monkeypatch):
    monkeypatch.setattr(discovery.platform, "system", lambda: "Windows")
    monkeypatch.setattr(discovery, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(discovery, "_discover_isolated", lambda client:
        discovery.EmailSource(client, "unavailable" if client == "outlook-win" else "ready", []))
    assert [source.status for source in discovery.discover_sources()] == ["unavailable", "ready"]


# ── 9/17: 截图实锤的两件事 ──────────────────────────────────────────
#   1. Foxmail 探测抛了清单之外的异常 → 子进程吐 traceback → 前端把整段栈当"原因"
#   2. 中文原因经 GBK 管道到 Rust 变乱码 (那条在 __main__ / Rust 侧, 这里只盯 1)


def test_unlisted_exception_becomes_unavailable_not_traceback(monkeypatch):
    class BrokenFoxmail:
        name = "eml_dir"

        def list_accounts(self):
            raise KeyError("Storage")  # 不在 except 清单里的类型

    monkeypatch.setattr(discovery, "_get_adapter_explicit", lambda client: BrokenFoxmail())
    source = discovery._discover_client("eml-dir")
    assert source.status == "unavailable"
    assert source.accounts == []
    # 类型名留着定位真因, 但不是 traceback
    assert source.reason.startswith("KeyError:")
    assert "Traceback" not in source.reason


def test_worker_main_always_emits_json(monkeypatch, capsys):
    """子进程入口: 哪怕 _discover_client 本身炸了, 也要给父进程一份 JSON。"""
    import runpy
    import sys as _sys

    def boom(client):
        raise RuntimeError("adapter import exploded")

    monkeypatch.setattr(discovery, "_discover_client", boom)
    monkeypatch.setattr(_sys, "argv", ["discovery", "eml-dir"])
    # 直接执行模块的 __main__ 段, 用已 patch 的 discovery 命名空间
    code = open(discovery.__file__, encoding="utf-8").read().split('if __name__ == "__main__":')[1]
    ns = dict(vars(discovery))
    ns["__name__"] = "__main__"
    exec("if True:" + code, ns)  # noqa: S102 — 测试里跑模块尾部
    out = capsys.readouterr().out.strip()
    data = json.loads(out)
    assert data["client"] == "eml-dir"
    assert data["status"] == "unavailable"
    assert data["reason"].startswith("RuntimeError:")
