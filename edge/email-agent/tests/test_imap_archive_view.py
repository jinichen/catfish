"""档案的读和进度 (9/27): 只在本地档案里的信能打开; 进度数字跟事实一致;
版本号迁移不丢档案起点; 列文件夹失败不当成文件夹空了。"""
from __future__ import annotations

import pytest

from catfish_email import archive_store, index_store
from catfish_email.adapters import imap_uidvalidity
from catfish_email.adapters.base import DataNotFoundError, EmailAdapterError

from tests.test_imap_sync import CountingIMAP, isolated_index, make  # noqa: F401  (fixture)


def _archive(key: str) -> str:
    """把索引里某一行"归档 + 核对"掉: 真写一份 .eml 到档案目录。"""
    db = index_store.open_index()
    try:
        return db.execute("SELECT msg_id FROM messages WHERE source_key=?", (key,)).fetchone()[0]
    finally:
        db.close()


def _store(adapter, fake, uid: bytes, key: str) -> None:
    raw = next(r for u, _, r in fake.messages["INBOX"] if u == uid)
    root = archive_store.archive_root()
    size, digest = archive_store.write_message(root, f"t/{uid.decode()}.eml", raw)
    db = index_store.open_index()
    index_store.mark_archived(db, key, path=f"t/{uid.decode()}.eml", size=size, sha256=digest)
    index_store.mark_verified(db, key)
    db.close()


def test_mail_only_in_the_local_archive_can_be_opened(isolated_index, monkeypatch):  # noqa: F811
    fake = CountingIMAP()
    adapter = make(fake, monkeypatch)
    adapter.sync_folder("INBOX", "Inbox")
    _store(adapter, fake, b"8417", "imap:INBOX:1:8417")
    msg_id = _archive("imap:INBOX:1:8417")
    fake.messages["INBOX"] = [m for m in fake.messages["INBOX"] if m[0] != b"8417"]  # 服务器清掉了
    adapter.sync_folder("INBOX", "Inbox")

    got = adapter.read_message(msg_id)
    assert got.subject, "只在本地档案里的信要能打开, 不能报「邮件不存在」"


def test_a_tampered_archive_file_is_not_shown(isolated_index, monkeypatch):  # noqa: F811
    fake = CountingIMAP()
    adapter = make(fake, monkeypatch)
    adapter.sync_folder("INBOX", "Inbox")
    _store(adapter, fake, b"8417", "imap:INBOX:1:8417")
    msg_id = _archive("imap:INBOX:1:8417")
    (archive_store.archive_root() / "t/8417.eml").write_bytes(b"Subject: tampered\\r\\n\\r\\nx")
    fake.messages["INBOX"] = [m for m in fake.messages["INBOX"] if m[0] != b"8417"]
    with pytest.raises(DataNotFoundError):
        adapter.read_message(msg_id)


def test_progress_counts_only_what_is_true(isolated_index, monkeypatch):  # noqa: F811
    """原来: 「还有 N 封排队」把水位线以下永远不会归档的也算进去; 「N 封只在本地档案」
    把从没归档过的也算进去。"""
    fake = CountingIMAP()
    adapter = make(fake, monkeypatch)
    adapter.sync_folder("INBOX", "Inbox")
    marks = {"INBOX": {"uidvalidity": "1", "uid": 8417}}   # 8417 在水位线上, 8418 之上
    db = index_store.open_index()
    try:
        stats = index_store.archive_stats(db, account="me@example.cn", marks=marks)
    finally:
        db.close()
    assert stats["pending"] == 1 and stats["before_cutoff"] == 1
    assert stats["only_local"] == 0


def test_uidvalidity_migration_keeps_the_archive_cutoff(isolated_index, monkeypatch):  # noqa: F811
    """起点是在版本号 0 下记的。不迁移的话 archive_folder 会重设起点, 旧起点到现在
    之间还没归档的信被永久跳过。"""
    fake = CountingIMAP()
    adapter = make(fake, monkeypatch)
    with monkeypatch.context() as m:
        m.setattr(imap_uidvalidity, "selected_uidvalidity", lambda conn, folder: "0")
        adapter.sync_folder("INBOX", "Inbox")
    root = archive_store.archive_root()
    archive_store.write_cutoff(root, "me@example.cn", {"INBOX": {"uidvalidity": "0", "uid": 8000}})

    adapter.sync_folder("INBOX", "Inbox")

    assert archive_store.read_cutoff(root, "me@example.cn")["INBOX"] == {"uidvalidity": "1", "uid": 8000}


def test_a_failed_folder_listing_is_not_an_empty_folder(isolated_index, monkeypatch):  # noqa: F811
    fake = CountingIMAP()
    adapter = make(fake, monkeypatch)
    adapter.sync_folder("INBOX", "Inbox")
    real = fake.uid
    fake.uid = lambda cmd, *a: ("NO", [b"busy"]) if cmd == "search" else real(cmd, *a)
    with pytest.raises(EmailAdapterError):
        adapter.sync_folder("INBOX", "Inbox")
    db = index_store.open_index()
    try:
        assert db.execute("SELECT count(*) FROM messages WHERE on_server=1").fetchone()[0] == 2
    finally:
        db.close()
