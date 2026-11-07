"""Apple Mail 账号编号 → 账号名 / 邮箱 (11/07): Mail 开着时记下, EMLX 兜底时换回来。"""
from __future__ import annotations

from unittest.mock import patch

from catfish_email.adapters import apple_mail as am
from catfish_email.adapters import apple_mail_account_map as account_map
from catfish_email.adapters import apple_mail_emlx_path as emlx_path
from catfish_email.adapters.apple_mail import AppleMailAdapter
from catfish_email.adapters.apple_mail_osascript import ClientNotRunningError

FS, RS = "\x1f", "\x1e"
UUID = "36984457-CE3B-4239-92E7-3374CE655974"


def test_mail开着时_顺手记下编号表():
    ids = f"{UUID}{FS}Chinatelecom{FS}ffchenhb@chinatelecom.cn{RS}AAAA{FS}Google{FS}x@gmail.com{RS}"
    accounts = f"Chinatelecom{FS}ffchenhb@chinatelecom.cn{FS}0{RS}"

    def run(script, **_):
        return ids if "id of acc" in script else accounts

    with patch.object(am, "_is_mail_running", return_value=True), patch.object(am, "_run_osascript", side_effect=run):
        a = AppleMailAdapter()
        a.list_accounts()
        assert account_map.load() == {}   # 平时取列表不多问 Mail
        a.refresh_account_map()
    assert account_map.load()[UUID] == {"name": "Chinatelecom", "address": "ffchenhb@chinatelecom.cn"}


def test_accounts命令_一个客户端没权限_别的照列(capsys):
    import json
    from types import SimpleNamespace

    from catfish_email.cli_read import _cmd_accounts

    class Denied:
        name = "foxmail_mac"

        def list_accounts(self):
            raise PermissionError(1, "Operation not permitted")

    class Ok:
        name = "apple_mail"
        refreshed = False

        def list_accounts(self):
            from catfish_email.adapters.base import Account
            return [Account(name="Chinatelecom", address="ffchenhb@chinatelecom.cn")]

        def refresh_account_map(self):
            Ok.refreshed = True

    assert _cmd_accounts([Denied(), Ok()], SimpleNamespace(json=True)) == 0
    out = capsys.readouterr()
    assert json.loads(out.out)[0]["address"] == "ffchenhb@chinatelecom.cn"
    assert "foxmail_mac" in out.err and Ok.refreshed


def test_表还新_不再问Mail():
    account_map.save({UUID: {"name": "Chinatelecom", "address": "a@chinatelecom.cn"}})
    calls = []
    account_map.refresh(lambda s: calls.append(s) or "")
    assert calls == []


def test_EMLX兜底_编号目录换回账号名和邮箱_也能按账号名找目录(tmp_path, monkeypatch):
    fake = tmp_path / "Mail" / "V10"
    (fake / UUID).mkdir(parents=True)
    (fake / "FF204811-C0B1-4B99-8651-4A2A2447D54D").mkdir()
    monkeypatch.setattr(emlx_path, "_detect_mail_data_dir", lambda: fake)
    account_map.save({UUID: {"name": "Chinatelecom", "address": "ffchenhb@chinatelecom.cn"}})

    def denied(script, **_):
        raise ClientNotRunningError("没权限")

    with patch.object(am, "_is_mail_running", return_value=True), patch.object(am, "_run_osascript", side_effect=denied):
        a = AppleMailAdapter()
        accounts = a.list_accounts()
        got = {x.name: x.address for x in accounts}
        assert got["Chinatelecom"] == "ffchenhb@chinatelecom.cn"
        assert "FF204811-C0B1-4B99-8651-4A2A2447D54D" in got   # 表里没有的照旧显示编号
        assert a._account_dir_emlx("Chinatelecom").name == UUID
        assert a._account_dir_emlx("ffchenhb@chinatelecom.cn").name == UUID


def test_坏表_当没有():
    account_map.map_path().parent.mkdir(parents=True, exist_ok=True)
    account_map.map_path().write_text("{坏的", encoding="utf-8")
    assert account_map.load() == {}
