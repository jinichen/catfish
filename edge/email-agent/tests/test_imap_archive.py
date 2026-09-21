"""归档接线 —— 把信真正落到盘上。

这一层的承诺是"服务器清理了本地还在"。兑现它的是三件事, 每件都有一条
测试盯着, 而且都是**做错了功能上看不出来**的那种:

  1. 存的是服务器原样发来的字节, 不是我们重新序列化的版本
  2. 归档不给邮件打 \\Seen —— 后台行为绝不能改员工邮箱的状态
  3. 校验没过的退回队列, 不是留在半路谁也不管
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from catfish_email import archive_store, index_store
from catfish_email.adapters.imap_mail import ImapConfig
from catfish_email.adapters.imap_sync import ImapSyncAdapter
from tests.test_adapter_imap import FakeIMAP, _raw


class ArchivingIMAP(FakeIMAP):
    """记下每一条 FETCH 的 spec —— 判据全在"用了什么 spec"上。"""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.fetch_specs: list[str] = []

    def uid(self, command, *args):
        if command == "fetch":
            self.fetch_specs.append(args[1])
        return super().uid(command, *args)


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("CATFISH_HOME", str(tmp_path))
    return tmp_path


def make(fake, monkeypatch) -> ImapSyncAdapter:
    monkeypatch.setattr(
        "catfish_email.adapters.imap_mail.imaplib.IMAP4_SSL",
        lambda host, port, timeout=None: fake,
    )
    return ImapSyncAdapter(
        ImapConfig(host="imap.example.cn", user="me@example.cn", password="s3cret")
    )


def _archive_everything(adapter, fake, folder="INBOX", role="Inbox"):
    """跑两轮: 第一轮只设起点, 第二轮才真归档。"""
    adapter.sync_folder(folder, role)
    first = adapter.archive_folder(folder, role)          # 设起点
    # 起点设在当前最大 UID, 所以得来一封新的才会被归档
    fake.messages[folder].append((b"9000", b"", _raw("新来的一封", day=20)))
    adapter.sync_folder(folder, role)
    second = adapter.archive_folder(folder, role)
    return first, second


# ─────────────────────────────────────────────────────────────
# 起点
# ─────────────────────────────────────────────────────────────


def test_first_run_sets_the_cutoff_and_archives_nothing(home, monkeypatch):
    """"从使用的第一天开始" —— 启用之前就在的一封都不回填。

    方向反了的代价很具体: 五年的邮箱, 几个 GB, 在员工刚装上软件、最没耐心
    那一刻开始下载。
    """
    fake = ArchivingIMAP()
    adapter = make(fake, monkeypatch)
    adapter.sync_folder("INBOX", "Inbox")

    got = adapter.archive_folder("INBOX", "Inbox")
    assert got["cutoff_set"] == 1
    assert got["archived"] == 0, "第一轮就开始回填历史了"

    marks = archive_store.read_cutoff(archive_store.archive_root(), "me@example.cn")
    assert marks["INBOX"]["uid"] == 8418, f"起点该是当前最大 UID, 实际 {marks}"


def test_only_mail_above_the_cutoff_gets_archived(home, monkeypatch):
    fake = ArchivingIMAP()
    adapter = make(fake, monkeypatch)
    _, second = _archive_everything(adapter, fake)
    assert second["archived"] == 1, "只该归档起点之后新来的那一封"
    assert second["verified"] == 1


# ─────────────────────────────────────────────────────────────
# 三条不能错的
# ─────────────────────────────────────────────────────────────


def test_archiving_never_marks_mail_as_read(home, monkeypatch):
    """**BODY.PEEK[] 不是 BODY[]。**

    归档是后台行为。用 BODY[] 的话, 员工没读过的邮件会被我们标成已读 ——
    直接改了他邮箱的状态, 而且他看不出是谁干的, 手机上也同步变了。

    判据放在 spec 字符串上而不是 flags 上: 假服务器不模拟 \\Seen 的副作用,
    盯 flags 会得到一条永远绿的测试。
    """
    fake = ArchivingIMAP()
    adapter = make(fake, monkeypatch)
    _archive_everything(adapter, fake)

    body_fetches = [s for s in fake.fetch_specs if "BODY" in s and "HEADER" not in s]
    assert body_fetches, "根本没取过正文, 这条测试没测到东西"
    for spec in body_fetches:
        assert "BODY.PEEK[" in spec, f"用了会打 \\Seen 的 spec: {spec}"
        assert "BODY[" not in spec.replace("BODY.PEEK[", ""), f"spec 里有裸 BODY[]: {spec}"


def test_archive_stores_the_servers_bytes_verbatim(home, monkeypatch):
    """存的必须是服务器原样发来的字节。

    拿解析后的 EmailMessage 再 serialize 一遍, Python 会重新折行、规范化
    头部大小写、按 policy 重编码。那样 archive_sha256 就只能证明"我们的
    序列化是确定的", 证明不了档案跟原件一致 —— 而那是它唯一的用途。
    """
    fake = ArchivingIMAP()
    adapter = make(fake, monkeypatch)
    _archive_everything(adapter, fake)

    root = archive_store.archive_root()
    files = list(root.rglob("*.eml"))
    assert len(files) == 1, f"应该只归档了一封, 实际 {files}"
    on_disk = files[0].read_bytes()

    expected = _raw("新来的一封", day=20)
    assert on_disk == expected, "盘上那份跟服务器发来的不是逐字节一样"


def test_failed_verification_goes_back_to_the_queue(home, monkeypatch):
    """校验没过 → 整条登记抹掉, 回队列。

    只清 verified_at 而留着 archive_path 的话, 它既不在待办队列里 (有 path),
    又永远不算已校验 —— **悄悄地谁也不管了**。而服务器端清理只认
    verified_at, 所以它也不会被误删; 后果是这封信永远停在半路, 没有任何
    地方会报出来。
    """
    fake = ArchivingIMAP()
    adapter = make(fake, monkeypatch)
    adapter.sync_folder("INBOX", "Inbox")
    adapter.archive_folder("INBOX", "Inbox")
    fake.messages["INBOX"].append((b"9000", b"", _raw("会坏的那封", day=20)))
    adapter.sync_folder("INBOX", "Inbox")

    # 让校验必然失败
    monkeypatch.setattr(archive_store, "verify_message", lambda *a, **kw: "假装坏了")
    got = adapter.archive_folder("INBOX", "Inbox")

    assert got["failed"] == 1
    assert got["archived"] == 0, "校验没过还算归档成功了"

    db = index_store.open_index()
    try:
        row = db.execute(
            "SELECT archive_path, archived_at, verified_at FROM messages "
            "WHERE source_key LIKE '%:9000'"
        ).fetchone()
        pending = index_store.pending_archive(
            db, account="me@example.cn", folder="Inbox", limit=10
        )
    finally:
        db.close()
    assert row == (None, None, None), f"登记没清干净: {row}"
    assert any(k.endswith(":9000") for k, _, _ in pending), "没回到待办队列"


# ─────────────────────────────────────────────────────────────
# 渐进 / 容错
# ─────────────────────────────────────────────────────────────


def test_archived_mail_is_not_fetched_again(home, monkeypatch):
    """已落地的不再取 —— 否则每轮同步都把整个档案重下一遍。"""
    fake = ArchivingIMAP()
    adapter = make(fake, monkeypatch)
    _archive_everything(adapter, fake)
    before = len([s for s in fake.fetch_specs if "PEEK[]" in s])

    adapter.sync_folder("INBOX", "Inbox")
    got = adapter.archive_folder("INBOX", "Inbox")
    after = len([s for s in fake.fetch_specs if "PEEK[]" in s])

    assert got["archived"] == 0
    assert after == before, "已归档的又取了一遍"


def test_a_message_the_server_no_longer_has_is_not_a_failure(home, monkeypatch):
    """FETCH 取不到不算失败, 留在队列下轮重试。

    算失败的话, 一封刚好被别的客户端删掉的邮件会让计数永远难看, 而那不是
    我们的问题。真正的失败是"写盘或校验出错"。
    """
    fake = ArchivingIMAP()
    adapter = make(fake, monkeypatch)
    adapter.sync_folder("INBOX", "Inbox")
    adapter.archive_folder("INBOX", "Inbox")
    fake.messages["INBOX"].append((b"9000", b"", _raw("要消失的", day=20)))
    adapter.sync_folder("INBOX", "Inbox")
    # 索引里有了, 但服务器上取不到
    monkeypatch.setattr(adapter, "_fetch_raw_bytes", lambda conn, uids: {})

    got = adapter.archive_folder("INBOX", "Inbox")
    assert got == {"archived": 0, "verified": 0, "failed": 0, "cutoff_set": 0}


def test_write_failure_is_counted_and_does_not_abort_the_batch(home, monkeypatch):
    """一封写不下去不能拖垮整批 —— 磁盘满/路径非法是单封的事。"""
    fake = ArchivingIMAP()
    adapter = make(fake, monkeypatch)
    adapter.sync_folder("INBOX", "Inbox")
    adapter.archive_folder("INBOX", "Inbox")
    fake.messages["INBOX"].append((b"9000", b"", _raw("写不下去", day=20)))
    adapter.sync_folder("INBOX", "Inbox")

    monkeypatch.setattr(
        archive_store, "write_message",
        lambda *a, **kw: (_ for _ in ()).throw(OSError("磁盘满了")),
    )
    got = adapter.archive_folder("INBOX", "Inbox")
    assert got["failed"] == 1 and got["archived"] == 0
