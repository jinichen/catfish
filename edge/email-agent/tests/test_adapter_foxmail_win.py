"""Windows Foxmail adapter tests.

测试使用仓库记录的 .box 格式夹具，验证的是适配器的发现、筛选、稳定 ID 和
只读边界；真实 Foxmail 版本的二进制格式仍需要在 Windows 现场用导出的样本
做一次验收。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from catfish_email.adapters.base import DataNotFoundError, ListFilter, NotSupportedError
from catfish_email.adapters.foxmail_win import FoxmailWinAdapter


def _make_storage(make_box_file, tmp_path: Path):
    source = make_box_file([
        {"subject": "旧通知", "body": "不用处理", "flags": 1},
        {"subject": "Foxmail 周报", "body": "请查看附件和进度"},
    ])
    storage = tmp_path / "Storage"
    box_dir = storage / "hongbo@example.com" / "Mail"
    box_dir.mkdir(parents=True)
    target = box_dir / "Inbox.box"
    target.write_bytes(source.read_bytes())
    (box_dir / "Inbox.ind").write_bytes(b"")
    return storage


def test_windows_foxmail_lists_and_reads_box_messages(make_box_file, tmp_path):
    storage = _make_storage(make_box_file, tmp_path)
    adapter = FoxmailWinAdapter(profiles_dir=storage)

    accounts = adapter.list_accounts()
    assert accounts[0].address == "hongbo@example.com"
    messages = adapter.list_messages(ListFilter(folder="Inbox", limit=10))
    assert {message.subject for message in messages} == {"Foxmail 周报", "旧通知"}
    report = next(message for message in messages if message.subject == "Foxmail 周报")
    assert report.body_text.strip() == "请查看附件和进度"

    full = adapter.read_message(report.id)
    assert full.body_text.strip() == "请查看附件和进度"
    assert full.sender == "张三 <zhang@example.com>"
    assert full.recipients == ("陈鸿波 <hongbo@example.com>",)


def test_windows_foxmail_filters_unread_and_searches(make_box_file, tmp_path):
    storage = _make_storage(make_box_file, tmp_path)
    adapter = FoxmailWinAdapter(profiles_dir=storage)

    unread = adapter.list_messages(ListFilter(folder="Inbox", unread_only=True))
    assert [message.subject for message in unread] == ["Foxmail 周报"]
    found = adapter.search("附件", folder="Inbox")
    assert [message.subject for message in found] == ["Foxmail 周报"]


def test_windows_foxmail_reads_the_requested_eml_sibling(tmp_path):
    inbox = tmp_path / "Storage" / "hongbo@example.com" / "Mail" / "Inbox"
    inbox.mkdir(parents=True)
    (inbox / "001.eml").write_text(
        "Subject: first\nFrom: a@example.com\n\nfirst body\n", encoding="utf-8"
    )
    (inbox / "002.eml").write_text(
        "Subject: second\nFrom: b@example.com\n\nsecond body\n", encoding="utf-8"
    )

    adapter = FoxmailWinAdapter(profiles_dir=tmp_path / "Storage")
    messages = adapter.list_messages(ListFilter(folder="Inbox", limit=10))
    second = next(message for message in messages if message.subject == "second")

    assert adapter.read_message(second.id).body_text == "second body\n"


def test_windows_foxmail_supports_explicit_root(monkeypatch, make_box_file, tmp_path):
    storage = _make_storage(make_box_file, tmp_path)
    monkeypatch.setenv("CATFISH_FOXMAIL_ROOT", str(storage))
    adapter = FoxmailWinAdapter()
    assert adapter.list_accounts()[0].address == "hongbo@example.com"


def test_windows_foxmail_missing_message_is_data_error(make_box_file, tmp_path):
    adapter = FoxmailWinAdapter(profiles_dir=_make_storage(make_box_file, tmp_path))
    with pytest.raises(DataNotFoundError):
        adapter.read_message("foxmail-win|hongbo%40example.com|Mail%2Fmissing.box|0")


def test_windows_foxmail_does_not_claim_writes(make_box_file, tmp_path):
    adapter = FoxmailWinAdapter(profiles_dir=_make_storage(make_box_file, tmp_path))
    with pytest.raises(NotSupportedError):
        adapter.create_draft(to=("a@example.com",), subject="x", body="y")
