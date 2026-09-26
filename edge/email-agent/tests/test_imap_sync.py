"""IMAP 增量同步 —— 判据是"第二次取了几封", 不是"列表看着对"。

# 为什么这样测

服务器**没有** CONDSTORE / QRESYNC (9/18 真机 CAPABILITY 实测), 拿不到
"自从上次以来变了什么"。但不需要, IMAP 的两个性质就够:

    · UID 在一个 UIDVALIDITY 周期内永不改变、永不复用
    · `UID FETCH <range> (FLAGS)` 不带正文, 很便宜

于是: 廉价地拉一遍全部 (uid, flags) → 跟索引对账 → 只对新增的和 flags 变了的
去取邮件头。

**增量对不对, 只有"取了几封"能证明。** 列表看着对说明不了问题 —— 每次全量
重取也能得到对的列表, 只是慢十倍。所以这个文件里每条都盯 fetch 次数。

这跟 test_index_store.py 里那九条是同一个思路 (那边盯 parse 次数), 因为增量
这件事的失败方式就是"悄悄退化成全量", 不报错、结果也对。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from catfish_email import index_store
from catfish_email.adapters import imap_sync
from catfish_email.adapters.base import ListFilter
from catfish_email.adapters.imap_mail import ImapConfig
from catfish_email.adapters.imap_sync import (
    ImapSyncAdapter,
    _parse_sync_key,
    sync_key,
)

# 复用真机形态的假服务器 —— 增量这层不该自造一套夹具, 那样就跟真机脱钩了。
# tests/ 是个包 (有 __init__.py), 所以走包路径导入。
from tests.test_adapter_imap import FakeIMAP, _raw


class CountingIMAP(FakeIMAP):
    """数 FETCH 次数 —— 分开数"只要 flags 的"和"要邮件头的"。

    只要 flags 的那次是廉价的 (一个往返, 不带正文), 每轮都发是对的。
    要邮件头的那些才是成本所在, 增量的目标就是把它压到只剩变化的部分。
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.flag_fetches = 0
        self.header_fetches = 0            # **往返数** —— 广域网上这个才是成本
        self.header_fetch_uids: list[bytes] = []

    def uid(self, command, *args):
        if command == "fetch" and args[1] == "(UID FLAGS)":
            self.flag_fetches += 1
            box = self.messages.get(self.selected or "", [])
            wanted = set(self._as_bytes(args[0]).split(b","))
            return "OK", [
                b"1 (UID " + uid + b" FLAGS (" + flags + b"))"
                for uid, flags, _ in box
                if uid in wanted
            ]
        if command == "fetch":
            self.header_fetches += 1
            self.header_fetch_uids.extend(self._as_bytes(args[0]).split(b","))
        return super().uid(command, *args)


@pytest.fixture
def isolated_index(tmp_path: Path, monkeypatch):
    """索引落到 tmp —— 绝不碰开发机上真的 ~/.catfish/email_index.db。"""
    monkeypatch.setenv("CATFISH_HOME", str(tmp_path))
    yield tmp_path


def make(fake: CountingIMAP, monkeypatch) -> ImapSyncAdapter:
    monkeypatch.setattr(
        "catfish_email.adapters.imap_mail.imaplib.IMAP4_SSL",
        lambda host, port, timeout=None: fake,
    )
    return ImapSyncAdapter(
        ImapConfig(host="imap.example.cn", user="me@example.cn", password="s3cret")
    )


# ─────────────────────────────────────────────────────────────
# source_key
# ─────────────────────────────────────────────────────────────


def test_sync_key_carries_uidvalidity():
    """不带 UIDVALIDITY 的话, 服务器重建邮箱后新旧两封会撞在同一个 key 上,
    而且内容完全不相干。"""
    assert sync_key("INBOX", "1", "8418") == "imap:INBOX:1:8418"
    assert sync_key("INBOX", "2", "8418") != sync_key("INBOX", "1", "8418")


@pytest.mark.parametrize(
    "folder", ["INBOX", "&XfJT0ZAB-", "a/b", "weird:name:with:colons"]
)
def test_sync_key_roundtrips_even_with_colons_in_folder(folder):
    """IMAP 没规定分隔符不能是冒号, 所以从右边切两刀而不是 split(':')。"""
    key = sync_key(folder, "7", "42")
    assert _parse_sync_key(key) == (folder, "7", "42")


