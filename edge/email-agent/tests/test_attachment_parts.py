"""附件口径 (rfc822.attachment_parts) —— 11/07 鸿波: 已发送里带 PDF 的信显示没附件。

Apple Mail 发出的信把附件写成 ``Content-Disposition: inline; filename=…``,
还夹在被切成几段的 text/html 中间。以前只认 ``attachment``, 一个都没认出来。
"""
from __future__ import annotations

import email
import email.policy
from pathlib import Path

from catfish_email import rfc822_util as rfc822
from catfish_email.adapters.apple_mail_emlx import (
    _save_attachment_payload_from_source,
    _walk_attachments,
)

PDF = b"%PDF-1.4 fake"
B64 = "JVBERi0xLjQgZmFrZQ=="


def _apple_mail_sent() -> bytes:
    """真机 MIME 树原样 (名字照抄, 内容换成假的)。"""
    return (
        "From: a@example.com\r\nSubject: t\r\nMIME-Version: 1.0\r\n"
        "Content-Type: multipart/alternative; boundary=ALT\r\n\r\n"
        "--ALT\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n正文\r\n"
        "--ALT\r\nContent-Type: multipart/mixed; boundary=MIX\r\n\r\n"
        "--MIX\r\nContent-Type: text/html; charset=utf-8\r\n\r\n<p>正文</p>\r\n"
        "--MIX\r\nContent-Type: application/pdf; name=\"=?utf-8?B?5YWa5aeU5Lya6K6w5b2VLnBkZg==?=\"\r\n"
        "Content-Disposition: inline; filename*=utf-8''%E5%85%9A%E5%A7%94%E4%BC%9A%E8%AE%B0%E5%BD%95.pdf\r\n"
        f"Content-Transfer-Encoding: base64\r\n\r\n{B64}\r\n"
        "--MIX\r\nContent-Type: text/html; charset=utf-8\r\n\r\n<p></p>\r\n"
        "--MIX\r\nContent-Type: application/pdf\r\n"
        "Content-Disposition: inline; filename=\"59.pdf\"\r\n"
        f"Content-Transfer-Encoding: base64\r\n\r\n{B64}\r\n"
        "--MIX\r\nContent-Type: text/html; charset=utf-8\r\n\r\n<p></p>\r\n"
        "--MIX--\r\n--ALT--\r\n"
    ).encode("utf-8")


def _parse(raw: bytes):
    return email.message_from_bytes(raw, policy=email.policy.default)


def test_apple_mail_inline_pdfs_are_attachments():
    msg = _parse(_apple_mail_sent())
    got = _walk_attachments(msg)
    assert [a.filename for a in got] == ["党委会记录.pdf", "59.pdf"]
    assert all(a.size_bytes == len(PDF) for a in got)
    assert rfc822.has_attachments(msg)
    assert [m[0] for m in rfc822.attachment_meta(msg)] == ["党委会记录.pdf", "59.pdf"]


def test_inline_pdf_can_be_exported(tmp_path: Path):
    src = tmp_path / "m.eml"
    src.write_bytes(_apple_mail_sent())
    out = _save_attachment_payload_from_source(str(src), "59.pdf")
    assert out is not None and out.read_bytes() == PDF


def test_cid_referenced_named_image_is_not_an_attachment():
    """带文件名、但正文用 cid: 画出来的图 (签名档) 仍然不算附件。"""
    raw = (
        "MIME-Version: 1.0\r\nContent-Type: multipart/related; boundary=R\r\n\r\n"
        "--R\r\nContent-Type: text/html; charset=utf-8\r\n\r\n<img src=\"cid:Logo@X\">\r\n"
        "--R\r\nContent-Type: image/png; name=\"logo.png\"\r\nContent-Disposition: inline; filename=\"logo.png\"\r\n"
        "Content-ID: <logo@x>\r\nContent-Transfer-Encoding: base64\r\n\r\niVBORw0KGgo=\r\n"
        "--R\r\nContent-Type: image/png\r\nContent-Disposition: inline; filename=\"拖进来的图.png\"\r\n"
        "Content-Transfer-Encoding: base64\r\n\r\niVBORw0KGgo=\r\n"
        "--R--\r\n"
    ).encode("utf-8")
    msg = _parse(raw)
    assert [a.filename for a in _walk_attachments(msg)] == ["拖进来的图.png"]


def test_plain_text_mail_has_none():
    msg = _parse(b"Subject: x\r\nContent-Type: text/plain\r\n\r\nhi\r\n")
    assert _walk_attachments(msg) == []
    assert not rfc822.has_attachments(msg)


