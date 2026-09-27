"""保留期到了, 从服务器删掉。

# 这是这套东西里唯一不可逆的一步

判错一次, 员工的邮件是真的没了。所以这些测试盯的不是"功能对不对",
是"什么情况下**绝对不能删**":

  · 没独立回读核对过的 —— 绝不删
  · 保留期没到的 —— 绝不删
  · 策略是 never / 认不出来的 —— 绝不删
  · 别的客户端标记待删的 —— 绝不连带清掉 (没有 UIDPLUS 时也一样)

9/27: 夹具改成**有状态**的 —— STORE 真改标记, EXPUNGE 真删信。原来的夹具
只记命令, "只清我们这几封、别人的待删标记原样挂回去"这件事它表达不了。
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
    """像真服务器一样改状态: STORE 改标记 (基类), EXPUNGE 真删, SEARCH 认
    DELETED / UID 条件。命令照样记在 stores / expunges / copies 里。"""

    def __init__(self, capabilities=(), expunge_ok=True, **kw):
        super().__init__(**kw)
        self.capabilities = tuple(capabilities)
        self.expunge_ok = expunge_ok
        self.bare_expunges = 0

    def _box(self):
        return self.messages.setdefault(self.selected or "", [])

    def uid(self, command, *args):
        if command == "search" and len(args) >= 2 and str(args[1]).upper() == "DELETED":
            return "OK", [b" ".join(u for u, f, _ in self._box() if rb"\Deleted" in f)]
        if command == "search" and len(args) >= 3 and str(args[1]).upper() == "UID":
            wanted = set(self._as_bytes(args[2]).split(b","))
            return "OK", [b" ".join(u for u, _, _ in self._box() if u in wanted)]
        if command == "expunge":            # UID EXPUNGE: 只清给定的且带 \Deleted 的
            wanted = set(self._as_bytes(args[0]).split(b","))
            self.expunges.append(self._as_bytes(args[0]).decode())
            self.messages[self.selected] = [
                m for m in self._box() if not (m[0] in wanted and rb"\Deleted" in m[1])
            ]
            return "OK", [b"UID EXPUNGE completed"]
        return super().uid(command, *args)

    def expunge(self):                       # 裸 EXPUNGE: 清掉所有带 \Deleted 的
        self.bare_expunges += 1
        if not self.expunge_ok:
            return "NO", [b"EXPUNGE failed"]
        self.messages[self.selected] = [m for m in self._box() if rb"\Deleted" not in m[1]]
        return "OK", [b"EXPUNGE completed"]

    def flags_of(self, uid: bytes) -> bytes | None:
        return next((f for u, f, _ in self.messages.get("INBOX", []) if u == uid), None)


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
    assert got == {"purged": 0, "failed": 0}
    assert fake.stores == [] and fake.expunges == [] and fake.bare_expunges == 0


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
    assert got["purged"] == 0
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
    assert fake.bare_expunges == 0, "有 UIDPLUS 还用裸 EXPUNGE"


@pytest.mark.parametrize("bad", ["3w", "", "immediate ", "IMMEDIATE", "0"])
def test_an_unrecognised_policy_never_deletes(home, monkeypatch, bad):
    """认不出来的策略退回 never。**方向不能反** —— 打错一个字就开始删
    邮件是不可接受的。"""
    cfg = ImapConfig(host="h", user="u", password="p", retention=bad)
    assert cfg.retention_seconds() is None, f"{bad!r} 被当成了会删的策略"


# ─────────────────────────────────────────────────────────────
# 没有 UIDPLUS —— 真机就是这种
# ─────────────────────────────────────────────────────────────


def _mixed_inbox():
    """8416 是要清的 (归档校验过), 8417 是员工在别的客户端里标了待删的, 8418 普通。"""
    return {"INBOX": [
        (b"8416", rb"\Seen", _raw("到期的", day=16)),
        (b"8417", rb"\Seen \Deleted", _raw("别处标了待删", day=17)),
        (b"8418", rb"\Seen", _raw("普通", day=18)),
    ]}


def test_without_uidplus_only_our_mail_is_expunged(home, monkeypatch):
    """真机 (电信邮箱) 没有 UIDPLUS, 只有裸 EXPUNGE —— 它会清掉文件夹里所有
    带 \\Deleted 的。别人标记待删的必须原样留着, 而且标记要挂回去。

    9/21 版的做法是只打标记不清, 容量腾不出来; 而且这个函数从来没被调用,
    界面上的保留策略一直没生效。"""
    fake = PurgingIMAP(capabilities=("IMAP4rev1",), messages=_mixed_inbox())
    adapter = make(fake, monkeypatch, retention="immediate")
    seed(adapter, verified_ago=10)                   # 索引里只留 8416 (最小的那个)
    got = adapter.purge_folder("INBOX", "Inbox")

    uids = [u for u, _, _ in fake.messages["INBOX"]]
    assert got == {"purged": 1, "failed": 0}
    assert b"8416" not in uids, "到期的没删掉"
    assert b"8417" in uids, "别的客户端标记待删的被连带清掉了 —— 不可恢复"
    assert rb"\Deleted" in fake.flags_of(b"8417"), "别人的待删标记没挂回去"
    assert b"8418" in uids and rb"\Deleted" not in fake.flags_of(b"8418")
    assert fake.bare_expunges == 1


def test_failed_expunge_rolls_back_every_mark(home, monkeypatch):
    """EXPUNGE 失败: 我们这封的删除标记撤回 (否则下一轮同步会把它当成员工删了,
    连档案登记一起抹掉), 别人的标记挂回去, 索引里仍算在服务器上。"""
    fake = PurgingIMAP(capabilities=("IMAP4rev1",), messages=_mixed_inbox(), expunge_ok=False)
    adapter = make(fake, monkeypatch, retention="immediate")
    key = seed(adapter, verified_ago=10)
    got = adapter.purge_folder("INBOX", "Inbox")

    assert got == {"purged": 0, "failed": 1}
    assert rb"\Deleted" not in fake.flags_of(b"8416"), "删除标记没撤回"
    assert rb"\Deleted" in fake.flags_of(b"8417"), "别人的待删标记丢了"
    db = index_store.open_index()
    try:
        on_server = db.execute("SELECT on_server FROM messages WHERE source_key=?", (key,)).fetchone()[0]
    finally:
        db.close()
    assert on_server == 1, "没删掉却标成了服务器上已无"


def test_sync_all_runs_the_retention_policy_but_never_on_drafts(home, monkeypatch):
    """接线: 保留策略写好之后一直没人调用 (9/27 查出)。sync_all (收信 / 后台归档)
    每个文件夹归档之后接着清到期的; 草稿箱不清。"""
    fake = PurgingIMAP(capabilities=("IMAP4rev1",))
    adapter = make(fake, monkeypatch, retention="1w")
    called: list[str] = []
    monkeypatch.setattr(adapter, "purge_folder", lambda raw, role: called.append(role) or {})
    adapter.sync_all()
    assert "Inbox" in called and "Sent" in called
    assert "Drafts" not in called, "草稿箱不是归档对象, 不能按保留策略清"


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


def test_mail_from_an_older_uidvalidity_is_never_purged(home, monkeypatch):
    """9/27: 邮箱重建后旧 key 的 UID 指向另一封信。清理前必须核对 UIDVALIDITY ——
    原来不核对, 而 UIDVALIDITY 又一直读成 0, 两层保护同时缺席。"""
    fake = PurgingIMAP(capabilities=("UIDPLUS",))
    adapter = make(fake, monkeypatch, retention="immediate")
    seed(adapter, verified_ago=WEEK)
    fake.uidvalidity = b"99"                 # 服务器重建了邮箱
    got = adapter.purge_folder("INBOX", "Inbox")
    assert got["purged"] == 0
    assert fake.stores == [] and fake.expunges == [] and fake.bare_expunges == 0, \
        "版本对不上还删了 —— 删的是别的信"


def test_background_archive_command_only_touches_sources_with_an_index(capsys):
    """Companion 每 10 分钟调 `catfish-email archive`: 只跑有本地索引的来源 (IMAP)
    的 sync_all, 不去让 Apple Mail 收信 —— 后台定时的动作不惊动别的客户端。"""
    import json

    from catfish_email.cli_action import _cmd_archive

    calls: list[str] = []

    class Indexed:
        name = "imap"

        def sync_all(self):
            calls.append("sync_all")

    class Client:
        name = "apple_mail"

        def check_new_mail(self, **_):
            calls.append("check_new_mail")

    assert _cmd_archive([Client(), Indexed()], None) == 0
    assert calls == ["sync_all"]
    assert json.loads(capsys.readouterr().out)["ran"] == ["imap"]
