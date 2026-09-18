"""IMAP 适配器 —— 夹具全部照 9/18 真机抓到的响应形态造。

# 背景

这一天在两条"读客户端本地数据"的路上各撞一次墙, 性质还不一样:

    Foxmail 7.2 (Win)  邮件文件加密 —— 有数据读不了
    新版 Outlook       无 COM 且本地无邮件 —— 网页套壳, 压根没数据

IMAP 是唯一不依赖客户端的路径。真机 (imap.chinatelecom.cn) 实测:

    CAPABILITY: IMAP4rev1 ID XLIST XAPPLEPUSHSERVICE AUTH=KERBEROS_V4
    没有 IDLE / UIDPLUS / CONDSTORE / QRESYNC, 没有 XOAUTH2
    UIDVALIDITY 1, UIDNEXT 8419, INBOX 57 封
    文件夹: INBOX, &XfJT0ZAB-(已发送), &g0l6P3ux-(草稿箱),
            &V4NXPnux-(垃圾箱), &XfJSIJZk-(已删除), &Xn9USmWHTvZZOQ-(广告文件夹)
    flags 只有 (\\Marked) —— **没有 \\Sent/\\Trash special-use**
    主题: =?GB2312?B?...?=

# 这个文件钉什么

  ① modified UTF-7 编解码 (用真机那六个名字做夹具)
  ② 文件夹角色识别只能靠解码后的名字, 不能靠 flags
  ③ FETCH 响应里 UID 在**前缀**里, 不在邮件内容里 —— 拿错整条 id 就是错的
  ④ id 必须带 UIDVALIDITY; 服务器重置时要拒绝旧 id 而不是去拉错邮件
  ⑤ 凭据绝不出现在日志和异常消息里
  ⑥ 只读: 写操作一律 NotSupportedError
  ⑦ 一封坏邮件不能让整个收件箱打不开
"""
from __future__ import annotations

import logging

import pytest

from catfish_email.adapters.base import (
    ClientNotRunningError,
    DataNotFoundError,
    ListFilter,
    NotSupportedError,
)
from catfish_email.adapters.imap_mail import (
    ImapAdapter,
    ImapConfig,
    folder_role,
    utf7_decode,
    utf7_encode,
)

#: 真机上那六个文件夹, 原样
REAL_FOLDERS = [
    ("INBOX", "INBOX"),
    ("&g0l6P3ux-", "草稿箱"),
    ("&XfJT0ZAB-", "已发送"),
    ("&V4NXPnux-", "垃圾箱"),
    ("&XfJSIJZk-", "已删除"),
    ("&Xn9USmWHTvZZOQ-", "广告文件夹"),
]

_SUBJ = "=?GB2312?B?1Nq9qM/uxL/H5bWl?="  # 在建项目清单


def _raw(subject: str = _SUBJ, sender: str = "ff_nic@chinatelecom.cn", day: int = 18) -> bytes:
    return (
        f"Date: Fri, {day} Sep 2026 13:05:57 +0800\r\n"
        f"From: =?GB2312?B?0MXPorCyyKvW0NDE?= <{sender}>\r\n"
        f"To: ffchenhb@chinatelecom.cn\r\n"
        f"Subject: {subject}\r\n"
        f"Message-ID: <{day}@chinatelecom.cn>\r\n"
        f"MIME-Version: 1.0\r\n"
        f'Content-Type: text/plain; charset="GB2312"\r\n\r\n'
    ).encode("utf-8")


