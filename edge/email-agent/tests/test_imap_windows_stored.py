"""Windows: tool-bridge 起的 CLI 没有 IMAP 环境变量时, 读 Companion 存的配置 (9/26)。"""
from __future__ import annotations

import json

import pytest

from catfish_email.adapters import imap_config


@pytest.fixture
def win(tmp_path, monkeypatch):
    for var in ("CATFISH_IMAP_HOST", "CATFISH_IMAP_USER", "CATFISH_IMAP_PASSWORD",
                "CATFISH_SMTP_HOST", "CATFISH_SMTP_PORT"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(imap_config.sys, "platform", "win32")
    src = tmp_path / "imap-source.json"
    monkeypatch.setattr(imap_config, "_source_json", lambda: src)
    asked = []
    monkeypatch.setattr(imap_config, "_read_windows_credential",
                        lambda target: asked.append(target) or "pw")
    return src, asked


def test_reads_json_and_credential_manager(win):
    src, asked = win
    src.write_text(json.dumps({"host": "imap.corp.cn", "user": "me@corp.cn", "port": 993,
                               "retention": "2w", "smtp_host": "smtp.corp.cn"}), encoding="utf-8")
    cfg = imap_config.config_from_env()
    assert (cfg.host, cfg.user, cfg.password, cfg.retention) == ("imap.corp.cn", "me@corp.cn", "pw", "2w")
    assert asked == ["imap:me@corp.cn.catfish"], "必须跟 keyring-rs 写入端的 target 一致"
    assert imap_config.os.environ["CATFISH_SMTP_HOST"] == "smtp.corp.cn"


def test_env_injected_by_companion_still_wins(win, monkeypatch):
    src, asked = win
    monkeypatch.setenv("CATFISH_IMAP_HOST", "h")
    monkeypatch.setenv("CATFISH_IMAP_USER", "u")
    monkeypatch.setenv("CATFISH_IMAP_PASSWORD", "p")
    assert imap_config.config_from_env().password == "p"
    assert asked == []


def test_no_json_or_no_password_means_not_configured(win, monkeypatch):
    src, _ = win
    assert imap_config.config_from_env() is None
    src.write_text(json.dumps({"host": "h", "user": "u"}), encoding="utf-8")
    monkeypatch.setattr(imap_config, "_read_windows_credential", lambda target: None)
    assert imap_config.config_from_env() is None


def test_macos_never_touches_the_credential_store(win, monkeypatch):
    src, asked = win
    monkeypatch.setattr(imap_config.sys, "platform", "darwin")
    src.write_text(json.dumps({"host": "h", "user": "u"}), encoding="utf-8")
    assert imap_config.config_from_env() is None
    assert asked == []
