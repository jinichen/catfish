"""IMAP 的文件夹名: modified UTF-7 编解码 + 角色识别。

9/18 从 imap_mail.py 拆出来 (那个文件补完写操作之后 1018 行, 越过仓库的
800 行红线)。拆这一块是因为它**跟 IMAP 协议无关** —— 纯粹是字符串编码和
名字到角色的映射, 不碰连接、不碰邮件。单独放也更好测。
"""
from __future__ import annotations

import binascii
import re

#: 解码后的文件夹名 → 统一角色。
#:
#: 只能靠名字认 —— 真机 (imap.chinatelecom.cn, 9/18) 上 LIST 返回的 flags
#: 只有 (\\Marked), **没有 \\Sent / \\Trash / \\Drafts 这些 special-use
#: 标记**, 所以 RFC 6154 那条路走不通。
FOLDER_ALIASES = {
    "inbox": "Inbox", "收件箱": "Inbox",
    "sent": "Sent", "sent items": "Sent", "已发送": "Sent", "已发送邮件": "Sent",
    "drafts": "Drafts", "draft": "Drafts", "草稿": "Drafts", "草稿箱": "Drafts",
    "trash": "Trash", "deleted": "Trash", "已删除": "Trash",
    "junk": "Junk", "spam": "Junk", "垃圾箱": "Junk", "垃圾邮件": "Junk",
}


#
# IMAP 的文件夹名不是 UTF-8, 是一种改过的 UTF-7: `&` 起头、`-` 收尾, 中间是
# base64 但用 `,` 代替 `/`。实测 chinatelecom.cn:
#     &XfJT0ZAB-        → 已发送
#     &Xn9USmWHTvZZOQ-  → 广告文件夹
# Python 标准库没有这个 codec, 只能自己写。


_B64_ALPHABET = re.compile(r"^[A-Za-z0-9+/]+$")


def _decode_chunk(chunk: str) -> str:
    """一段 modified-base64 → 文本; 解不出返回空串 (调用方回退原文)。

    ⚠ 必须自己校验字母表: `binascii.a2b_base64` 默认**忽略**非法字符而不是报错,
    所以 "&@@@@-" 会被静默解成空串 —— 文件夹名直接消失, 比保留乱码还糟。
    (`strict_mode=True` 是 3.11+ 才有的, 不能依赖。)
    """
    b64 = chunk.replace(",", "/")
    if not _B64_ALPHABET.match(b64):
        return ""
    b64 += "=" * (-len(b64) % 4)
    try:
        data = binascii.a2b_base64(b64)
    except binascii.Error:
        return ""
    if not data or len(data) % 2:  # UTF-16-BE 必须是偶数字节
        return ""
    try:
        return data.decode("utf-16-be")
    except UnicodeDecodeError:
        return ""


def utf7_decode(raw: str) -> str:
    out: list[str] = []
    index = 0
    while index < len(raw):
        if raw[index] != "&":
            out.append(raw[index])
            index += 1
            continue
        end = raw.find("-", index)
        if end == -1:  # 没有收尾符 —— 坏名字, 原样保留
            out.append(raw[index:])
            break
        chunk = raw[index + 1 : end]
        if chunk == "":
            out.append("&")  # `&-` 是转义的字面 &
        else:
            out.append(_decode_chunk(chunk) or raw[index : end + 1])
        index = end + 1
    return "".join(out)


def utf7_encode(text: str) -> str:
    out: list[str] = []
    buffer: list[str] = []

    def flush() -> None:
        if not buffer:
            return
        data = "".join(buffer).encode("utf-16-be")
        b64 = binascii.b2a_base64(data, newline=False).decode("ascii").rstrip("=")
        out.append("&" + b64.replace("/", ",") + "-")
        buffer.clear()

    for char in text:
        if char == "&":
            flush()
            out.append("&-")
        elif 0x20 <= ord(char) <= 0x7E:
            flush()
            out.append(char)
        else:
            buffer.append(char)
    flush()
    return "".join(out)


def folder_role(decoded_name: str) -> str:
    """解码后的文件夹名 → 统一角色; 认不出的原样保留。"""
    return FOLDER_ALIASES.get(decoded_name.casefold(), decoded_name)


def folder_matches(actual: str, requested: str) -> bool:
    if requested == "*":
        return True
    return folder_role(actual).casefold() == folder_role(requested).casefold()

