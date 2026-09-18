"""IMAP 的写操作: 附件 / 标已读 / 删除 / 存草稿 / 发送。

# 为什么补这些

鸿波问 "imap 还能够和客户端一样用鲶鱼处理邮件处理和回复？" —— 查下来是
不能: ImapAdapter 只覆盖了 check_new_mail, 其余写操作全落到基类的
NotSupportedError。

在 macOS 上无所谓 (Apple Mail 还在)。**但达华那些机器上 IMAP 是唯一的路**
—— 新版 Outlook 无 COM、Foxmail 把邮件加密了。只读意味着员工打开邮件页
看得见、什么都做不了, 按钮都在, 一点就报错。

# 这个文件里最要紧的一条

`test_delete_never_expunges_blindly`。真机 CAPABILITY 没有 UIDPLUS, 只有
裸 EXPUNGE, 而**裸 EXPUNGE 会清掉当前文件夹里所有打了 \\Deleted 的邮件**,
包括员工在 Foxmail / Outlook 上标了删除、还没压缩的那些。员工在鲶鱼里删
一封, 另一个客户端里攒了半年的待删邮件一起没了, 不可恢复。

换来的好处仅仅是"原件早几天从服务器上消失"。完全不值。
"""
from __future__ import annotations

import pytest

from catfish_email.adapters.base import DataNotFoundError, EmailAdapterError, ListFilter
from catfish_email.adapters.imap_mail import ImapAdapter, ImapConfig

from tests.test_adapter_imap import FakeIMAP


@pytest.fixture
def adapter(monkeypatch):
    fake = FakeIMAP()
    monkeypatch.setattr(
        "catfish_email.adapters.imap_mail.imaplib.IMAP4_SSL",
        lambda host, port, timeout=None: fake,
    )
    a = ImapAdapter(ImapConfig(host="imap.example.cn", user="me@example.cn", password="s3cret"))
    a._fake = fake
    return a


def first(adapter):
    return adapter.list_messages(ListFilter(folder="Inbox", limit=1))[0]


# ─────────────────────────────────────────────────────────────
# 标已读
# ─────────────────────────────────────────────────────────────


def test_mark_read_sets_the_seen_flag(adapter):
    msg = first(adapter)
    adapter.mark_read(msg.id)
    assert adapter._fake.stores[-1][1] == "+FLAGS"
    assert r"\Seen" in adapter._fake.stores[-1][2]


def test_mark_unread_removes_it(adapter):
    adapter.mark_read(first(adapter).id, read=False)
    assert adapter._fake.stores[-1][1] == "-FLAGS"


def test_mark_read_shows_up_in_the_next_listing(adapter):
    """标完要真的反映出来 —— 不然员工点一下没反应, 会以为坏了。"""
    msg = first(adapter)
    adapter.mark_read(msg.id)
    again = [m for m in adapter.list_messages(ListFilter(folder="Inbox", limit=9)) if m.id == msg.id]
    assert again and again[0].is_read is True


def test_stale_uidvalidity_refuses_to_write(adapter):
    """拿着旧 id 去 STORE 就是**对另一封不相干的邮件动手**。

    读错了顶多显示错, 写错了是标错/删错邮件。所以写之前必须校 UIDVALIDITY。
    """
    msg = first(adapter)
    adapter._fake.uidvalidity = b"2"
    with pytest.raises(DataNotFoundError):
        adapter.mark_read(msg.id)
    assert adapter._fake.stores == [], "UIDVALIDITY 不对还是写出去了"


# ─────────────────────────────────────────────────────────────
# 删除 —— 这一节是重点
# ─────────────────────────────────────────────────────────────


def test_delete_copies_to_trash_first(adapter):
    """先留副本再打标记。顺序反了的话中间失败就没有后悔药。"""
    adapter.delete_message(first(adapter).id)
    assert adapter._fake.copies, "没往已删除里留副本"
    assert adapter._fake.copies[0][1] == "&XfJSIJZk-"  # 已删除


