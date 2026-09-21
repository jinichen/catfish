"""保留期到了, 从服务器删掉。

# 这是这套东西里唯一不可逆的一步

判错一次, 员工的邮件是真的没了。所以这些测试盯的不是"功能对不对",
是"什么情况下**绝对不能删**":

  · 没独立回读核对过的 —— 绝不删
  · 保留期没到的 —— 绝不删
  · 策略是 never / 认不出来的 —— 绝不删
  · 没有 UIDPLUS 时 —— 绝不裸 EXPUNGE, 而且要说出来容量没释放
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from catfish_email import index_store
from catfish_email.adapters.imap_mail import ImapConfig
from catfish_email.adapters.imap_sync import ImapSyncAdapter
from tests.test_adapter_imap import FakeIMAP, _raw

WEEK = 7 * 24 * 3600


class PurgingIMAP(FakeIMAP):
    """记下 STORE 和 EXPUNGE —— 判据全在"发了什么命令"上。"""

    def __init__(self, capabilities=(), **kw):
        super().__init__(**kw)
        self.capabilities = tuple(capabilities)
        self.stores: list[tuple[str, str]] = []
        self.expunges: list[str] = []
        self.copies: list[str] = []

    def uid(self, command, *args):
        if command == "store":
            self.stores.append((args[0].decode() if isinstance(args[0], bytes) else str(args[0]), str(args[2])))
            return "OK", [b""]
        if command == "expunge":
            self.expunges.append(args[0].decode() if isinstance(args[0], bytes) else str(args[0]))
            return "OK", [b""]
        if command == "copy":
            self.copies.append(str(args[1]))
            return "OK", [b""]
        return super().uid(command, *args)


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("CATFISH_HOME", str(tmp_path))
    return tmp_path


def make(fake, monkeypatch, retention="never") -> ImapSyncAdapter:
    monkeypatch.setattr(
        "catfish_email.adapters.imap_mail.imaplib.IMAP4_SSL",
        lambda host, port, timeout=None: fake,
    )
    return ImapSyncAdapter(
        ImapConfig(host="h", user="me@example.cn", password="s", retention=retention)
    )


def seed(adapter, *, verified_ago: float | None, on_server: int = 1) -> str:
    """往索引里塞一封, 指定它多久之前校验通过的。"""
    adapter.sync_folder("INBOX", "Inbox")
    db = index_store.open_index()
    try:
        key = db.execute(
            "SELECT source_key FROM messages ORDER BY source_key LIMIT 1"
        ).fetchone()[0]
        db.execute(
            "UPDATE messages SET archive_path='x.eml', archived_at=1, "
            "verified_at=?, on_server=? WHERE source_key=?",
            (None if verified_ago is None else time.time() - verified_ago, on_server, key),
        )
        db.execute("DELETE FROM messages WHERE source_key != ?", (key,))
        db.commit()
    finally:
        db.close()
    return key


# ─────────────────────────────────────────────────────────────
# 绝对不能删的情况
# ─────────────────────────────────────────────────────────────


def test_never_deletes_anything(home, monkeypatch):
    """默认策略是 never, 而且默认值是刻意的。

    删服务器上的邮件不可逆。不能因为员工没注意到设置项就悄悄开始删 ——
    要删必须是他明确选过。
    """
    fake = PurgingIMAP(capabilities=("UIDPLUS",))
    adapter = make(fake, monkeypatch, retention="never")
    seed(adapter, verified_ago=WEEK * 10)
    got = adapter.purge_folder("INBOX", "Inbox")
    assert got == {"purged": 0, "flagged": 0, "needs_expunge": 0}
    assert fake.stores == [] and fake.expunges == []


def test_unverified_mail_is_never_purged(home, monkeypatch):
    """**最要紧的一条。** 没独立回读核对过的绝不删。

    archived_at 有值只说明 write() 没抛异常 —— 盘上那份可能是截断的、
    坏块的、被别的进程改过的。拿它当依据, 本地那份是坏的而服务器那份
    已经删了, 信就是没了, 而且没有任何地方报过错。
    """
    fake = PurgingIMAP(capabilities=("UIDPLUS",))
    adapter = make(fake, monkeypatch, retention="immediate")
    seed(adapter, verified_ago=None)          # archived 了但没 verified
    got = adapter.purge_folder("INBOX", "Inbox")
    assert got["purged"] == 0 and got["flagged"] == 0
    assert fake.stores == [], "没校验过的被删了"


def test_retention_window_is_respected(home, monkeypatch):
    fake = PurgingIMAP(capabilities=("UIDPLUS",))
    adapter = make(fake, monkeypatch, retention="2w")
    seed(adapter, verified_ago=WEEK)          # 才一周, 策略是两周
    assert adapter.purge_folder("INBOX", "Inbox")["purged"] == 0
    assert fake.stores == []


def test_expired_mail_is_purged(home, monkeypatch):
    fake = PurgingIMAP(capabilities=("UIDPLUS",))
    adapter = make(fake, monkeypatch, retention="1w")
    seed(adapter, verified_ago=WEEK + 3600)
    got = adapter.purge_folder("INBOX", "Inbox")
    assert got["purged"] == 1
    assert fake.expunges, "有 UIDPLUS 却没精准清"


@pytest.mark.parametrize("bad", ["3w", "", "immediate ", "IMMEDIATE", "0"])
def test_an_unrecognised_policy_never_deletes(home, monkeypatch, bad):
    """认不出来的策略退回 never。**方向不能反** —— 打错一个字就开始删
    邮件是不可接受的。"""
    cfg = ImapConfig(host="h", user="u", password="p", retention=bad)
    assert cfg.retention_seconds() is None, f"{bad!r} 被当成了会删的策略"


# ─────────────────────────────────────────────────────────────
# 没有 UIDPLUS —— 真机就是这种
# ─────────────────────────────────────────────────────────────


def test_without_uidplus_it_flags_but_never_bare_expunges(home, monkeypatch):
    """裸 EXPUNGE 会清掉整个文件夹里所有打了 \\Deleted 的邮件 ——
    包括员工在 Foxmail/Outlook 里标了删除还没压缩的那些, 不可恢复。

    宁可不释放容量也不能干这个。
    """
    fake = PurgingIMAP(capabilities=("IMAP4rev1",))     # 没有 UIDPLUS
    adapter = make(fake, monkeypatch, retention="immediate")
    seed(adapter, verified_ago=10)
    got = adapter.purge_folder("INBOX", "Inbox")

    assert got["flagged"] == 1 and got["purged"] == 0
    assert fake.stores, "连标记都没打"
    assert fake.expunges == [], "裸 EXPUNGE 了 —— 会连带清掉别处标记的邮件"


def test_it_says_out_loud_that_capacity_was_not_reclaimed(home, monkeypatch, caplog):
    """降级必须说话。

    员工开这个功能就是为了腾空间。结果空间没腾出来而界面一切正常, 是最坏
    的一种 —— 他会以为已经腾了, 继续等容量下降。
    """
    fake = PurgingIMAP(capabilities=("IMAP4rev1",))
    adapter = make(fake, monkeypatch, retention="immediate")
    seed(adapter, verified_ago=10)
    with caplog.at_level("WARNING"):
        got = adapter.purge_folder("INBOX", "Inbox")

    assert got["needs_expunge"] == 1, "界面拿不到「容量没释放」这个事实"
    assert any("容量还没释放" in r.message for r in caplog.records), \
        "只在返回值里说了, 日志一声不吭"


# ─────────────────────────────────────────────────────────────
# 跟手动删的区别
# ─────────────────────────────────────────────────────────────


def test_expiry_purge_does_not_copy_to_trash(home, monkeypatch):
    """到期清理**不** COPY 到「已删除」。

    副本同样占容量, 而腾容量正是这个功能存在的理由 —— 留一份等于删完更胖。

    (员工手动删是另一条路, 那边要 COPY: 他可能后悔, 要留退路。这里的退路
    是本地档案, 而且已经独立回读核对过了。)
    """
    fake = PurgingIMAP(capabilities=("UIDPLUS",))
    adapter = make(fake, monkeypatch, retention="immediate")
    seed(adapter, verified_ago=10)
    adapter.purge_folder("INBOX", "Inbox")
    assert fake.copies == [], "到期清理还往「已删除」复制了一份, 容量白腾"


def test_purged_mail_is_marked_off_server_not_deleted_locally(home, monkeypatch):
    """服务器上没了, 本地档案照旧 —— 这就是档案馆的全部意义。"""
    fake = PurgingIMAP(capabilities=("UIDPLUS",))
    adapter = make(fake, monkeypatch, retention="immediate")
    key = seed(adapter, verified_ago=10)
    adapter.purge_folder("INBOX", "Inbox")

    db = index_store.open_index()
    try:
        row = db.execute(
            "SELECT on_server, archive_path, verified_at FROM messages WHERE source_key=?",
            (key,),
        ).fetchone()
    finally:
        db.close()
    assert row is not None, "索引行被删了 —— 档案指针没了"
    assert row[0] == 0, "没标成服务器上已无"
    assert row[1] == "x.eml" and row[2] is not None, "档案登记被清掉了"
