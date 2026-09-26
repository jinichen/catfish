"""IMAP 草稿: 回复写对线程头 + 在鲶鱼里改草稿 (9/26)。见 adapters/imap_drafts.py。"""
from __future__ import annotations

from catfish_email.adapters.base import ListFilter

from tests.test_imap_writes import adapter, first  # noqa: F401  (pytest fixture)


def test_reply_with_internal_id_uses_the_originals_message_id(adapter):  # noqa: F811
    """前端传的是 `imap|INBOX|...`。以前原样写进 In-Reply-To, 对方客户端串不上线程。"""
    original = first(adapter)
    assert original.message_id, "测试数据里的原邮件要有 Message-ID"
    draft_id = adapter.create_draft(to=["a@b.cn"], subject="Re: x", body="收到", in_reply_to=original.id)
    got = adapter.read_message(draft_id)
    assert got.in_reply_to == original.message_id
    assert not got.in_reply_to.startswith("imap|")


def test_editing_a_draft_replaces_it_and_keeps_the_thread(adapter):  # noqa: F811
    original = first(adapter)
    old_id = adapter.create_draft(to=["a@b.cn"], subject="Re: x", body="初稿", in_reply_to=original.id)
    new_id = adapter.create_draft(to=["a@b.cn"], subject="Re: x", body="改过的", replaces=old_id)

    got = adapter.read_message(new_id)
    assert "改过的" in got.body_text
    assert got.in_reply_to == original.message_id, "改完还得是同一个线程的回复"

    old_uid = old_id.rsplit("|", 1)[-1]
    def _s(v):
        return v.decode() if isinstance(v, bytes) else str(v)
    assert any(_s(uid) == old_uid and r"\Deleted" in flags for uid, _, flags in adapter._fake.stores), \
        "旧版本要打删除标记"
    drafts = adapter.list_messages(ListFilter(folder="Drafts", limit=50))
    assert [m.id for m in drafts if "初稿" in (m.body_text or m.snippet or "")] == []
    assert new_id in [m.id for m in drafts]


def test_editing_never_copies_the_old_version_into_trash(adapter):  # noqa: F811
    old_id = adapter.create_draft(to=["a@b.cn"], subject="s", body="v1")
    copies_before = len(getattr(adapter._fake, "copies", []))
    adapter.create_draft(to=["a@b.cn"], subject="s", body="v2", replaces=old_id)
    assert len(getattr(adapter._fake, "copies", [])) == copies_before
