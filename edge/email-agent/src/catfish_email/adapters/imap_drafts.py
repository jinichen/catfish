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
