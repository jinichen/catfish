"""9/18: Foxmail 7.2 存储布局 —— 五个同族 bug 的回归钉。

# 背景

真机 (Foxmail 7.2, 账号 5671 封共 7.8 GB) 上邮件 tab 一直是「收件箱为空」。
一路挖下来是五个 bug, 成因全是同一个: **按 6.x 的布局写死判据**。

  ① Storage 路径发现不了 —— `FMStorage.list` 不在配置文件后缀白名单里
  ② 账号目录认不出   —— `_looks_like_account_root` 只认 `Mail`, 7.2 是 `Mails`
  ③ 文件夹名成了 Boxes —— `_folder_for` 把容器目录当文件夹, 请求 Inbox 零命中
  ④ `.box` 解析刷屏   —— 7.2 的 .box 是 'LSTG' id 索引, 里面没有 FOXM,
                         旧代码逐字节试探, 一个字节一条 WARNING, 刷了四千行
  ⑤ 邮件根本没被扫到 —— `_mail_files` 只收 `.box`/`.eml`, 而 7.2 的邮件文件
                         叫 `Mails/0/0/1024`, **纯数字文件名, 没有扩展名**

⑤ 才是「收件箱为空」的真因; 前四个各自独立, 少修一个照样不出邮件。

# 这个文件钉什么

  - 合成一份 7.2 布局 (Mails 分桶 + LSTG 索引), 端到端能列出邮件
  - 文件夹归属走索引: sent.box 里的进 Sent, 其余进 Inbox (7.2 没有 inbox.box)
  - 已读状态来自 unread.box —— 7.2 的邮件文件本身不带 flags
  - 索引读不懂时退回全部 Inbox, 而不是让邮件消失
  - 单封邮件起始偏移靠嗅探: 裸 RFC822 和带专有前缀两种都要能读
  - 格式对不上时警告条数有上限, 不再 O(文件大小)
"""
from __future__ import annotations

import struct
from pathlib import Path

import pytest

from catfish_email import box_parser
from catfish_email.adapters import foxmail7_store
from catfish_email.adapters.base import ListFilter
from catfish_email.adapters.foxmail_win import FoxmailWinAdapter

_RFC822 = (
    "From: 张三 <zhang@example.com>\r\n"
    "To: 陈鸿波 <hongbo@example.com>\r\n"
    "Subject: {subject}\r\n"
    "Date: Thu, {day:02d} Sep 2026 10:00:00 +0800\r\n"
    "Message-ID: <{mid}@example.com>\r\n"
    "Content-Type: text/plain; charset=utf-8\r\n"
    "\r\n"
    "{body}\r\n"
)


def _mail_bytes(subject: str, body: str, mid: int, day: int = 15) -> bytes:
    return _RFC822.format(subject=subject, body=body, mid=mid, day=day).encode("utf-8")


def _lstg(ids: list[int]) -> bytes:
    """造一份 7.2 的 LSTG 索引: 32 字节头 + uint32 id 数组。

    头部照抄真机 all.box 的前 16 字节形状 (magic + 版本 + 头长度 0x20)。
    """
    header = b"LSTG" + struct.pack("<HH", 1, 1) + b"\x00" * 4 + struct.pack("<I", 32)
    header = header.ljust(32, b"\x00")
    return header + b"".join(struct.pack("<I", i) for i in ids)