def test_delete_marks_the_original_deleted(adapter):
    adapter.delete_message(first(adapter).id)
    assert adapter._fake.stores[-1][1] == "+FLAGS"
    assert r"\Deleted" in adapter._fake.stores[-1][2]


def test_delete_never_expunges_blindly(adapter):
    """**整个改动里最要紧的一条。**

    服务器没有 UIDPLUS (真机实测), 没有 `UID EXPUNGE`, 只有裸 `EXPUNGE`。
    裸 EXPUNGE 会连带清掉员工在别的客户端标了删除、还没压缩的所有邮件,
    而且不可恢复。宁可原件多在服务器上留几天。
    """
    assert "UIDPLUS" not in adapter._fake.capabilities  # 夹具照真机
    adapter.delete_message(first(adapter).id)
    assert adapter._fake.expunges == [], "在没有 UIDPLUS 的服务器上执行了 EXPUNGE"


def test_delete_expunges_precisely_when_uidplus_is_available(adapter):
    """有 UIDPLUS 就只清这一封 —— 别人标的 \\Deleted 一根汗毛不动。"""
    adapter._fake.capabilities = ("IMAP4REV1", "UIDPLUS")
    msg = first(adapter)
    adapter.delete_message(msg.id)
    assert adapter._fake.expunges == [msg.id.split("|")[-1]]


def test_deleted_mail_disappears_from_the_listing(adapter):
    """因为不 EXPUNGE, 原件还躺在收件箱里 —— 列表不过滤的话, 员工删完一刷新
    它又回来了。"""
    msg = first(adapter)
    adapter.delete_message(msg.id)
    assert msg.id not in [m.id for m in adapter.list_messages(ListFilter(folder="Inbox", limit=9))]


def test_delete_without_a_trash_folder_still_marks_but_warns(adapter, caplog):
    adapter._fake.folders = [("INBOX", "INBOX")]
    with caplog.at_level("WARNING"):
        adapter.delete_message(first(adapter).id)
    assert "已删除" in caplog.text
    assert adapter._fake.stores, "没有废纸篓也该打上删除标记"


# ─────────────────────────────────────────────────────────────
# 附件 (纯读)
# ─────────────────────────────────────────────────────────────


def test_export_attachment_writes_the_real_bytes(adapter, monkeypatch):
    # 文件名故意用中文 —— 国内企业邮件的附件基本都是中文名, 而 RFC2231
    # 编码那一段最容易出问题。这里走最常见的形态: 原样 UTF-8 放在头里。
    raw = (
        "From: a@b.cn\r\nTo: c@d.cn\r\nSubject: att\r\n"
        "Content-Type: multipart/mixed; boundary=B\r\n\r\n"
        "--B\r\nContent-Type: text/plain\r\n\r\nhi\r\n"
        "--B\r\nContent-Type: application/pdf\r\n"
        'Content-Disposition: attachment; filename="报表.pdf"\r\n\r\n'
        "PDFBYTES\r\n--B--\r\n"
    ).encode("utf-8")
    adapter._fake.messages["INBOX"] = [(b"5", b"", raw)]
    msg = first(adapter)
    path = adapter.export_attachment(msg.id, "报表.pdf")
    assert path.name == "报表.pdf", "落盘要用原名, 系统打开时显示的就是它"
    assert path.read_bytes().startswith(b"PDFBYTES")


def test_export_attachment_says_which_name_it_could_not_find(adapter):
    with pytest.raises(DataNotFoundError, match="没有名为"):
        adapter.export_attachment(first(adapter).id, "不存在.pdf")


# ─────────────────────────────────────────────────────────────
# 草稿
# ─────────────────────────────────────────────────────────────


