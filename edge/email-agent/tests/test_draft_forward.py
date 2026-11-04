"""11/4: 转发 —— 原来没有这条路, 小鲶写「原文见下方」, 草稿里下方什么都没有。

现在 draft --forward <原邮件 id>: 原邮件头 + 原文接在正文后面, 原附件一并带上; 取不全就不建草稿。
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from catfish_email import cli_action
from catfish_email.adapters.base import Attachment, DataNotFoundError, Message


class _Source:
    name = "imap"
    supports_drafts = True

    def __init__(self, tmp_path, *, html_only=False, broken_attachment=False):
        self.tmp_path = tmp_path
        self.html_only = html_only
        self.broken_attachment = broken_attachment
        self.drafts: list[dict] = []

    def read_message(self, message_id):
        if message_id != "imap|INBOX|1|7":
            raise DataNotFoundError("没有这封")
        return Message(
            id=message_id, account="me@example.com", folder="Inbox",
            subject="示例通知: 下周例会改到周三", sender="办公室 <office@example.com>",
            recipients=("a@example.com", "b@example.com"), cc=("c@example.com",),
            date="2026-01-05T09:00:00+08:00",
            attachments=(Attachment(filename="议程.pdf", size_bytes=10),),
            body_text="" if self.html_only else "例会改到周三上午九点。",
            body_html="<p>例会改到<b>周三</b>上午九点。</p>",
        )

    def export_attachment(self, message_id, filename):
        if self.broken_attachment:
            raise DataNotFoundError("附件没下载")
        p = self.tmp_path / filename
        p.write_bytes(b"%PDF fake")
        return p

    def create_draft(self, *, to, subject, body, cc=(), bcc=(), in_reply_to=None, account=None,
                     replaces=None, attachments=()):
        self.drafts.append({"body": body, "attachments": list(attachments), "to": list(to)})
        return "imap|Drafts|1|9"


def _args(**kw):
    base = dict(to="team@example.com", cc="", bcc="", subject="转发: 示例通知", body="各位: 请看下方通知。",
                body_file=None, in_reply_to=None, replace=None, account=None, json=True, attach=[],
                keep_attachment=[], forward="imap|INBOX|1|7")
    base.update(kw)
    return SimpleNamespace(**base)


def _run(src, args):
    with patch("catfish_email.last_draft.record"):
        return cli_action._cmd_draft([src], args)


def test_forward_appends_original_and_carries_attachments(tmp_path, capsys):
    src = _Source(tmp_path)
    assert _run(src, _args()) == 0
    (d,) = src.drafts
    body = d["body"]
    assert body.startswith("各位: 请看下方通知。")
    assert "---------- 转发的邮件 ----------" in body
    for piece in ("发件人: 办公室 <office@example.com>", "收件人: a@example.com, b@example.com",
                  "抄送: c@example.com", "主题: 示例通知: 下周例会改到周三", "例会改到周三上午九点。"):
        assert piece in body
    assert [p.rsplit("/", 1)[-1] for p in d["attachments"]] == ["议程.pdf"]


def test_forward_of_html_only_mail_uses_stripped_html(tmp_path):
    src = _Source(tmp_path, html_only=True)
    assert _run(src, _args()) == 0
    assert "例会改到周三上午九点。" in src.drafts[0]["body"]


def test_forward_refuses_when_an_attachment_cannot_be_taken(tmp_path, capsys):
    src = _Source(tmp_path, broken_attachment=True)
    assert _run(src, _args()) == 1
    assert src.drafts == []
    assert "议程.pdf" in capsys.readouterr().err


def test_forward_refuses_unknown_original(tmp_path, capsys):
    src = _Source(tmp_path)
    assert _run(src, _args(forward="imap|INBOX|1|999")) == 1
    assert src.drafts == []
    assert "读不出来" in capsys.readouterr().err


def test_plain_draft_unchanged(tmp_path):
    src = _Source(tmp_path)
    assert _run(src, _args(forward=None)) == 0
    assert src.drafts[0]["body"] == "各位: 请看下方通知。" and src.drafts[0]["attachments"] == []
