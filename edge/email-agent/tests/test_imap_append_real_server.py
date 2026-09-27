"""存草稿后拿到它的 id —— 按 9/27 真机探测的行为测 (imap.chinatelecom.cn):

    APPEND: OK [b'[APPENDUID 2 8526] APPEND completed']      ← 没有 UIDPLUS 也带
    同一连接: 重新 SELECT / NOOP / 等 10 秒, 搜 Message-ID 都是空
    新连接:   立刻搜到 8526

FakeIMAP 的默认行为已经改成跟真机一样 (appenduid=True, stale_session_after_append=True)。
之前夹具"同连接立刻可见", 三种找回办法测试全绿、真机全挂 —— 修了三轮才看出来。
"""
from __future__ import annotations

from tests.test_imap_writes import adapter  # noqa: F401  (pytest fixture)


def test_uses_appenduid_from_the_append_response(adapter):  # noqa: F811
    draft_id = adapter.create_draft(to=["a@b.cn"], subject="单价-上海擎标", body="正文")
    assert draft_id.endswith("|9000"), "UID 取自 APPENDUID"
    assert adapter.read_message(draft_id).subject == "单价-上海擎标"


def test_new_draft_id_matches_the_ids_in_the_list(adapter):  # noqa: F811
    """新草稿 id 跟列表里的 id 必须是一个体系。1.0.37 拿 APPENDUID 的 2 拼 id, 而
    _select 当时读错一律给 "0", 新草稿一打开就「邮箱已重建 2 → 0」。"""
    from catfish_email.adapters.base import ListFilter

    draft_id = adapter.create_draft(to=["a@b.cn"], subject="s", body="b")
    listed = [m.id for m in adapter.list_messages(ListFilter(folder="Drafts", limit=10))]
    assert draft_id in listed


def test_without_appenduid_it_looks_again_on_a_fresh_connection(adapter, monkeypatch):  # noqa: F811
    fake = adapter._fake
    fake.appenduid = False
    logins = []
    real_login = fake.login
    monkeypatch.setattr(fake, "login", lambda u, p: logins.append(u) or real_login(u, p))
    draft_id = adapter.create_draft(to=["a@b.cn"], subject="s", body="b")
    assert len(logins) >= 1, "旧连接看不到刚存的草稿, 必须重新登录再找"
    assert adapter.read_message(draft_id).subject == "s"


def test_the_appending_connection_really_cannot_see_its_own_append(adapter):  # noqa: F811
    """钉住夹具本身: 它要像真机一样"看不见", 否则上面两条又会变成假绿。"""
    conn = adapter._connect()
    conn.select('"&g0l6P3ux-"', readonly=True)
    before = conn.uid("search", None, "ALL")[1][0]
    conn.append('"&g0l6P3ux-"', r"(\Draft)", None, b"Subject: x\r\n\r\nb")
    conn.select('"&g0l6P3ux-"', readonly=True)
    assert conn.uid("search", None, "ALL")[1][0] == before