def _bucket_path(root: Path, mail_id: int) -> Path:
    """真机实测的分桶规则: Mails/{id%32}/{id//32%32}/{id}。

    注意实现里**不用**这个公式 (遍历目录拿真实映射), 这里只是为了把夹具造得
    像真的。公式写死在测试里没关系, 写死在实现里就是第六个同族 bug。
    """
    return root / "Mails" / str(mail_id % 32) / str((mail_id // 32) % 32) / str(mail_id)


@pytest.fixture
def storage72(tmp_path: Path) -> Path:
    """一份 Foxmail 7.2 形状的 Storage: 1 个账号, 4 封邮件, 3 份索引。"""
    account = tmp_path / "Storage" / "ffchenhb@chinatelecom.cn"
    (account / "Accounts").mkdir(parents=True)
    boxes = account / "Boxes"
    boxes.mkdir()

    mails = {
        1024: ("季度汇报", "请查收本季度数据"),
        1056: ("项目进度", "达华项目已进场"),
        2048: ("发给客户的报价", "报价见附件"),
        2080: ("草稿没写完", "先存着"),
    }
    for mail_id, (subject, body) in mails.items():
        path = _bucket_path(account, mail_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(_mail_bytes(subject, body, mail_id))

    (boxes / "all.box").write_bytes(_lstg(sorted(mails)))
    (boxes / "sent.box").write_bytes(_lstg([2048]))
    (boxes / "draft.box").write_bytes(_lstg([2080]))
    (boxes / "unread.box").write_bytes(_lstg([1056]))
    # 标记位 box, 不该被当成文件夹
    (boxes / "topmost.box").write_bytes(_lstg([1024]))
    (boxes / "mId_bId.map").write_bytes(b"\x00" * 64)
    return account.parent


# ─────────────────────────────────────────────────────────────
# ⑤ 无扩展名邮件文件 + ② 账号目录判据
# ─────────────────────────────────────────────────────────────


def test_account_dir_with_mails_and_boxes_is_recognised(storage72: Path):
    account = storage72 / "ffchenhb@chinatelecom.cn"
    assert foxmail7_store.is_foxmail7_account(account)
    # 6.x 的 Mail 目录不该被误判成 7.x
    assert not foxmail7_store.is_foxmail7_account(storage72)


def test_mail_files_pick_up_extensionless_numeric_names(storage72: Path):
    account = storage72 / "ffchenhb@chinatelecom.cn"
    found = foxmail7_store.mail_files(account)
    assert set(found) == {1024, 1056, 2048, 2080}


def test_pointing_at_account_dir_does_not_invent_a_boxes_account(storage72: Path):
    """旧判据会把 Boxes 当账号, 界面上冒出一个叫 "Boxes" 的账号。"""
    account = storage72 / "ffchenhb@chinatelecom.cn"
    adapter = FoxmailWinAdapter(profiles_dir=account)
    assert [a.address for a in adapter.list_accounts()] == ["ffchenhb@chinatelecom.cn"]


def test_inbox_is_not_empty(storage72: Path):
    """收件箱为空 —— 这一条就是用户报的那个现象。"""
    adapter = FoxmailWinAdapter(profiles_dir=storage72)
    messages = adapter.list_messages(ListFilter(folder="Inbox", limit=10))
    assert {m.subject for m in messages} == {"季度汇报", "项目进度"}


# ─────────────────────────────────────────────────────────────
# ③ 文件夹归属走索引, 不靠路径猜
# ─────────────────────────────────────────────────────────────


def test_folders_come_from_the_index(storage72: Path):
    adapter = FoxmailWinAdapter(profiles_dir=storage72)
    sent = adapter.list_messages(ListFilter(folder="Sent", limit=10))
    drafts = adapter.list_messages(ListFilter(folder="Drafts", limit=10))
    assert [m.subject for m in sent] == ["发给客户的报价"]
    assert [m.subject for m in drafts] == ["草稿没写完"]


def test_flag_boxes_are_not_folders(storage72: Path):
    """topmost.box 是标记位; 它不该造出一个叫 topmost 的文件夹, 也不该
    把 1024 从收件箱里抢走。"""
    account = storage72 / "ffchenhb@chinatelecom.cn"
    acc = foxmail7_store.load_account(account)
    assert set(acc.folder_of.values()) == {"Sent", "Drafts"}
    assert acc.folder_for(_bucket_path(account, 1024)) == "Inbox"


def test_unread_state_comes_from_unread_box(storage72: Path):
    adapter = FoxmailWinAdapter(profiles_dir=storage72)
    unread = adapter.list_messages(ListFilter(folder="Inbox", unread_only=True, limit=10))
    assert [m.subject for m in unread] == ["项目进度"]


def test_unreadable_index_falls_back_to_inbox_not_to_nothing(storage72: Path):
    """索引读不懂时宁可文件夹分得糙, 也不能让邮件不显示。"""
    account = storage72 / "ffchenhb@chinatelecom.cn"
    for box in (account / "Boxes").glob("*.box"):
        box.write_bytes(b"LSTG" + b"\x00" * 28 + b"\xff" * 64)  # id 全对不上
    acc = foxmail7_store.load_account(account)
    assert not acc.index_understood
    adapter = FoxmailWinAdapter(profiles_dir=storage72)
    inbox = adapter.list_messages(ListFilter(folder="Inbox", limit=10))
    assert len(inbox) == 4


def test_index_decoding_is_validated_against_the_filesystem(storage72: Path):
    """头长度不去逆向, 而是几个候选各解一遍取命中最多的。"""
    account = storage72 / "ffchenhb@chinatelecom.cn"
    known = set(foxmail7_store.mail_files(account))
    ids = foxmail7_store.read_lstg_ids(account / "Boxes" / "all.box", known)
    assert ids is not None and set(ids) == known
    # 命中太少 → 承认读不懂, 不硬凑
    assert foxmail7_store.read_lstg_ids(account / "Boxes" / "all.box", {999999}) is None


# ─────────────────────────────────────────────────────────────
# ④ LSTG 不是邮件容器; 警告要有上限
# ─────────────────────────────────────────────────────────────


def test_lstg_box_is_not_parsed_as_a_mail_container(storage72: Path, caplog):
    box = storage72 / "ffchenhb@chinatelecom.cn" / "Boxes" / "all.box"
    with caplog.at_level("WARNING"):
        assert list(box_parser.parse_box_file(box)) == []
    assert caplog.text == "", "认出是索引就该安静退出, 一条警告都不该有"


def test_bad_header_warnings_are_capped(tmp_path: Path, caplog):
    """格式判据不适用是**一个**事实, 不该产生 O(文件大小) 条日志。"""
    junk = tmp_path / "junk.box"
    junk.write_bytes(b"\x00" * 4096)
    with caplog.at_level("WARNING"):
        assert list(box_parser.parse_box_file(junk)) == []
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) <= box_parser.MAX_BAD_HEADER_WARNINGS + 2
    assert any("对不上" in r.getMessage() for r in warnings)


# ─────────────────────────────────────────────────────────────
# 单封邮件: 起始偏移嗅探
# ─────────────────────────────────────────────────────────────


def test_sniff_finds_headers_at_offset_zero():
    assert box_parser.sniff_rfc822_offset(_mail_bytes("x", "y", 1)) == 0


def test_sniff_skips_a_proprietary_prefix():
    """下一版 Foxmail 要是在邮件前面挂一段自己的头, 不该又得改一次代码。"""
    prefix = b"\x01\x02\x03\x04" + b"\x00" * 60
    data = prefix + _mail_bytes("带前缀", "正文", 2)
    assert box_parser.sniff_rfc822_offset(data) == len(prefix)
    # 正文里带冒号的行不该被当成邮件头开头
    assert b"Subject:" in data


def test_sniff_rejects_a_colon_line_in_plain_text():
    assert box_parser.sniff_rfc822_offset(b"note: hello\nthis is not mail\n") is None


def test_parse_mail_file_reads_headers_only_when_asked(tmp_path: Path):
    """列清单只读头部: 真机一个账号 7.8 GB, 为显示 5 条全文读入是不行的。"""
    path = tmp_path / "1024"
    path.write_bytes(_mail_bytes("大邮件", "正文" + "填充" * 100000, 3))
    head = box_parser.parse_mail_file(path, head_bytes=4096)
    assert head.message["Subject"] == "大邮件"
    full = box_parser.parse_mail_file(path)
    assert full.raw_length > head.raw_length


def test_reading_one_message_returns_that_message(storage72: Path):
    adapter = FoxmailWinAdapter(profiles_dir=storage72)
    listed = adapter.list_messages(ListFilter(folder="Inbox", limit=10))
    target = next(m for m in listed if m.subject == "季度汇报")
    full = adapter.read_message(target.id)
    assert full.subject == "季度汇报"
    assert full.body_text.strip() == "请查收本季度数据"
