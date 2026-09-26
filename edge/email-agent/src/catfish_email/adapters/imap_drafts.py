"""IMAP 草稿: 回复线程头 + 改草稿 (9/26)。

从 imap_mail.py 拆出来 (那个文件 772 行, 再加就过 800 红线)。

# 两件事

1. **线程头写错了。** create_draft 原来把 --in-reply-to 原样写进 In-Reply-To /
   References —— 而前端传的是**鲶鱼内部 id** (`imap|INBOX|1|8418`), 不是 RFC 的
   Message-ID。对方邮件客户端靠 Message-ID 串线程, 看到的是一串它不认识的字符,
   这封回复在对方那里就成了一封孤立的新邮件。现在按内部 id 取回原邮件, 用它真正的
   Message-ID / References。

2. **改草稿。** IMAP 没有"修改一封邮件"这个操作, 只能 APPEND 新的、再把旧的去掉。
   新版本继承旧草稿的线程头 (旧草稿是回复, 改完还得是同一个线程的回复)。
   去掉旧版本不走 delete_message —— 那个会 COPY 一份到「已删除」, 改一次草稿就往
   已删除里塞一份旧稿不合理。这里只打 \\Deleted; 有 UIDPLUS 才 UID EXPUNGE 这一封,
   没有就留着标记 (理由同 delete_message: 裸 EXPUNGE 会连带清掉别处标记的邮件),
   我们自己的列表会过滤掉 \\Deleted。
"""
from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .imap_mail import ImapAdapter

logger = logging.getLogger("catfish_email.adapters.imap_drafts")


def threading_headers(adapter: "ImapAdapter", in_reply_to: str | None) -> tuple[str | None, str | None]:
    """--in-reply-to 的值 → (In-Reply-To, References)。

    · `imap|...`  鲶鱼内部 id: 取回原邮件, 用它的 Message-ID, References 接在原邮件的后面
    · `<...@...>` 已经是 RFC Message-ID: 原样用
    · 别的来源的内部 id: 这里解析不了, 不写线程头 (写错比不写更糟)
    """
    value = (in_reply_to or "").strip()
    if not value:
        return None, None
    if value.startswith("imap|"):
        original = adapter._fetch_raw(value)
        message_id = (original.get("Message-ID") or "").strip()
        if not message_id:
            return None, None
        parent_refs = (original.get("References") or original.get("In-Reply-To") or "").strip()
        return message_id, f"{parent_refs} {message_id}".strip()
    if value.startswith("<") and value.endswith(">"):
        return value, value
    logger.warning("回复的原邮件来自别的来源 (%s), 草稿不写线程头", value.split("|", 1)[0])
    return None, None


def inherited_headers(adapter: "ImapAdapter", draft_id: str) -> tuple[str | None, str | None]:
    """被替换的旧草稿的线程头, 改完原样沿用。"""
    old = adapter._fetch_raw(draft_id)
    return (old.get("In-Reply-To") or None), (old.get("References") or None)


def drop_old_draft(adapter: "ImapAdapter", draft_id: str) -> None:
    """新版本已经存好之后, 把旧版本从草稿箱里去掉。失败不回滚新草稿, 只记日志 ——
    多一份旧稿员工看得见、能手动删; 反过来丢了新稿才是真损失。"""
    try:
        folder_raw, uid = adapter._locate(draft_id, writable=True)
        conn = adapter._connect()
        typ, _ = conn.uid("store", uid, "+FLAGS", r"(\Deleted)")
        if typ != "OK":
            raise RuntimeError(typ)
        if adapter._has_capability("UIDPLUS"):
            conn.uid("expunge", uid)
    except Exception as error:  # noqa: BLE001
        logger.warning("新草稿已保存, 但旧草稿没去掉 (%s): %s", draft_id, error)


#: 找回刚存的草稿时往回翻多少封。刚 APPEND 的一定在最新的那几封里。
_RECENT_WINDOW = 30


def uid_by_recent_headers(conn, msg_id: str) -> str | None:
    """HEADER 搜索不管用时的兜底: 取文件夹里最新几封的 Message-ID 逐个比。

    9/26 Windows 实测: 存草稿「草稿存进去了, 但找不回它的 UID」—— 电信邮箱对
    `UID SEARCH HEADER Message-ID <...>` 返回空。服务器没有 UIDPLUS, 拿不到
    APPENDUID, 只剩这条路。调用前已经 SELECT 了目标文件夹。
    """
    typ, data = conn.uid("search", None, "ALL")
    if typ != "OK" or not data or not data[0]:
        return None
    recent = data[0].split()[-_RECENT_WINDOW:]
    if not recent:
        return None
    wanted = msg_id.strip().lower()
    typ, fetched = conn.uid(
        "fetch", b",".join(recent).decode("ascii"),
        "(UID BODY.PEEK[HEADER.FIELDS (MESSAGE-ID)])",
    )
    if typ != "OK" or not fetched:
        return None
    for item in fetched:
        if not isinstance(item, tuple) or len(item) < 2:
            continue
        meta, header = item[0], item[1]
        text = header.decode("utf-8", "replace") if isinstance(header, bytes) else str(header)
        if wanted not in text.lower():
            continue
        match = re.search(rb"UID (\d+)", meta if isinstance(meta, bytes) else str(meta).encode())
        if match:
            return match.group(1).decode("ascii")
    return None


def uids_in(conn, folder_raw: str, select) -> set[str]:
    """APPEND 之前草稿箱里已有的 UID。"""
    select(conn, folder_raw)
    typ, data = conn.uid("search", None, "ALL")
    if typ != "OK" or not data or not data[0]:
        return set()
    return {u.decode("ascii") for u in data[0].split()}


def new_uid_since(conn, folder_raw: str, select, before: set[str]) -> str | None:
    """最后的兜底: APPEND 前后对比, 多出来的那个 UID 就是刚存的草稿。

    9/26 真机第二次: HEADER 搜索和按 Message-ID 翻最新几封都没找到 —— 有的服务器
    在 APPEND 时会改写 Message-ID, 按内容认不出来。UID 是服务器分配的、只增不减,
    前后一比就知道。多出来不止一个 (别的客户端同时也在存草稿) 时取最大的,
    那是最后存进去的。
    """
    select(conn, folder_raw)
    typ, data = conn.uid("search", None, "ALL")
    if typ != "OK" or not data or not data[0]:
        return None
    added = [u.decode("ascii") for u in data[0].split() if u.decode("ascii") not in before]
    return max(added, key=int) if added else None
