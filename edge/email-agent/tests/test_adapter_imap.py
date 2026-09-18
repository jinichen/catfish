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
    """照 imaplib 的真实响应形态造 —— 包括那个恶心的 FETCH 元组结构。

    ⚠ 这个形状**在真服务器上对过**, 不是照我的理解编的 (9/18,
    imap.chinatelecom.cn, 拿真 UID FETCH 的响应逐项打印):

        [0] tuple len=2  parts=['bytes', 'bytes']
             prefix: b'55 (UID 8416 FLAGS () BODY[HEADER] {1322}'
        [1] bytes: b')'
        [2] tuple len=2  ...  b'56 (UID 8417 FLAGS () BODY[HEADER] {6099}'
        [3] bytes: b')'
        ...
        parsed 3 of 3 requested

    要点:
      · 每封是 (前缀 bytes, 原始邮件 bytes) 的二元组, 后面跟一个单独的 b')'
      · **UID 和 FLAGS 都在前缀里**, 不在邮件内容里 —— 取错整条 id 就是错的
      · 前缀开头那个数字是序号 (55/56/57), 不是 UID。别混
      · 真机上 FLAGS 是空的 `()` (57 封全未读), 所以空 flags 这条路必须能走通

    为什么强调这个: 上一版 Foxmail 的 .box 夹具是照我们"记录的格式"造的,
    绿了四个月, 到真机上六个 bug —— 当时 conftest 自己就留过警告说
    "这些测试不证明能读真文件"。同样的错不犯第二次。
    """

    def __init__(
        self, folders=None, messages=None, uidvalidity=b"1", login_ok=True,
        capabilities=("IMAP4REV1", "ID", "XLIST"),
    ):
        #: 真机 (imap.chinatelecom.cn) 的 CAPABILITY 里**没有 UIDPLUS**,
        #: 所以默认就不给 —— 夹具要长得像真机, 不是长得像理想服务器。
        self.capabilities = capabilities
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
        #: 每次 SELECT 的 (文件夹, readonly) —— 读路径必须全是 readonly=True
        self.selects: list[tuple[str, bool]] = []
        #: 每次 STORE 的 (uid, 操作, flags), 给写操作的测试核对
        self.stores: list[tuple[bytes, str, str]] = []
        self.copies: list[tuple[bytes, str]] = []
        self.expunges: list[bytes] = []
        self.appends: list[tuple[str, str, bytes]] = []

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
        # 9/18 下午: 原来这里是 `assert readonly, "只读适配器绝不能用可写方式
        # SELECT"` —— 那时 adapter 确实一个写操作都没有。补了标已读/删除/
        # 存草稿之后这条不变量不得不松, 但**不是松成"随便写"**: 改成记账,
        # 由 test_only_write_operations_open_a_writable_folder 逐条核对哪些
        # 操作开了可写。松一条不变量就得换一条更细的, 不能直接删掉。
        self.selects.append((folder.strip('"'), readonly))
        self.selected = folder.strip('"')
        if self.selected not in dict(self.folders):
            return "NO", [b"no such mailbox"]
        return "OK", [str(len(self.messages.get(self.selected, []))).encode()]

    def response(self, key):
        if key == "UIDVALIDITY":
            return "OK", [self.uidvalidity]
        return "OK", [None]

    @staticmethod
    def _as_bytes(value):
        """imaplib 的 uid() str/bytes 都收, 夹具也得都收 —— 我们自己的调用点
        两种都有 (列清单拼 bytes, 读单封传 str)。夹具比真库挑剔的话, 测试会
        挂在一个真机上根本不存在的问题上。"""
        return value if isinstance(value, bytes) else str(value).encode()

    def uid(self, command, *args):
        box = self.messages.get(self.selected or "", [])
        if command == "search":
            # HEADER Message-ID <x> —— APPEND 之后靠这个把新 UID 找回来
            # (服务器没有 UIDPLUS 的 APPENDUID)。真按头过滤, 别让测试靠
            # "反正返回全部, 取最后一个"蒙混过去。
            if len(args) >= 3 and str(args[1]).upper() == "MESSAGE-ID":
                needle = str(args[2]).encode()
                return "OK", [
                    b" ".join(uid for uid, _, raw in box if needle in raw)
                ]
            return "OK", [b" ".join(uid for uid, _, _ in box)]
        if command == "fetch":
            wanted = set(self._as_bytes(args[0]).split(b","))
            out = []
            for uid, flags, raw in box:
                if uid not in wanted:
                    continue
                prefix = b"1 (UID " + uid + b" FLAGS (" + flags + b") BODY[HEADER] {%d}" % len(raw)
                out.append((prefix, raw))
                out.append(b")")
            return "OK", out
        if command == "store":
            # imaplib 的 uid() 两种都收 (str 和 bytes), 我们的调用点也是两
            # 种都有 —— 取列表时拼的是 bytes, 写操作传的是 str uid。夹具
            # 要跟真库一样宽容, 不然测试会挂在一个真机上不存在的问题上。
            uid_arg = self._as_bytes(args[0])
            self.stores.append((uid_arg, args[1], args[2]))
            box = self.messages.get(self.selected or "", [])
            wanted = set(uid_arg.split(b","))
            add = args[1].startswith("+")
            flag = args[2].strip("()")
            self.messages[self.selected or ""] = [
                (
                    uid,
                    (
                        (flags + b" " + flag.encode()).strip()
                        if add
                        else flags.replace(flag.encode(), b"").strip()
                    )
                    if uid in wanted
                    else flags,
                    raw,
                )
                for uid, flags, raw in box
            ]
            return "OK", [b"STORE completed"]
        if command == "copy":
            self.copies.append((args[0], args[1].strip('"')))
            return "OK", [b"COPY completed"]
        if command == "expunge":
            self.expunges.append(
                args[0] if isinstance(args[0], str) else args[0].decode()
            )
            return "OK", [b"EXPUNGE completed"]
        return "NO", [b"unsupported"]

    def append(self, folder, flags, date_time, message):
        self.appends.append((folder.strip('"'), flags, message))
        box = self.messages.setdefault(folder.strip('"'), [])
        box.append((str(9000 + len(box)).encode(), flags.strip("()").encode(), message))
        return "OK", [b"APPEND completed"]

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