class FakeIMAP:
    """照 imaplib 的真实响应形态造 —— 包括那个恶心的 FETCH 元组结构。"""

    def __init__(self, folders=None, messages=None, uidvalidity=b"1", login_ok=True):
        self.folders = folders or REAL_FOLDERS
        # {folder_raw: [(uid, flags, raw_bytes), ...]}
        self.messages = messages if messages is not None else {
            "INBOX": [(b"8418", rb"\Seen", _raw()), (b"8417", b"", _raw("plain subject", day=17))],
            "&XfJT0ZAB-": [(b"77", rb"\Seen", _raw("已发出的", day=16))],
        }
        self.uidvalidity = uidvalidity
        self.login_ok = login_ok
        self.selected: str | None = None
        self.logged_out = False

    def login(self, user, password):
        if not self.login_ok:
            import imaplib
            raise imaplib.IMAP4.error(f"AUTHENTICATIONFAILED for {user}")
        return ("OK", [b"LOGIN completed"])

    def list(self):
        return "OK", [
            f'(\\Marked) "/" "{raw}"'.encode() for raw, _ in self.folders
        ]

    def select(self, folder, readonly=False):
        assert readonly, "只读适配器绝不能用可写方式 SELECT"
        self.selected = folder.strip('"')
        if self.selected not in dict(self.folders):
            return "NO", [b"no such mailbox"]
        return "OK", [str(len(self.messages.get(self.selected, []))).encode()]

    def response(self, key):
        if key == "UIDVALIDITY":
            return "OK", [self.uidvalidity]
        return "OK", [None]

    def uid(self, command, *args):
        box = self.messages.get(self.selected or "", [])
        if command == "search":
            return "OK", [b" ".join(uid for uid, _, _ in box)]
        if command == "fetch":
            wanted = set(args[0].split(b","))
            out = []
            for uid, flags, raw in box:
                if uid not in wanted:
                    continue
                prefix = b"1 (UID " + uid + b" FLAGS (" + flags + b") BODY[HEADER] {%d}" % len(raw)
                out.append((prefix, raw))
                out.append(b")")
            return "OK", out
        return "NO", [b"unsupported"]

    def logout(self):
        self.logged_out = True
        return "BYE", [b"logout"]


@pytest.fixture
def adapter(monkeypatch):
    fake = FakeIMAP()
    monkeypatch.setattr(
        "catfish_email.adapters.imap_mail.imaplib.IMAP4_SSL",
        lambda host, port, timeout=None: fake,
    )
    a = ImapAdapter(ImapConfig(host="imap.example.cn", user="me@example.cn", password="s3cret"))
    a._fake = fake  # 测试自己留的把手
    return a


# ─────────────────────────────────────────────────────────────
# ① modified UTF-7
# ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("raw,decoded", REAL_FOLDERS)
def test_utf7_decodes_real_folder_names(raw, decoded):
    assert utf7_decode(raw) == decoded


@pytest.mark.parametrize("raw,decoded", REAL_FOLDERS)
def test_utf7_roundtrips(raw, decoded):
    assert utf7_decode(utf7_encode(decoded)) == decoded


def test_utf7_handles_literal_ampersand():
    assert utf7_decode("A&-B") == "A&B"
    assert utf7_decode(utf7_encode("R&D 报告")) == "R&D 报告"


def test_utf7_keeps_broken_names_instead_of_dying():
    """一个坏名字不该让整个文件夹列表挂掉。"""
    assert utf7_decode("&notbase64") == "&notbase64"
    assert utf7_decode("&@@@@-") == "&@@@@-"


# ─────────────────────────────────────────────────────────────
# ② 角色识别靠名字, 不靠 flags
# ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "decoded,role",
    [("INBOX", "Inbox"), ("已发送", "Sent"), ("草稿箱", "Drafts"),
     ("已删除", "Trash"), ("垃圾箱", "Junk"), ("广告文件夹", "广告文件夹")],
)
def test_folder_role_from_decoded_name(decoded, role):
    assert folder_role(decoded) == role


def test_folders_are_listed_and_decoded(adapter):
    assert adapter.folders() == REAL_FOLDERS


# ─────────────────────────────────────────────────────────────
# ③ ④ FETCH 解析与 id
# ─────────────────────────────────────────────────────────────


def test_uid_comes_from_the_fetch_prefix_not_the_body(adapter):
    """UID 在响应前缀里。取错的话整条 id 指向的就是别的邮件。"""
    msgs = adapter.list_messages(ListFilter(folder="Inbox", limit=10))
    ids = {m.id for m in msgs}
    assert ids == {"imap|INBOX|1|8418", "imap|INBOX|1|8417"}


def test_id_carries_uidvalidity(adapter):
    msg = adapter.list_messages(ListFilter(folder="Inbox", limit=1))[0]
    assert msg.id.split("|")[2] == "1"


def test_stale_uidvalidity_is_refused_not_silently_wrong(adapter):
    """服务器重建邮箱后旧 UID 指向完全不相干的邮件 —— 必须拒, 不能照拉。"""
    adapter._fake.uidvalidity = b"999"
    with pytest.raises(DataNotFoundError, match="UIDVALIDITY"):
        adapter.read_message("imap|INBOX|1|8418")