def test_create_draft_appends_to_the_drafts_folder(adapter):
    adapter.create_draft(to=["a@b.cn"], subject="主题", body="正文")
    folder, flags, _ = adapter._fake.appends[-1]
    assert folder == "&g0l6P3ux-"  # 草稿箱
    assert r"\Draft" in flags


def test_create_draft_returns_a_usable_id(adapter):
    """没有 UIDPLUS 就拿不到 APPEND 的新 UID, 得自己生成 Message-ID 再搜回来。
    返回的 id 必须真能拿去读。"""
    draft_id = adapter.create_draft(to=["a@b.cn"], subject="主题", body="正文")
    assert adapter.read_message(draft_id).subject == "主题"


def test_draft_body_survives_chinese(adapter):
    """自己发的一律 UTF-8 —— 国内企业邮箱那些 GB2312 我们只负责读。"""
    draft_id = adapter.create_draft(
        to=["a@b.cn"], subject="在建项目清单", body="各位领导同事，大家好！"
    )
    got = adapter.read_message(draft_id)
    assert got.subject == "在建项目清单"
    assert "各位领导同事" in got.body_text


def test_reply_draft_carries_the_thread_headers(adapter):
    draft_id = adapter.create_draft(
        to=["a@b.cn"], subject="Re: x", body="收到", in_reply_to="<orig@x.cn>"
    )
    got = adapter.read_message(draft_id)
    assert got.in_reply_to == "<orig@x.cn>"


def test_create_draft_without_a_drafts_folder_fails_loudly(adapter):
    adapter._fake.folders = [("INBOX", "INBOX")]
    with pytest.raises(DataNotFoundError, match="草稿箱"):
        adapter.create_draft(to=["a@b.cn"], subject="s", body="b")


# ─────────────────────────────────────────────────────────────
# 发送
# ─────────────────────────────────────────────────────────────


class FakeSMTP:
    sent: list[tuple[str, list[str], bytes]] = []

    def __init__(self, host, port):
        self.host, self.port = host, port

    def login(self, user, password):
        self.user = user

    def sendmail(self, sender, recipients, raw):
        FakeSMTP.sent.append((sender, recipients, raw))

    def quit(self):
        pass


@pytest.fixture
def smtp(monkeypatch):
    FakeSMTP.sent = []
    from catfish_email import smtp_send

    real = smtp_send.send
    monkeypatch.setattr(
        smtp_send, "send",
        lambda cfg, raw, rcpt, **kw: real(cfg, raw, rcpt, transport=FakeSMTP),
    )
    return FakeSMTP


def test_send_goes_out_over_smtp_to_every_recipient(adapter, smtp):
    draft_id = adapter.create_draft(
        to=["a@b.cn"], cc=["c@d.cn"], subject="s", body="b"
    )
    adapter.send_message(draft_id)
    sender, recipients, _ = smtp.sent[-1]
    assert sender == "me@example.cn"
    assert set(recipients) == {"a@b.cn", "c@d.cn"}


def test_send_files_a_copy_into_sent(adapter, smtp):
    """服务器不会因为你 SMTP 发了就自动往已发送塞一份 —— 得自己 APPEND。"""
    adapter.send_message(adapter.create_draft(to=["a@b.cn"], subject="s", body="b"))
    assert any(folder == "&XfJT0ZAB-" for folder, _, _ in adapter._fake.appends)


def test_send_removes_the_draft(adapter, smtp):
    draft_id = adapter.create_draft(to=["a@b.cn"], subject="s", body="b")
    adapter.send_message(draft_id)
    left = adapter.list_messages(ListFilter(folder="Drafts", limit=9))
    assert draft_id not in [m.id for m in left]


def test_a_draft_without_recipients_is_refused_before_connecting(adapter, smtp):
    draft_id = adapter.create_draft(to=[], subject="s", body="b")
    with pytest.raises(EmailAdapterError, match="收件人"):
        adapter.send_message(draft_id)
    assert smtp.sent == [], "没有收件人却还是连了 SMTP"