# ─────────────────────────────────────────────────────────────
# 增量 —— 全都盯 fetch 次数
# ─────────────────────────────────────────────────────────────


def test_first_sync_fetches_everything(isolated_index, monkeypatch):
    fake = CountingIMAP()
    adapter = make(fake, monkeypatch)
    stats = adapter.sync_folder("INBOX", "Inbox")
    assert stats.scanned == 2
    assert stats.parsed == 2
    assert len(fake.header_fetch_uids) == 2


def test_a_batch_is_one_round_trip_not_one_per_message(isolated_index, monkeypatch):
    """首次同步必须**成批**取, 不能一封一个往返。

    一封一个往返在 57 封的测试邮箱上看不出问题 —— 真机上也就多等两秒。
    到了五年的邮箱就是两千个往返, 跨广域网按 100ms 算三分多钟, 用户以为卡死。
    这条盯的是往返数, 不是取到的封数, 因为两者只在这个 bug 下才会不一样。
    """
    monkeypatch.setattr(imap_sync, "SYNC_FETCH_BATCH", 100)
    many = [
        (str(9000 + i).encode(), b"", _raw(f"第 {i} 封", day=1 + i % 28))
        for i in range(500)
    ]
    fake = CountingIMAP(messages={"INBOX": many})
    adapter = make(fake, monkeypatch)
    stats = adapter.sync_folder("INBOX", "Inbox")

    assert stats.parsed == 500
    # 5 是写死的, **不是**从 SYNC_FETCH_BATCH 算出来的 —— 算出来的话批量大小
    # 一改断言跟着变, 这条就什么都不守了 (第一版就是这么写错的)。
    assert fake.header_fetches == 5, (
        f"500 封 / 每批 100 = 5 个往返, 实际 {fake.header_fetches}"
    )


def test_the_shipped_batch_size_is_actually_a_batch():
    """上一条把批量大小调小了才好数往返, 这条守的是**出厂值**没被改成 1。"""
    assert imap_sync.SYNC_FETCH_BATCH >= 50


def test_index_is_capped_so_a_five_year_mailbox_syncs_in_bounded_time(
    isolated_index, monkeypatch
):
    """只索引最新的 SYNC_INDEX_CAP 封。索引是给"看收件箱"用的, 不是归档。"""
    monkeypatch.setattr(imap_sync, "SYNC_INDEX_CAP", 10)
    many = [
        (str(9000 + i).encode(), b"", _raw(f"第 {i} 封", day=1 + i % 28))
        for i in range(50)
    ]
    fake = CountingIMAP(messages={"INBOX": many})
    adapter = make(fake, monkeypatch)
    stats = adapter.sync_folder("INBOX", "Inbox")

    assert stats.scanned == 10, "超出上限的不该进对账"
    # UID 递增 → 留下的必须是**最新**那批, 不是最老那批
    rows = adapter.list_messages(ListFilter(folder="Inbox", limit=100))
    assert {m.id.split("|")[-1] for m in rows} == {str(9040 + i) for i in range(10)}


def test_second_sync_fetches_nothing(isolated_index, monkeypatch):
    """这条是整个增量的命门。第二次还去取, 说明退化成全量了。"""
    fake = CountingIMAP()
    adapter = make(fake, monkeypatch)
    adapter.sync_folder("INBOX", "Inbox")
    fake.header_fetch_uids.clear()

    stats = adapter.sync_folder("INBOX", "Inbox")
    assert stats.scanned == 2
    assert stats.unchanged == 2
    assert stats.parsed == 0
    assert fake.header_fetch_uids == [], "没变的邮件不该再取一次"
    assert fake.flag_fetches == 2, "FLAGS 每轮都该问 —— 它便宜, 而且是变更的唯一来源"


def test_only_the_new_message_is_fetched(isolated_index, monkeypatch):
    fake = CountingIMAP()
    adapter = make(fake, monkeypatch)
    adapter.sync_folder("INBOX", "Inbox")
    fake.header_fetch_uids.clear()

    fake.messages["INBOX"].append((b"8419", b"", _raw("新来的", day=19)))
    stats = adapter.sync_folder("INBOX", "Inbox")
    assert stats.parsed == 1
    assert fake.header_fetch_uids == [b"8419"]