def test_empty_flags_means_unread(monkeypatch):
    """真机上 FLAGS 是空的 `()` (9/18: 57 封全未读)。空 flags 必须能走通,
    不能因为正则匹配不到就把整封丢掉。"""
    fake = FakeIMAP(messages={"INBOX": [(b"8416", b"", _raw("未读的"))]})
    monkeypatch.setattr(
        "catfish_email.adapters.imap_mail.imaplib.IMAP4_SSL",
        lambda host, port, timeout=None: fake,
    )
    a = ImapAdapter(ImapConfig(host="h", user="u", password="p"))
    msgs = a.list_messages(ListFilter(folder="Inbox", limit=5))
    assert len(msgs) == 1
    assert msgs[0].is_read is False


def test_sequence_number_prefix_is_not_mistaken_for_uid(monkeypatch):
    """前缀开头那个数字是序号不是 UID。真机上 55/56/57 对应 UID 8416/8417/8418。"""
    fake = FakeIMAP(messages={"INBOX": [(b"8416", b"", _raw())]})
    monkeypatch.setattr(
        "catfish_email.adapters.imap_mail.imaplib.IMAP4_SSL",
        lambda host, port, timeout=None: fake,
    )
    a = ImapAdapter(ImapConfig(host="h", user="u", password="p"))
    assert a.list_messages(ListFilter(folder="Inbox", limit=5))[0].id.endswith("|8416")


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


def test_read_paths_never_open_a_writable_folder(adapter):
    """列表 / 读单封 / 搜索一律只读打开。

    9/18 下午补写操作之前, 这条是靠 FakeIMAP.select 里一句 assert 守着的
    (`只读适配器绝不能用可写方式 SELECT`)。现在 adapter 确实要写了, 那句
    assert 只好去掉 —— 但**不是就此不管**: 改成记账, 读路径这条单独钉。

    可写地打开文件夹本身就有副作用: 有些服务器会在可写 SELECT 时把
    \\Recent 清掉。读邮件不该动任何状态。
    """
    adapter.list_messages(ListFilter(folder="*", limit=5))
    msg = adapter.list_messages(ListFilter(folder="Inbox", limit=1))[0]
    adapter.read_message(msg.id)
    adapter.search("清单", folder="*")
    assert adapter._fake.selects, "一次都没 SELECT? 那这条测试什么都没测到"
    assert all(readonly for _, readonly in adapter._fake.selects), (
        f"有读路径用可写方式打开了文件夹: "
        f"{[f for f, ro in adapter._fake.selects if not ro]}"
    )


def test_only_write_operations_open_a_writable_folder(adapter):
    """反过来: 真要写的时候必须是可写打开, 否则服务器会拒绝 STORE。"""
    msg = adapter.list_messages(ListFilter(folder="Inbox", limit=1))[0]
    adapter._fake.selects.clear()
    adapter.mark_read(msg.id)
    assert any(not readonly for _, readonly in adapter._fake.selects)


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
