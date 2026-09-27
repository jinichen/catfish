"""UIDVALIDITY: 读对它, 以及把读错那段时间发出去的旧 id 迁到正确的值 (9/27)。

# 发生了什么

9/27 在真机 (imap.chinatelecom.cn) 上抓了 EXAMINE 的原始回应:

    * OK [UIDVALIDITY 1] UIDs valid          ← 收件箱 1, 草稿箱 2, 已发送 3 ...

服务器一直在报, imaplib 也收下了。是我们读错了: `conn.response("UIDVALIDITY")`
返回的是 `("UIDVALIDITY", [b"1"])` —— 元组第一项是**响应码名字本身**, 不是
"OK"。原代码判 `typ == "OK"` 永远不成立, 于是一律返回 "0"。结果:

  · 这台服务器上发出去的所有 id、索引里所有 key, 版本号都是 0
  · 「邮箱重建后拒绝旧 id」这道保护从来没起过作用 —— 两边都是 0, 永远相等
  · 测试没发现: FakeIMAP 的 response() 返回的是 ("OK", ...), 跟真库不一样

(1.0.40 当时的判断"服务器不报 UIDVALIDITY"是错的, 那是被这个 bug 误导的。)

# 迁移: 为什么不能直接把 0 换成真值

版本号进 id, id 是好几处缓存的键 —— 邮件分诊/急缓评级 (Rust 侧 email_action /
email_urgency, 没命中就重新调模型评)、推送去重、前端已读记录、对话里小鲶提到过
的 id。直接换掉等于所有邮件在这些地方都"不认识了", 全部重评一遍。

所以按邮件逐封核实后再改名:

  1. 索引里版本号是 0 的行, 按 UID 向服务器批量取 Message-ID (只取这一个头)
  2. 跟索引里存的 Message-ID 对上 → 这个 UID 在当前版本下就是这封信 → 改名
  3. 对不上 → 说明中间真的重建过, 这行作废 (交给正常同步按"服务器上没了"处理,
     新 UID 下的那封会作为新行取回来)
  4. 服务器上已经没有的行 (档案) 没法逐封核实: 只有在本文件夹核实过的行
     **全部对上**时才一起改名 —— 那说明版本一直没变
  5. 改名记录写进索引的 id_migration 表, 并导出一份 JSON 给 Companion
     (Rust 侧缓存 / 前端已读记录照着改名); 旧 id 再被用到时 (比如对话历史里
     小鲶引用过的), 按这张表换成新 id, 表里没有的一律拒绝
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .imap_mail import ImapAdapter

logger = logging.getLogger("catfish_email.adapters.imap_uidvalidity")

#: 旧代码读不到 UIDVALIDITY 时填的占位
LEGACY = "0"
_VERIFY_BATCH = 200
_UIDVALIDITY_IN_STATUS = re.compile(rb"UIDVALIDITY (\d+)")
_UID_IN_FETCH = re.compile(rb"UID (\d+)")
_MESSAGE_ID = re.compile(rb"(?im)^message-id:\s*(<[^>\r\n]+>)")


def selected_uidvalidity(conn, folder_raw: str) -> str:
    """SELECT/EXAMINE 之后调, 返回这个文件夹的 UIDVALIDITY。

    imaplib 的 response(code) 返回 (code, [data]) —— 第一项是 code 本身。
    SELECT 没带的话 (RFC 要求带, 以防万一) 用 STATUS 单独问一次。
    """
    _, data = conn.response("UIDVALIDITY")
    value = data[0] if data else None
    if value:
        return value.decode("ascii", "replace").strip() if isinstance(value, bytes) else str(value).strip()
    typ, data = conn.status(f'"{folder_raw}"', "(UIDVALIDITY)")
    for item in (data or []) if typ == "OK" else []:
        match = _UIDVALIDITY_IN_STATUS.search(item if isinstance(item, bytes) else str(item).encode())
        if match:
            return match.group(1).decode("ascii")
    logger.warning("文件夹 %s 拿不到 UIDVALIDITY, 旧 id 保护对它无效", folder_raw)
    return LEGACY


# ── 迁移 ────────────────────────────────────────────────────────


def _message_ids_by_uid(conn, uids: list[str]) -> dict[str, str]:
    """按 UID 批量取 Message-ID (只取这一个头)。调用前已 SELECT 该文件夹。"""
    found: dict[str, str] = {}
    for i in range(0, len(uids), _VERIFY_BATCH):
        batch = ",".join(uids[i : i + _VERIFY_BATCH])
        typ, data = conn.uid("fetch", batch, "(UID BODY.PEEK[HEADER.FIELDS (MESSAGE-ID)])")
        if typ != "OK":
            continue
        for item in data or []:
            if not isinstance(item, tuple) or len(item) < 2:
                continue
            uid = _UID_IN_FETCH.search(item[0] if isinstance(item[0], bytes) else str(item[0]).encode())
            mid = _MESSAGE_ID.search(item[1] if isinstance(item[1], bytes) else str(item[1]).encode())
            if uid and mid:
                found[uid.group(1).decode()] = mid.group(1).decode("utf-8", "replace").strip()
    return found


def _norm(message_id: str | None) -> str:
    return (message_id or "").strip().lower()


def migrate_folder(adapter: "ImapAdapter", conn, db: sqlite3.Connection,
                   folder_raw: str, role: str, current: str) -> dict[str, str]:
    """把本文件夹版本号为 0 的索引行逐封核实后改名到 current。返回 {旧 id: 新 id}。"""
    from .imap_sync import _parse_sync_key, sync_key  # noqa: PLC0415

    if current == LEGACY:
        return {}
    assert adapter.config is not None
    prefix = sync_key(folder_raw, LEGACY, "")
    rows = db.execute(
        "SELECT source_key, message_id, on_server FROM messages "
        "WHERE account=? AND folder=? AND substr(source_key, 1, ?) = ?",
        (adapter.config.user, role, len(prefix), prefix),
    ).fetchall()
    if not rows:
        return {}

    on_server = [(k, m) for k, m, s in rows if s]
    remote = _message_ids_by_uid(conn, [_parse_sync_key(k)[2] for k, _ in on_server])
    matched, mismatched = [], []
    for key, stored in on_server:
        uid = _parse_sync_key(key)[2]
        if not _norm(stored) or uid not in remote:
            continue
        (matched if _norm(remote[uid]) == _norm(stored) else mismatched).append(key)
    unchanged = bool(matched) and not mismatched
    to_rename = matched + ([k for k, _, _ in rows if k not in matched] if unchanged else [])

    mapping: dict[str, str] = {}
    for old_key in to_rename:
        _, _, uid = _parse_sync_key(old_key)
        new_key = sync_key(folder_raw, current, uid)
        old_id = adapter._pack_id(folder_raw, LEGACY, uid)
        new_id = adapter._pack_id(folder_raw, current, uid)
        if db.execute("SELECT 1 FROM messages WHERE source_key=?", (new_key,)).fetchone():
            # 新 key 已经有一行 (这次改名之前就同步过): 档案指针挪过去, 旧行删掉
            db.execute(
                """UPDATE messages SET
                       archive_path=COALESCE(archive_path, (SELECT archive_path FROM messages WHERE source_key=?)),
                       archive_bytes=COALESCE(archive_bytes, (SELECT archive_bytes FROM messages WHERE source_key=?)),
                       archive_sha256=COALESCE(archive_sha256, (SELECT archive_sha256 FROM messages WHERE source_key=?)),
                       archived_at=COALESCE(archived_at, (SELECT archived_at FROM messages WHERE source_key=?)),
                       verified_at=COALESCE(verified_at, (SELECT verified_at FROM messages WHERE source_key=?))
                   WHERE source_key=?""",
                (old_key, old_key, old_key, old_key, old_key, new_key),
            )
            db.execute("DELETE FROM messages WHERE source_key=?", (old_key,))
        else:
            db.execute("UPDATE messages SET source_key=?, msg_id=? WHERE source_key=?",
                       (new_key, new_id, old_key))
        db.execute("INSERT OR REPLACE INTO id_migration (old_id, new_id) VALUES (?, ?)", (old_id, new_id))
        mapping[old_id] = new_id
    db.commit()
    logger.info(
        "imap: %s 旧 id 迁移 → UIDVALIDITY %s: 核实对上 %d, 对不上 %d, 改名 %d (共 %d 行)",
        role, current, len(matched), len(mismatched), len(mapping), len(rows),
    )
    if mapping:
        export_mapping(db)
    return mapping


def export_mapping(db: sqlite3.Connection) -> None:
    """整张改名表导出成 JSON, 给 Companion (Rust 缓存 / 前端已读记录) 照着改名。"""
    from .. import index_store  # noqa: PLC0415

    mapping = dict(db.execute("SELECT old_id, new_id FROM id_migration").fetchall())
    path = index_store.index_db_path().parent / "email_id_migration.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(mapping, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def resolve_legacy(message_id: str) -> str | None:
    """版本号是 0 的旧 id → 迁移后的新 id; 没迁移过 (核实没通过) 返回 None。"""
    from .. import index_store  # noqa: PLC0415

    db = index_store.open_index()
    try:
        row = db.execute("SELECT new_id FROM id_migration WHERE old_id=?", (message_id,)).fetchone()
    finally:
        db.close()
    return row[0] if row else None