def test_flag_change_refetches_only_that_one(isolated_index, monkeypatch):
    """已读状态变了要反映出来 —— fingerprint 用 flags 就是为了这个。"""
    fake = CountingIMAP()
    adapter = make(fake, monkeypatch)
    adapter.sync_folder("INBOX", "Inbox")
    fake.header_fetch_uids.clear()

    # 8417 从未读变已读
    fake.messages["INBOX"] = [
        (uid, rb"\Seen" if uid == b"8417" else flags, raw)
        for uid, flags, raw in fake.messages["INBOX"]
    ]
    stats = adapter.sync_folder("INBOX", "Inbox")
    assert stats.parsed == 1
    assert fake.header_fetch_uids == [b"8417"]

    rows = adapter.list_messages(ListFilter(folder="Inbox", limit=10))
    by_uid = {m.id.split("|")[-1]: m for m in rows}
    assert by_uid["8417"].is_read is True


def test_message_gone_from_server_is_kept_as_archive(isolated_index, monkeypatch):
    """服务器上没了 ≠ 索引里删掉。

    9/20 这条**反过来了**。原来叫 ..._is_dropped_from_the_index, 断言的是
    "服务器删了本地跟着删" —— 那是镜子的语义。

    定位改成档案馆之后那是错的: 公司邮箱有容量上限、会自动清理, 而越老的信
    越可能已经被清掉, 偏偏越老的信越是要沉淀的那些。跟着删 = 这套东西白做。

    现在的语义: 行留着, 只把 on_server 置 0。
    """
    from catfish_email import index_store

    fake = CountingIMAP()
    adapter = make(fake, monkeypatch)
    adapter.sync_folder("INBOX", "Inbox")

    fake.messages["INBOX"] = [m for m in fake.messages["INBOX"] if m[0] != b"8417"]
    stats = adapter.sync_folder("INBOX", "Inbox")
    assert stats.removed == 1, "统计上仍然记'来源里没了 1 封'"

    db = index_store.open_index()
    try:
        rows = list(db.execute(
            "SELECT source_key, on_server FROM messages ORDER BY source_key"
        ))
    finally:
        db.close()
    assert len(rows) == 2, f"档案馆里两行都该在, 实际 {rows}"
    by_uid = {r[0].rsplit(":", 1)[1]: r[1] for r in rows}
    assert by_uid["8417"] == 0, "服务器上没了的要标 on_server=0"
    assert by_uid["8418"] == 1, "还在服务器上的不该被动"


def test_uidvalidity_reset_rebuilds_instead_of_mixing(isolated_index, monkeypatch):
    """服务器重建邮箱 → UID 从头发放。旧行必须整批换掉, 绝不能新旧混在一起。

    9/20 改档案馆之后这条差点被破坏, 而且是最坏的那种"差点":

    on_missing="keep" 意味着旧 UIDVALIDITY 那批**不再被删**, 只标 on_server=0。
    新周期的信拿到全新 key 插进来, 于是同一封信在列表里出现两次。

    而这绝不是边角情况 —— "邮箱容量满了 → 清理/重建" 正是最常触发
    UIDVALIDITY 变化的场景, 也正是这套档案馆存在的理由。不处理的话,
    档案馆第一次真正派上用场那天就开始出重复。

    解法是按 RFC822 Message-ID 去重: 它是这封信跨 UIDVALIDITY 唯一稳定的身份。
    """
    fake = CountingIMAP()
    adapter = make(fake, monkeypatch)
    adapter.sync_folder("INBOX", "Inbox")

    fake.uidvalidity = b"2"
    stats = adapter.sync_folder("INBOX", "Inbox")
    assert stats.parsed == 2, "新周期的 UID 是全新的 key, 全部要重取"
    assert stats.removed == 2, "旧周期那两行在服务器上确实没了"

    rows = adapter.list_messages(ListFilter(folder="Inbox", limit=10))
    assert len(rows) == 2, f"邮箱重建后列表里出现重复: {[m.id for m in rows]}"
    assert all(m.id.split("|")[2] == "2" for m in rows), "不该留着旧 UIDVALIDITY 的行"


