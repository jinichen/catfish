"""账号命令在所有客户端失败时必须返回真实错误。"""

from __future__ import annotations

import argparse
import json

from catfish_email.__main__ import _cmd_accounts
from catfish_email.adapters.base import DataNotFoundError, EmailAdapter, ListFilter


class _FailingAccountsAdapter(EmailAdapter):
    def __init__(self, name: str, reason: str) -> None:
        self._name = name
        self._reason = reason

    @property
    def name(self) -> str:
        return self._name

    def list_accounts(self):
        raise DataNotFoundError(self._reason)

    def list_messages(self, filt: ListFilter):
        raise DataNotFoundError(self._reason)

    def read_message(self, message_id: str):
        raise DataNotFoundError(self._reason)

    def search(self, query: str, *, account=None, folder="Inbox", limit=30):
        raise DataNotFoundError(self._reason)


def test_cmd_accounts_all_adapters_fail_returns_error_instead_of_empty(capsys):
    adapters = [
        _FailingAccountsAdapter("outlook_win", "Outlook COM 不可用"),
        _FailingAccountsAdapter("foxmail_win", "Foxmail Storage 不存在"),
    ]

    rc = _cmd_accounts(adapters, argparse.Namespace(json=True))

    assert rc == 1
    captured = capsys.readouterr()
    assert json.loads(captured.out) == []
    assert "outlook_win" in captured.err
    assert "foxmail_win" in captured.err
