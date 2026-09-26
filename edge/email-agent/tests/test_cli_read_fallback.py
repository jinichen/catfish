"""_cmd_read 无前缀 id 的逐客户端回退 (9/26)。"""
from __future__ import annotations

import json

from catfish_email.__main__ import _cmd_read
from catfish_email.adapters.base import EmailAdapterError, Message

from tests.test_cli_main import _FakeAdapter, _make_read_args


class _BrokenReadAdapter(_FakeAdapter):
    def read_message(self, message_id):
        raise EmailAdapterError("Outlook COM: 没有配置账号")


def test_cmd_read_unprefixed_id_skips_broken_client_and_reaches_imap(capsys):
    """9/26: 任何一个来源出错都不该挡住后面的来源。起因是 Windows 当时候选顺序
    outlook-win → imap, Outlook COM 一报错就退出 1, 存着这封信的 IMAP 没被问到。
    (Windows 配了 IMAP 后已不再用 Outlook, 见 inbox._windows_candidates; 这条守的是
    CLI 回退本身。)"""
    m = Message(id="legacy-id-1", account="me@corp.cn", folder="Inbox",
                subject="采购合同", sender="x@y.com", date="2026-09-23T10:23:00", is_read=True)
    rc = _cmd_read(
        [_BrokenReadAdapter(name="outlook_win"), _FakeAdapter(name="imap", messages=[m])],
        _make_read_args(m.id, mark_read=False),
    )
    assert rc == 0
    assert json.loads(capsys.readouterr().out)["subject"] == "采购合同"


def test_cmd_read_reports_every_client_error_when_all_fail(capsys):
    rc = _cmd_read(
        [_BrokenReadAdapter(name="outlook_win"), _BrokenReadAdapter(name="imap")],
        _make_read_args("legacy-id-2", mark_read=False),
    )
    assert rc == 1
    err = capsys.readouterr().err
    assert "[outlook_win]" in err and "[imap]" in err
