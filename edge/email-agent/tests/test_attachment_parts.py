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
