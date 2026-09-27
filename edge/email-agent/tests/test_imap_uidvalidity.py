"""UIDVALIDITY 读对 + 旧 id 迁移 (9/27)。背景见 adapters/imap_uidvalidity.py 文件头。"""
from __future__ import annotations

import json

import pytest

from catfish_email import index_store
from catfish_email.adapters import imap_uidvalidity
from catfish_email.adapters.base import DataNotFoundError, ListFilter

from tests.test_adapter_imap import _raw
from tests.test_imap_sync import CountingIMAP, isolated_index, make  # noqa: F401  (fixture)


def _legacy_sync(adapter, monkeypatch):
    """用旧代码的行为 (版本号一律 "0") 同步一次, 造出升级前的索引。"""
    with monkeypatch.context() as m:
        m.setattr(imap_uidvalidity, "selected_uidvalidity", lambda conn, folder: "0")
        adapter.sync_folder("INBOX", "Inbox")


def _rows():
    db = index_store.open_index()
    try:
        return {r[0]: r[1:] for r in db.execute(
            "SELECT source_key, msg_id, is_read, archive_path, on_server FROM messages")}
    finally:
        db.close()


def test_select_reads_uidvalidity_the_way_real_imaplib_returns_it(isolated_index, monkeypatch):  # noqa: F811
    """真 imaplib: response("UIDVALIDITY") → ("UIDVALIDITY", [b"1"])。旧代码判 typ=="OK", 永远 "0"。"""
    adapter = make(CountingIMAP(), monkeypatch)
    msg = adapter.list_messages(ListFilter(folder="Inbox", limit=1))[0]
    assert msg.id.split("|")[2] == "1"


def test_status_is_the_fallback_when_select_does_not_report_it(isolated_index, monkeypatch):  # noqa: F811
    fake = CountingIMAP(select_reports_uidvalidity=False)
    adapter = make(fake, monkeypatch)
    assert adapter._select(adapter._connect(), "INBOX") == "1"
    assert fake.statuses == 1


def test_legacy_rows_are_verified_and_renamed_not_refetched(isolated_index, monkeypatch):  # noqa: F811
    fake = CountingIMAP()
    adapter = make(fake, monkeypatch)
    _legacy_sync(adapter, monkeypatch)
    db = index_store.open_index()
    db.execute("UPDATE messages SET archive_path='a/18.eml' WHERE source_key='imap:INBOX:0:8418'")
    db.commit()
    db.close()
    fake.header_fetch_uids.clear()

    stats = adapter.sync_folder("INBOX", "Inbox")

    rows = _rows()
    assert set(rows) == {"imap:INBOX:1:8418", "imap:INBOX:1:8417"}
    assert rows["imap:INBOX:1:8418"][0] == "imap|INBOX|1|8418"
    assert rows["imap:INBOX:1:8418"][2] == "a/18.eml", "档案指针跟着改名, 不能丢"
    assert stats.parsed == 0, "核实过的行只改名, 不重新取邮件头"
    mapping = json.loads((isolated_index / "email_id_migration.json").read_text(encoding="utf-8"))
    assert mapping == {"imap|INBOX|0|8418": "imap|INBOX|1|8418", "imap|INBOX|0|8417": "imap|INBOX|1|8417"}


def test_old_ids_still_work_through_the_mapping_and_unknown_ones_are_refused(isolated_index, monkeypatch):  # noqa: F811
    """对话历史里小鲶引用过的旧 id: 迁移过的照样能读; 没迁移过的 (核实没通过) 一律拒。"""
    fake = CountingIMAP()
    adapter = make(fake, monkeypatch)
    _legacy_sync(adapter, monkeypatch)
    adapter.sync_folder("INBOX", "Inbox")

    assert adapter.read_message("imap|INBOX|0|8418").subject
    with pytest.raises(DataNotFoundError, match="UIDVALIDITY"):
        adapter.read_message("imap|INBOX|0|9999")


def test_a_mismatch_means_the_mailbox_was_rebuilt_and_that_row_is_not_renamed(isolated_index, monkeypatch):  # noqa: F811
    """UID 8417 现在是另一封信 → 中间真的重建过。只改核实对上的, 对不上的作废重取。"""
    fake = CountingIMAP()
    adapter = make(fake, monkeypatch)
    _legacy_sync(adapter, monkeypatch)
    fake.messages["INBOX"] = [
        (uid, flags, raw if uid == b"8418" else _raw("重建后的另一封", day=21))
        for uid, flags, raw in fake.messages["INBOX"]
    ]

    adapter.sync_folder("INBOX", "Inbox")

    rows = _rows()
    assert "imap:INBOX:1:8418" in rows, "对上的照常改名"
    db = index_store.open_index()
    try:
        migrated = dict(db.execute("SELECT old_id, new_id FROM id_migration").fetchall())
        subjects = [r[0] for r in db.execute(
            "SELECT subject FROM messages WHERE source_key='imap:INBOX:1:8417'")]
    finally:
        db.close()
    assert "imap|INBOX|0|8417" not in migrated, "对不上的旧 id 不能指到新邮件上"
    assert subjects == ["重建后的另一封"], "新 UID 下的那封作为新行取回来"
    with pytest.raises(DataNotFoundError):
        adapter.read_message("imap|INBOX|0|8417")


def test_migration_runs_once(isolated_index, monkeypatch):  # noqa: F811
    fake = CountingIMAP()
    adapter = make(fake, monkeypatch)
    _legacy_sync(adapter, monkeypatch)
    adapter.sync_folder("INBOX", "Inbox")
    fake.header_fetch_uids.clear()
    stats = adapter.sync_folder("INBOX", "Inbox")
    assert fake.header_fetch_uids == [] and stats.parsed == 0


def test_old_id_from_a_folder_not_listed_yet_still_resolves(isolated_index, monkeypatch):  # noqa: F811
    """旧 id 所在文件夹这次还没被列过 (没迁移): 用到它时先迁移那个文件夹。"""
    fake = CountingIMAP()
    adapter = make(fake, monkeypatch)
    _legacy_sync(adapter, monkeypatch)  # 只造了收件箱的旧行, 不再 sync
    assert adapter.read_message("imap|INBOX|0|8417").subject
