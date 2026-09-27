"""从本地档案读邮件 (9/27)。

档案馆的全部意义是"服务器清理了, 本地还在" —— 但原来只存不读: 列表里显示
「只在本地档案里」的那些, 一点开照样去问服务器, 报「邮件不存在」。

这里只在服务器那头读不到时才用 (ImapSyncAdapter.read_message / _fetch_raw 的兜底),
而且只认**独立回读核对过** (verified_at) 的档案, 读的时候再比一次 SHA-256 ——
盘上那份被改过 / 坏了, 宁可报读不到也不给员工看错的内容。
"""
from __future__ import annotations

import email
import email.policy
import hashlib
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .imap_mail import ImapAdapter, _Remote

logger = logging.getLogger("catfish_email.adapters.imap_archive_read")


def from_archive(adapter: "ImapAdapter", message_id: str) -> "_Remote | None":
    from .. import archive_store, index_store  # noqa: PLC0415
    from .imap_mail import _Remote  # noqa: PLC0415

    db = index_store.open_index()
    try:
        row = db.execute(
            "SELECT archive_path, archive_sha256, folder FROM messages "
            "WHERE msg_id=? AND archive_path IS NOT NULL AND verified_at IS NOT NULL",
            (message_id,),
        ).fetchone()
    finally:
        db.close()
    if row is None:
        return None
    rel, sha256, role = row
    path = archive_store.archive_root() / rel
    try:
        raw = path.read_bytes()
    except OSError as error:
        logger.warning("档案文件读不到 %s: %s", path, error)
        return None
    if sha256 and hashlib.sha256(raw).hexdigest() != sha256:
        logger.error("档案文件跟归档时的校验值对不上, 不读: %s", path)
        return None
    folder_raw, uidvalidity, uid = adapter._unpack_id(message_id)
    return _Remote(
        folder_raw=folder_raw, folder_role=role, uidvalidity=uidvalidity, uid=uid,
        flags="\\Seen", message=email.message_from_bytes(raw, policy=email.policy.default),
    )