def test_malformed_id_is_refused(adapter):
    with pytest.raises(DataNotFoundError, match="非法"):
        adapter.read_message("eml-dir|x|y")


# ─────────────────────────────────────────────────────────────
# 内容
# ─────────────────────────────────────────────────────────────


def test_gb2312_subject_is_decoded(adapter):
    subjects = {m.subject for m in adapter.list_messages(ListFilter(folder="Inbox", limit=10))}
    assert "在建项目清单" in subjects


def test_seen_flag_becomes_is_read(adapter):
    by_uid = {m.id.split("|")[-1]: m for m in adapter.list_messages(ListFilter(folder="Inbox", limit=10))}
    assert by_uid["8418"].is_read is True
    assert by_uid["8417"].is_read is False


def test_unread_only_filter(adapter):
    unread = adapter.list_messages(ListFilter(folder="Inbox", unread_only=True, limit=10))
    assert [m.id.split("|")[-1] for m in unread] == ["8417"]


def test_sent_folder_is_reachable_by_role(adapter):
    sent = adapter.list_messages(ListFilter(folder="Sent", limit=10))
    assert [m.folder for m in sent] == ["Sent"]
    assert sent[0].subject == "已发出的"


def test_newest_first(adapter):
    msgs = adapter.list_messages(ListFilter(folder="Inbox", limit=10))
    assert [m.id.split("|")[-1] for m in msgs] == ["8418", "8417"]


# ─────────────────────────────────────────────────────────────
# ⑤ 凭据不外泄
# ─────────────────────────────────────────────────────────────


def test_password_never_appears_in_login_failure(monkeypatch, caplog):
    fake = FakeIMAP(login_ok=False)
    monkeypatch.setattr(
        "catfish_email.adapters.imap_mail.imaplib.IMAP4_SSL",
        lambda host, port, timeout=None: fake,
    )
    a = ImapAdapter(ImapConfig(host="h", user="me@example.cn", password="TOPSECRET"))
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(ClientNotRunningError) as excinfo:
            a.list_accounts()
    assert "TOPSECRET" not in str(excinfo.value)
    assert "TOPSECRET" not in caplog.text
    assert "授权码" in str(excinfo.value), "要告诉员工八成是授权码的问题"


def test_redacted_form_has_no_password():
    cfg = ImapConfig(host="h", user="u@x.cn", password="TOPSECRET", port=993)
    assert "TOPSECRET" not in cfg.redacted()
    assert cfg.redacted() == "u@x.cn@h:993"


# ─────────────────────────────────────────────────────────────
# ⑥ ⑦ 只读边界与容错
# ─────────────────────────────────────────────────────────────


def test_writes_are_not_claimed(adapter):
    msg = adapter.list_messages(ListFilter(folder="Inbox", limit=1))[0]
    with pytest.raises(NotSupportedError):
        adapter.delete_message(msg.id)
    with pytest.raises(NotSupportedError):
        adapter.mark_read(msg.id)


def test_select_is_always_readonly(adapter):
    """FakeIMAP.select 里断言了 readonly —— 这条测的是我们没传可写。"""
    adapter.list_messages(ListFilter(folder="*", limit=5))


def test_one_broken_message_does_not_empty_the_inbox(monkeypatch, caplog):
    fake = FakeIMAP(messages={
        "INBOX": [(b"1", b"", b"\x00\xff" * 50), (b"2", b"", _raw("好的那封"))],
    })
    monkeypatch.setattr(
        "catfish_email.adapters.imap_mail.imaplib.IMAP4_SSL",
        lambda host, port, timeout=None: fake,
    )
    a = ImapAdapter(ImapConfig(host="h", user="u", password="p"))
    msgs = a.list_messages(ListFilter(folder="Inbox", limit=10))
    assert "好的那封" in {m.subject for m in msgs}


def test_missing_config_says_what_to_set(monkeypatch):
    for var in ("CATFISH_IMAP_HOST", "CATFISH_IMAP_USER", "CATFISH_IMAP_PASSWORD"):
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(DataNotFoundError, match="CATFISH_IMAP_HOST"):
        ImapAdapter()
