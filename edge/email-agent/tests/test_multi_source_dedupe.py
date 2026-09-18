"""多来源合并: 同一封邮件留谁的 id。

# 撞到的是什么

鸿波 catch "现在同时从客户端和 IMAP 一起吗？会打架的"。

`catfish-email list` 不带 --client 时走 get_all_adapters() —— **所有来源
一起跑再合并**。这是 5/18 定的设计, 前提是"员工同时用 Mail.app 看 iCloud +
Foxmail 看企业邮箱"。9/18 加了 IMAP 之后, 同一个邮箱会**同时**被本地客户端
和 IMAP 读到, 这个前提第一次被打破。

去重本来就有, 按 (folder, Message-ID), 两边都能合上。问题在留谁:

    旧: return 0 if "|emlx:" in message.id else 1
        → apple-mail 和 imap 都是 1, 先到先得
        → 而 imap 当时被我排在候选第一位, 于是 imap 赢

IMAP 是只读的, 写操作一律 NotSupportedError。所以 imap 赢的后果是:
**列表上一点征兆都没有, 员工点删除才发现废了**。这种症状没人会往"来源选
错了"上想。

# 这个文件钉什么

判据是"这个 id 还能不能用来做事", 不是哪个来源更新更快:

    2  能执行动作的客户端 (Apple Mail 的 AppleScript id / Outlook)
    1  只读来源 (IMAP)
    0  本地缓存 id (EMLX) —— 只是个文件, 动作路由不回客户端
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from catfish_email.cli_read import _dedupe_messages, _source_priority

READ_ONLY = frozenset({"imap"})


@dataclass
class FakeMsg:
    id: str
    message_id: str | None = "<same@chinatelecom.cn>"
    folder: str = "Inbox"
    subject: str = "【网信安预警2026年第071期】"
    date: str = "2026-09-18T11:37:04"
    extras: dict = field(default_factory=dict)


APPLE = ("apple-mail", FakeMsg(id="apple_mail|Chinatelecom|2610"))
EMLX = ("apple-mail", FakeMsg(id="apple_mail|Chinatelecom|emlx:2610"))
IMAP = ("imap", FakeMsg(id="imap|INBOX|1|8418"))


# ─────────────────────────────────────────────────────────────
# 优先级本身
# ─────────────────────────────────────────────────────────────


def test_a_client_that_can_act_beats_a_read_only_source():
    assert _source_priority(*APPLE, READ_ONLY) > _source_priority(*IMAP, READ_ONLY)


def test_a_read_only_source_still_beats_a_local_cache_id():
    """IMAP 至少还连着服务器; EMLX 只是磁盘上一个文件, 动作路由不回客户端。"""
    assert _source_priority(*IMAP, READ_ONLY) > _source_priority(*EMLX, READ_ONLY)


def test_unknown_adapters_are_assumed_able_to_act():
    """没登记成只读的一律当成能做事 —— 漏登记的代价该是"多试一次然后报错",
    而不是"悄悄降级成看不了"。"""
    assert _source_priority("outlook-win", FakeMsg(id="outlook|x|1"), READ_ONLY) == 2


# ─────────────────────────────────────────────────────────────
# 合并结果
# ─────────────────────────────────────────────────────────────


def test_same_mail_from_both_sources_keeps_the_one_that_can_delete():
    """这条是整件事的命门。"""
    out = _dedupe_messages([IMAP, APPLE], READ_ONLY)
    assert len(out) == 1
    assert out[0][0] == "apple-mail"
    assert out[0][1].id == "apple_mail|Chinatelecom|2610"


def test_order_does_not_decide_the_winner():
    """IMAP 排前排后都不该改变结果 —— 旧实现就是"先到先得", 候选顺序一动
    行为就变, 而顺序是另一个文件里的事 (inbox.py 的候选表)。"""
    first = _dedupe_messages([IMAP, APPLE], READ_ONLY)
    second = _dedupe_messages([APPLE, IMAP], READ_ONLY)
    assert first[0][1].id == second[0][1].id == "apple_mail|Chinatelecom|2610"


def test_imap_only_mail_is_kept():
    """只有 IMAP 读到的邮件当然要留 —— 只读好过看不见。

    Windows 上这是常态: 新版 Outlook 无 COM, Foxmail 加密, IMAP 是唯一的路。
    """
    out = _dedupe_messages([IMAP], READ_ONLY)
    assert len(out) == 1 and out[0][0] == "imap"


def test_different_mails_are_not_merged():
    other = ("imap", FakeMsg(id="imap|INBOX|1|9999", message_id="<other@x.cn>"))
    out = _dedupe_messages([APPLE, other], READ_ONLY)
    assert len(out) == 2


def test_same_message_id_in_different_folders_is_not_merged():
    """收件箱和已发送里各有一封同 Message-ID 的, 是两条记录。"""
    sent = ("imap", FakeMsg(id="imap|Sent|1|77", folder="Sent"))
    out = _dedupe_messages([APPLE, sent], READ_ONLY)
    assert len(out) == 2


def test_mail_without_message_id_falls_back_to_per_adapter_identity():
    """没有 Message-ID 就不能跨来源合 —— 主题+时间不是身份, 会误合。"""
    a = ("apple-mail", FakeMsg(id="apple_mail|x|1", message_id=""))
    b = ("imap", FakeMsg(id="imap|INBOX|1|1", message_id=""))
    assert len(_dedupe_messages([a, b], READ_ONLY)) == 2


@pytest.mark.parametrize("read_only", [frozenset(), frozenset({"imap"})])
def test_dedupe_never_drops_everything(read_only):
    """无论怎么配, 合并都不许把列表清空。"""
    assert _dedupe_messages([APPLE, IMAP, EMLX], read_only)


# ─────────────────────────────────────────────────────────────
# 只读名单从真 adapter 上来, 不是写死的
# ─────────────────────────────────────────────────────────────


def test_read_only_flag_matches_what_the_adapter_can_actually_do():
    """`read_only` 这面旗必须跟真实能力一致, 否则优先级就是在骗人。

    9/18 当天这面旗翻过一次: 上午 ImapAdapter 是纯只读的 (read_only=True),
    下午补了标已读/删除/存草稿/发送之后翻成 False。翻旗的同时这条测试也
    跟着翻 —— 但**不是把断言改成新值就算数**, 而是改成"照着真实现覆盖了
    哪些方法来判", 这样下次谁再动能力, 这条会自己对上或者自己挂掉。
    """
    from catfish_email.adapters.base import EmailAdapter
    from catfish_email.adapters.imap_mail import ImapAdapter

    writes = ("mark_read", "delete_message", "create_draft", "send_message")
    can_write = any(
        getattr(ImapAdapter, m) is not getattr(EmailAdapter, m) for m in writes
    )
    assert ImapAdapter.read_only is not can_write, (
        f"read_only={ImapAdapter.read_only} 但实际"
        f"{'能' if can_write else '不能'}写 —— 旗和实现对不上"
    )


def test_an_adapter_that_cannot_write_is_marked_read_only():
    """反面: 真的只能看的来源必须挂旗, 否则 dedupe 会把它当成能做事的,
    员工点删除才发现废了。"""
    from catfish_email.adapters.base import EmailAdapter

    assert EmailAdapter.read_only is False, "基类默认当成能写 —— 漏标的代价是多试一次然后报错, 比悄悄降级好"


def test_local_client_adapters_are_not_marked_read_only():
    from catfish_email.adapters.apple_mail import AppleMailAdapter

    assert AppleMailAdapter.read_only is False
