"""转发邮件, 保留原排版 (11/4)。

鸿波:「转发要保留原邮件排版」。试过的路 (真机, 邮件 App):
  - AppleScript `forward`: 原排版在, 但一写正文 (`set content`) 整段原文就被清掉, 附言加不进去
  - 打开 X-Unsent .eml: 邮件 App 只当查看窗口打开, 不能编辑 / 存草稿
  - `html content` (邮件 App 隐藏属性) 建草稿: HTML、表格、data: 内嵌图、附件都保住 ← 用这条

所以这里从原邮件的完整 RFC822 拼一份 HTML: 附言 + 「转发的邮件」头 + 原 HTML 正文 (内嵌图片
cid: 换成 data: URI, 不然转出去是裂图); 真附件另列, 由草稿照常带上。
"""
from __future__ import annotations

import base64
import email
import email.policy
import html
import re
from email.message import EmailMessage

MAX_INLINE_IMAGE_BYTES = 5 * 1024 * 1024


def _header(msg: EmailMessage, name: str) -> str:
    value = msg.get(name)
    return str(value).strip() if value else ""


def _body_part(msg: EmailMessage, subtype: str) -> EmailMessage | None:
    for part in msg.walk():
        if part.get_content_type() == f"text/{subtype}" and not part.is_attachment():
            return part
    return None


def _cid(part: EmailMessage) -> str:
    return (_header(part, "Content-ID")).strip("<>")


def parse_forward(raw: bytes, note: str) -> tuple[str, list[tuple[str, bytes]]]:
    """返回 (要写进草稿的整份 HTML, [(附件文件名, 内容)])。"""
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    assert isinstance(msg, EmailMessage)

    html_part = _body_part(msg, "html")
    if html_part is not None:
        original = html_part.get_content()
    else:
        text_part = _body_part(msg, "plain")
        text = text_part.get_content() if text_part is not None else ""
        original = f'<div style="white-space:pre-wrap">{html.escape(text)}</div>'

    attachments: list[tuple[str, bytes]] = []
    for part in msg.walk():
        is_body_text = part.get_content_type() in ("text/plain", "text/html") and not part.is_attachment()
        if part.is_multipart() or is_body_text:
            continue
        payload = part.get_payload(decode=True) or b""
        cid = _cid(part)
        if cid and part.get_content_maintype() == "image" and f"cid:{cid}" in original \
                and len(payload) <= MAX_INLINE_IMAGE_BYTES:
            data = base64.b64encode(payload).decode("ascii")
            original = original.replace(f"cid:{cid}", f"data:{part.get_content_type()};base64,{data}")
            continue
        name = part.get_filename()
        if name or part.is_attachment():
            attachments.append((name or "附件", payload))

    rows = [("发件人", _header(msg, "From")), ("时间", _header(msg, "Date")), ("收件人", _header(msg, "To")),
            ("抄送", _header(msg, "Cc")), ("主题", _header(msg, "Subject"))]
    head = "<br>".join(f"<b>{k}:</b> {html.escape(v)}" for k, v in rows if v)
    note_html = "<br>".join(html.escape(line) for line in note.rstrip().splitlines())
    block = (
        f'<div>{note_html}</div><br>'
        '<div style="border-top:1px solid #ccc;padding-top:6px;color:#555;font-size:13px">'
        f'---------- 转发的邮件 ----------<br>{head}</div><br>'
    )
    body_tag = re.search(r"<body[^>]*>", original, re.IGNORECASE)
    if body_tag:
        combined = original[:body_tag.end()] + block + original[body_tag.end():]
    else:
        combined = f"<html><body>{block}{original}</body></html>"
    return combined, attachments
