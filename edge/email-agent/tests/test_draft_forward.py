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


# ── 11/4 保留原排版: 邮件 App 那条路出整份 HTML ─────────────────────

from email.message import EmailMessage  # noqa: E402

from catfish_email.forward_mime import parse_forward  # noqa: E402


def _rich_original() -> bytes:
    m = EmailMessage()
    m["From"] = "办公室 <office@example.com>"
    m["To"] = "a@example.com"
    m["Subject"] = "示例通知"
    m["Date"] = "Mon, 05 Jan 2026 09:00:00 +0800"
    m.set_content("纯文字版")
    m.add_alternative('<html><body><table><tr><td><b>加粗</b></td></tr></table><img src="cid:logo1"></body></html>',
                      subtype="html")
    m.get_payload()[1].add_related(b"\x89PNG fake", maintype="image", subtype="png", cid="<logo1>")
    m.add_attachment(b"%PDF fake", maintype="application", subtype="pdf", filename="议程.pdf")
    return m.as_bytes()


def test_parse_forward_keeps_layout_inlines_images_and_lists_real_attachments():
    html_out, files = parse_forward(_rich_original(), "各位:\n请看下方通知。")
    assert html_out.index("各位:<br>请看下方通知。") < html_out.index("---------- 转发的邮件 ----------") \
        < html_out.index("<table>")
    assert "<b>加粗</b>" in html_out
    assert "cid:logo1" not in html_out and "data:image/png;base64," in html_out  # 内嵌图转成 data:, 不裂图
    assert "办公室 &lt;office@example.com&gt;" in html_out
    assert files == [("议程.pdf", b"%PDF fake")]  # 内嵌图不重复当附件


def test_parse_forward_of_plain_text_mail_keeps_line_breaks():
    m = EmailMessage()
    m["From"] = "a@example.com"
    m["Subject"] = "s"
    m.set_content("第一行\n第二行 <不是标签>")
    html_out, files = parse_forward(m.as_bytes(), "附言")
    assert "white-space:pre-wrap" in html_out and "&lt;不是标签&gt;" in html_out and files == []


class _HtmlSource(_Source):
    name = "apple_mail"

    def read_raw_source(self, message_id):
        return _rich_original()

    def create_draft(self, *, to, subject, body, cc=(), bcc=(), in_reply_to=None, account=None,
                     replaces=None, attachments=(), html_body=None):
        import os
        self.drafts.append({"body": body, "html": html_body,
                            "attachments": [(os.path.basename(p), open(p, "rb").read()) for p in attachments],
                            "dirs": {os.path.dirname(p) for p in attachments}})
        return "apple_mail|Work|9"


def test_cli_forward_uses_html_when_the_mail_app_can_take_it(tmp_path):
    import os
    src = _HtmlSource(tmp_path)
    # 原邮件头仍走 read_message (校验原信在); 假源只认 imap 那个 id, 这里转一下
    src.read_message = lambda _id: _Source(tmp_path).read_message("imap|INBOX|1|7")  # type: ignore[method-assign]
    assert _run(src, _args(forward="apple_mail|Work|7")) == 0
    (d,) = src.drafts
    assert d["html"] and "<b>加粗</b>" in d["html"] and "各位: 请看下方通知。" in d["html"]
    assert d["attachments"] == [("议程.pdf", b"%PDF fake")]
    assert all(not os.path.exists(x) for x in d["dirs"])  # 导出的原附件存完草稿就删


# ── IMAP 邮箱也走 HTML (11/4 鸿波「IMAP 方式会不会有问题」: 原来 IMAP 只能纯文字转发) ──

import email as _email  # noqa: E402
import email.policy as _policy  # noqa: E402

from tests.test_imap_writes import adapter  # noqa: E402,F401  (pytest fixture)


def test_imap_draft_with_html_body_is_alternative_plus_attachment(adapter, tmp_path):  # noqa: F811
    pdf = tmp_path / "议程.pdf"
    pdf.write_bytes(b"%PDF fake")
    draft_id = adapter.create_draft(to=["a@b.cn"], subject="转发: 示例", body="纯文字后备",
                                    html_body="<html><body><table><tr><td>T</td></tr></table></body></html>",
                                    attachments=[str(pdf)])
    parsed = _email.message_from_bytes(adapter.read_raw_source(draft_id), policy=_policy.default)
    assert parsed.get_body(("html",)).get_content().count("<table>") == 1
    assert parsed.get_body(("plain",)).get_content().strip() == "纯文字后备"
    assert [p.get_filename() for p in parsed.iter_attachments()] == ["议程.pdf"]


def test_cli_forward_on_imap_takes_the_html_path(tmp_path):
    class _ImapLike(_HtmlSource):
        name = "imap"

    src = _ImapLike(tmp_path)
    assert _run(src, _args(forward="imap|INBOX|1|7")) == 0
    assert src.drafts[0]["html"] and "<b>加粗</b>" in src.drafts[0]["html"]
