"""删掉的邮件不该留在列表里 (9/26 截图: 草稿箱里删掉/被替换的旧草稿还在, 一点开
「邮件不存在或已删除: uid=8502」)。"""
from __future__ import annotations

from catfish_email.adapters.base import ListFilter

from tests.test_imap_sync import CountingIMAP, isolated_index, make  # noqa: F401  (fixture)

DRAFTS = "&g0l6P3ux-"


def _ids(adapter, folder):
    return [m.id for m in adapter.list_messages(ListFilter(folder=folder, limit=50))]


def test_flagged_deleted_message_leaves_the_list(isolated_index, monkeypatch):  # noqa: F811
    """我们删信不 EXPUNGE (没 UIDPLUS), 原件带着 \\Deleted 留在文件夹里。以前同步把它
    当成"服务器上没了"标 on_server=0 继续列着 —— 那是给服务器容量清理准备的档案语义,
    不是给员工亲手删的信的。"""
    fake = CountingIMAP()
    adapter = make(fake, monkeypatch)
    before = _ids(adapter, "Inbox")
    victim = next(i for i in before if i.endswith("|8417"))
    adapter.delete_message(victim)
    assert victim not in _ids(adapter, "Inbox")


def test_replaced_draft_disappears_from_drafts(isolated_index, monkeypatch):  # noqa: F811
    fake = CountingIMAP()
    adapter = make(fake, monkeypatch)
    old = adapter.create_draft(to=["a@b.cn"], subject="预算", body="v1")
    assert old in _ids(adapter, "Drafts")
    new = adapter.create_draft(to=["a@b.cn"], subject="预算", body="v2", replaces=old)
    ids = _ids(adapter, "Drafts")
    assert new in ids and old not in ids


def test_draft_gone_from_server_is_not_kept_as_archive(isolated_index, monkeypatch):  # noqa: F811
    """草稿发出去后服务器上就没了。草稿箱不当档案馆, 不留 on_server=0 的旧稿。"""
    fake = CountingIMAP()
    adapter = make(fake, monkeypatch)
    draft = adapter.create_draft(to=["a@b.cn"], subject="s", body="b")
    assert draft in _ids(adapter, "Drafts")
    fake.messages[DRAFTS] = []
    assert _ids(adapter, "Drafts") == []
