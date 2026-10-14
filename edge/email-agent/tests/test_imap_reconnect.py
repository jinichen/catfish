"""10/14 Windows: 读一封信时服务器中途断开 (imaplib.IMAP4.abort: socket error: EOF),
原始 traceback 直接糊在邮件页上。现在: 只读操作重连再试一次; 还不行有档案给档案,
没档案给一句人话; 写操作不重试 (重试可能做两遍)。"""
from __future__ import annotations

import imaplib

import pytest

from catfish_email.adapters.base import ClientNotRunningError
from catfish_email.adapters.imap_mail import ImapAdapter, ImapConfig

from tests.test_adapter_imap import FakeIMAP
from tests.test_imap_archive_view import _archive, _store
from tests.test_imap_sync import CountingIMAP, isolated_index, make  # noqa: F401  (fixture)


class DroppingIMAP(FakeIMAP):
    """前 n 次 FETCH 整封时服务器把连接关了。"""

    def __init__(self, drops: int, **kw):
        super().__init__(**kw)
        self.drops = drops

    def uid(self, command, *args):
        if command == "fetch" and "BODY.PEEK[]" in str(args[1:]) and self.drops > 0:
            self.drops -= 1
            raise imaplib.IMAP4.abort("socket error: EOF")
        if command == "store" and self.drops > 0:
            self.drops -= 1
            raise imaplib.IMAP4.abort("socket error: EOF")
        return super().uid(command, *args)

    def shutdown(self):
        pass


def _adapter(fake, monkeypatch):
    connects = []

    def factory(host, port, timeout=None):
        connects.append(1)
        return fake

    monkeypatch.setattr("catfish_email.adapters.imap_mail.imaplib.IMAP4_SSL", factory)
    a = ImapAdapter(ImapConfig(host="imap.example.cn", user="me@example.cn", password="s3cret"))
    return a, connects


def test_one_drop_reconnects_and_reads(monkeypatch):
    a, connects = _adapter(DroppingIMAP(drops=1), monkeypatch)
    msg = a.read_message("imap|INBOX|1|8418")
    assert msg.subject
    assert len(connects) == 2                       # 断了以后真的新建了连接


def test_two_drops_give_a_plain_sentence_not_a_traceback(monkeypatch):
    a, _ = _adapter(DroppingIMAP(drops=2), monkeypatch)
    with pytest.raises(ClientNotRunningError) as info:
        a.read_message("imap|INBOX|1|8418")
    text = str(info.value)
    assert "中途断开" in text and "imap.example.cn" in text
    assert "Traceback" not in text and "s3cret" not in text


def test_writes_are_not_retried(monkeypatch):
    a, connects = _adapter(DroppingIMAP(drops=1), monkeypatch)
    with pytest.raises(imaplib.IMAP4.abort):
        a.mark_read("imap|INBOX|1|8418")
    assert len(connects) == 1


class DroppingCounting(CountingIMAP):
    drop_all = False

    def uid(self, command, *args):
        if self.drop_all and command == "fetch" and "BODY.PEEK[]" in str(args[1:]):
            raise imaplib.IMAP4.abort("socket error: EOF")
        return super().uid(command, *args)

    def shutdown(self):
        pass


def test_server_keeps_dropping_but_local_archive_has_it(isolated_index, monkeypatch):  # noqa: F811
    fake = DroppingCounting()
    adapter = make(fake, monkeypatch)
    adapter.sync_folder("INBOX", "Inbox")
    _store(adapter, fake, b"8417", "imap:INBOX:1:8417")
    msg_id = _archive("imap:INBOX:1:8417")
    fake.drop_all = True
    got = adapter.read_message(msg_id)
    assert got.subject, "服务器读不到时, 档案里有就给档案里的"


def test_cli_never_dumps_a_traceback(tmp_path, monkeypatch, capsys):
    from catfish_email import __main__ as cli

    monkeypatch.setenv("CATFISH_HOME", str(tmp_path))

    def boom(argv):
        raise imaplib.IMAP4.abort("socket error: EOF")

    monkeypatch.setattr(cli, "_main", boom)
    assert cli.main(["read", "imap|INBOX|1|1"]) == 1
    err = capsys.readouterr().err
    assert "Traceback" not in err and "邮件程序出错了" in err
    assert "Traceback" in (tmp_path / "email-cli-error.log").read_text(encoding="utf-8")