def _apple_mail_draft() -> bytes:
    """11/13 鸿波「为什么附件又显示不出来」: 带 xlsx + 2 个 PDF 的草稿。真机 MIME 树 (结构照抄, 内容换假的):

        multipart/alternative
        ├── text/plain
        └── multipart/related
            ├── text/html   正文末尾每个附件一个 <object type="application/x-apple-msg-attachment" data="cid:…">
            ├── application/pdf  inline      Content-ID <B>
            ├── application/vnd…sheet  attachment  Content-ID <X>
            └── application/pdf  inline      Content-ID <A>

    以前「正文里出现过 cid:」就算内嵌图 → 两个 PDF 被剔出附件列表, 只剩 xlsx;
    正文里三个 object 在 webview 里显示成「Blocked Plug-in」。
    """
    obj = '<span class="Apple-string-attachment"><object type="application/x-apple-msg-attachment" width="68" data="cid:{}"></object></span>'
    html = "<html><body><p>附件是结算材料</p>" + "".join(obj.format(c) for c in ("X-1", "A-1", "B-1")) + "</body></html>"
    return (
        "From: a@example.com\r\nSubject: t\r\nMIME-Version: 1.0\r\n"
        "Content-Type: multipart/alternative; boundary=ALT\r\n\r\n"
        "--ALT\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n附件是结算材料\r\n"
        "--ALT\r\nContent-Type: multipart/related; type=\"text/html\"; boundary=REL\r\n\r\n"
        f"--REL\r\nContent-Type: text/html; charset=utf-8\r\n\r\n{html}\r\n"
        "--REL\r\nContent-Type: application/pdf; name=\"B.pdf\"\r\nContent-Disposition: inline; filename=\"B.pdf\"\r\n"
        f"Content-Id: <B-1>\r\nContent-Transfer-Encoding: base64\r\n\r\n{B64}\r\n"
        "--REL\r\nContent-Type: application/vnd.openxmlformats-officedocument.spreadsheetml.sheet; name=\"X.xlsx\"\r\n"
        "Content-Disposition: attachment; filename=\"X.xlsx\"\r\n"
        f"Content-Id: <X-1>\r\nContent-Transfer-Encoding: base64\r\n\r\n{B64}\r\n"
        "--REL\r\nContent-Type: application/pdf; name=\"A.pdf\"\r\nContent-Disposition: inline; filename=\"A.pdf\"\r\n"
        f"Content-Id: <A-1>\r\nContent-Transfer-Encoding: base64\r\n\r\n{B64}\r\n"
        "--REL--\r\n--ALT--\r\n"
    ).encode("utf-8")


def test_apple_mail_draft_object_placeholders_do_not_hide_attachments():
    msg = _parse(_apple_mail_draft())
    assert sorted(a.filename for a in _walk_attachments(msg)) == ["A.pdf", "B.pdf", "X.xlsx"]


def test_apple_mail_draft_body_shows_attachment_chips_not_plugins(tmp_path: Path):
    from catfish_email.adapters.apple_mail_emlx import _extract_display_html_from_source_file

    src = tmp_path / "d.eml"
    src.write_bytes(_apple_mail_draft())
    html = _extract_display_html_from_source_file(str(src))
    assert "<object" not in html.lower()
    assert "附件是结算材料" in html
    assert html.index("📎 X.xlsx") < html.index("📎 A.pdf") < html.index("📎 B.pdf")


def test_img_cid_still_counts_as_inline_image_not_attachment():
    raw = (
        "From: a@example.com\r\nSubject: t\r\nMIME-Version: 1.0\r\n"
        "Content-Type: multipart/related; boundary=REL\r\n\r\n"
        "--REL\r\nContent-Type: text/html; charset=utf-8\r\n\r\n<p><img src=\"cid:logo@x\"></p>\r\n"
        "--REL\r\nContent-Type: image/png; name=\"logo.png\"\r\nContent-Disposition: inline; filename=\"logo.png\"\r\n"
        f"Content-Id: <logo@x>\r\nContent-Transfer-Encoding: base64\r\n\r\n{B64}\r\n"
        "--REL--\r\n"
    ).encode("utf-8")
    assert rfc822.attachment_parts(_parse(raw)) == []


def test_draft_rebuild_html_drops_attachment_placeholders():
    from catfish_email.adapters.apple_mail_drafts import _draft_html_file

    path = _draft_html_file(_parse(_apple_mail_draft()))
    try:
        text = Path(path).read_text(encoding="utf-8")
    finally:
        Path(path).unlink()
    assert "<object" not in text.lower() and "📎" not in text and "附件是结算材料" in text
