"""10/14 Windows: 带大附件的信读不出来 / 进不了档案 —— 整封一次 FETCH 被服务器中途断开。
现在先问大小, 大信按 BODY.PEEK[]<起点.长度> 分段取。夹具模拟"整封取大于 N 字节就断"的服务器。"""
from __future__ import annotations

import imaplib

from catfish_email import archive_store
from catfish_email.adapters import imap_chunked
from catfish_email.adapters.imap_mail import ImapAdapter, ImapConfig

from tests.test_adapter_imap import FakeIMAP, _raw
from tests.test_imap_archive import ArchivingIMAP, home, make  # noqa: F401  (fixture)

BIG = _raw("业务外包框架协议") + b"\r\n" + b"A" * 5000   # 带"大附件"


class CutsBigFetches:
    """整封取超过 limit 字节就像现场一样断开; 分段取照常给。"""
    limit = 1000

    def uid(self, command, *args):
        spec = str(args[1]) if len(args) > 1 else ""
        if command == "fetch" and "BODY.PEEK[]" in spec and "<" not in spec:
            wanted = set(self._as_bytes(args[0]).split(b","))
            box = self.messages.get(self.selected or "", [])
            if any(len(raw) > self.limit for uid, _, raw in box if uid in wanted):
                raise imaplib.IMAP4.abort("socket error: EOF")
        self.calls.append(spec)
        return super().uid(command, *args)


class BigServer(CutsBigFetches, FakeIMAP):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.calls = []
        self.messages["INBOX"].append((b"9001", b"\\Seen", BIG))


def _adapter(fake, monkeypatch):
    monkeypatch.setattr(imap_chunked, "CHUNK", 1024)
    monkeypatch.setattr("catfish_email.adapters.imap_mail.imaplib.IMAP4_SSL", lambda h, p, timeout=None: fake)
    return ImapAdapter(ImapConfig(host="imap.example.cn", user="me@example.cn", password="s3cret"))


def test_big_mail_is_read_in_pieces(monkeypatch):
    fake = BigServer()
    msg = _adapter(fake, monkeypatch).read_message("imap|INBOX|1|9001")
    assert msg.subject == "业务外包框架协议"
    assert msg.is_read                                   # flags 从问大小那一步带过来
    pieces = [c for c in fake.calls if "<" in c]
    assert len(pieces) == len(BIG) // 1024 + 1, pieces
    assert all("PEEK" in c for c in fake.calls if "BODY" in c), "不能把信标成已读"


def test_small_mail_still_one_fetch(monkeypatch):
    fake = BigServer()
    _adapter(fake, monkeypatch).read_message("imap|INBOX|1|8418")
    assert not [c for c in fake.calls if "<" in c]


def test_pieces_join_to_the_exact_server_bytes(monkeypatch):
    fake = BigServer()
    _adapter(fake, monkeypatch)
    fake.selected = "INBOX"
    assert imap_chunked.fetch_raw_many(fake, [b"9001", b"8418"])["9001"] == BIG


def test_short_read_gives_up_instead_of_saving_half(monkeypatch):
    fake = BigServer()
    _adapter(fake, monkeypatch)
    fake.selected = "INBOX"
    assert imap_chunked.fetch_partial(fake, "9001", len(BIG) + 10) is None


class BigArchiving(CutsBigFetches, ArchivingIMAP):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.calls = []


def test_one_big_mail_no_longer_sinks_the_archive_batch(home, monkeypatch):  # noqa: F811
    monkeypatch.setattr(imap_chunked, "CHUNK", 1024)
    fake = BigArchiving()
    adapter = make(fake, monkeypatch)
    adapter.sync_folder("INBOX", "Inbox")
    adapter.archive_folder("INBOX", "Inbox")            # 设起点
    fake.messages["INBOX"].append((b"9000", b"", _raw("新来的小信", day=20)))
    fake.messages["INBOX"].append((b"9002", b"", BIG))  # 同一批里夹一封大的
    adapter.sync_folder("INBOX", "Inbox")
    stats = adapter.archive_folder("INBOX", "Inbox")
    assert stats["archived"] == 2, stats
    on_disk = sorted(p.read_bytes() for p in archive_store.archive_root().rglob("*.eml"))
    assert BIG in on_disk, "大信要逐字节原样进档案"