def test_uidvalidity_reset_does_not_throw_away_an_archived_copy(
    isolated_index, monkeypatch
):
    """去重只许删**还没归档**的旧行。

    旧行一旦落了 .eml, 它就不只是一条索引记录, 而是档案本体的指针。
    连同 archive_path 一起删掉 = 磁盘上那个 .eml 成了没人认识的孤儿,
    而它可能是服务器上早已不存在的那封信的唯一副本。

    正确做法是把 archive 指针挪到新行上 —— 归档功能上线时要做的事。
    在那之前, 这条测试保证去重不会先把东西删了。
    """
    from catfish_email import index_store

    fake = CountingIMAP()
    adapter = make(fake, monkeypatch)
    adapter.sync_folder("INBOX", "Inbox")

    # 假装其中一封已经归档落地
    db = index_store.open_index()
    try:
        db.execute(
            "UPDATE messages SET archive_path=?, archived_at=1.0 "
            "WHERE source_key LIKE '%:8417'",
            ("acct/2026-09/x.eml",),
        )
        db.commit()
    finally:
        db.close()

    fake.uidvalidity = b"2"
    adapter.sync_folder("INBOX", "Inbox")

    db = index_store.open_index()
    try:
        kept = list(db.execute(
            "SELECT source_key FROM messages WHERE archive_path IS NOT NULL"
        ))
    finally:
        db.close()
    assert len(kept) == 1, "已归档的旧行被去重顺手删了 —— 那是档案本体的指针"


# ─────────────────────────────────────────────────────────────
# 容错
# ─────────────────────────────────────────────────────────────


def test_sync_failure_falls_back_to_the_index_not_an_empty_inbox(
    isolated_index, monkeypatch, caplog
):
    """对账挂了要给旧数据, 不能给空收件箱。

    今天在 Foxmail 上看够了空收件箱有多难排查 —— 它跟"真的没邮件"长得一模一样。
    """
    fake = CountingIMAP()
    adapter = make(fake, monkeypatch)
    adapter.sync_folder("INBOX", "Inbox")

    def boom(*a, **k):
        raise OSError("网络断了")

    monkeypatch.setattr(adapter, "sync_folder", boom)
    with caplog.at_level("WARNING"):
        rows = adapter.list_messages(ListFilter(folder="Inbox", limit=10))
    assert len(rows) == 2, "索引里还有上一轮的数据, 该给出来"
    assert "旧数据" in caplog.text


def test_one_bad_folder_does_not_stop_the_others(isolated_index, monkeypatch):
    fake = CountingIMAP()
    adapter = make(fake, monkeypatch)
    real = adapter.sync_folder

    def flaky(folder_raw, role):
        if role == "Drafts":
            raise OSError("这个文件夹打不开")
        return real(folder_raw, role)

    monkeypatch.setattr(adapter, "sync_folder", flaky)
    out = adapter.sync_all()
    assert "Inbox" in out and "Sent" in out
    assert "Drafts" not in out


def test_check_new_mail_syncs_instead_of_telling_the_user_to_refresh_a_client(
    isolated_index, monkeypatch
):
    """界面上的「收信」在 IMAP 下不能报错。

    基类 EmailAdapter 的默认实现抛 NotSupportedError, 文案是"员工需要手动在
    客户端里 refresh" —— IMAP 这条路存在的前提就是没有能用的客户端, 照抄那个
    文案等于叫员工去刷一个不存在的东西。
    """
    fake = CountingIMAP()
    adapter = make(fake, monkeypatch)
    adapter.check_new_mail()  # 不该抛
    rows = adapter.list_messages(ListFilter(folder="Inbox", limit=10))
    assert len(rows) == 2, "「收信」该把索引填上"


def test_check_new_mail_covers_every_folder_not_just_the_open_one(
    isolated_index, monkeypatch
):
    fake = CountingIMAP()
    adapter = make(fake, monkeypatch)
    adapter.check_new_mail()
    rows = adapter.list_messages(ListFilter(folder="Sent", limit=10))
    assert [m.id.split("|")[-1] for m in rows] == ["77"]


def test_index_is_scoped_to_the_account(isolated_index, monkeypatch):
    """换个账号不该看到上一个账号的邮件 —— 索引按 account 分区。"""
    fake = CountingIMAP()
    make(fake, monkeypatch).sync_folder("INBOX", "Inbox")

    other = ImapSyncAdapter(
        ImapConfig(host="imap.example.cn", user="someone-else@example.cn", password="p")
    )
    conn = index_store.open_index()
    try:
        rows = index_store.query_messages(
            conn, account=other.config.user, folder="Inbox", limit=10
        )
    finally:
        conn.close()
    assert rows == []
