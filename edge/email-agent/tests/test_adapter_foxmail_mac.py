"""FoxmailMacAdapter 单测 (新版 SQLite + .mail 后端)。

不依赖真 Foxmail 安装, 用 conftest.py 的 make_foxmail_profile fixture
合成完整的 Profiles 目录 (sqlite + .mail 文件)。
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from catfish_email.adapters.base import (
    DataNotFoundError,
    ListFilter,
    NotSupportedError,
)
from catfish_email.adapters.foxmail_mac import FoxmailMacAdapter


# ============================================================
# 初始化 / 探测
# ============================================================


def test_no_profiles_dir_raises(tmp_path: Path):
    with pytest.raises(DataNotFoundError):
        FoxmailMacAdapter(profiles_dir=tmp_path / "does_not_exist")


def test_empty_profiles_no_accounts(tmp_path: Path):
    empty = tmp_path / "Profiles"
    empty.mkdir()
    a = FoxmailMacAdapter(profiles_dir=empty)
    with pytest.raises(DataNotFoundError):
        a.list_accounts()


def test_list_accounts_basic(make_foxmail_profile):
    profiles, account = make_foxmail_profile(account="hongbo@x.com", mails=[
        {"subject": "x", "body": "y"},
    ])
    a = FoxmailMacAdapter(profiles_dir=profiles)
    accs = a.list_accounts()
    assert len(accs) == 1
    assert accs[0].address == "hongbo@x.com"
    assert accs[0].is_default is True


def test_list_accounts_skips_dir_without_db(tmp_path: Path):
    """有目录但没 messages.db → 跳过 (员工没真用过)"""
    profiles = tmp_path / "Profiles"
    (profiles / "real@x.com").mkdir(parents=True)
    # 用一个真实 db 文件让它认
    import sqlite3
    sqlite3.connect(profiles / "real@x.com" / "messages.db").close()
    (profiles / "fake@x.com").mkdir()  # 没 messages.db
    a = FoxmailMacAdapter(profiles_dir=profiles)
    accs = a.list_accounts()
    assert [acc.address for acc in accs] == ["real@x.com"]


def test_list_accounts_multiple_default_first(tmp_path: Path):
    """多账号: 第一个 is_default=True"""
    import sqlite3
    profiles = tmp_path / "Profiles"
    for addr in ["a@x.com", "b@x.com"]:
        d = profiles / addr
        d.mkdir(parents=True)
        sqlite3.connect(d / "messages.db").close()
    a = FoxmailMacAdapter(profiles_dir=profiles)
    accs = a.list_accounts()
    assert [acc.is_default for acc in accs] == [True, False]


# ============================================================
# list_messages: 基本路径
# ============================================================


def test_list_messages_basic(make_foxmail_profile):
    profiles, _ = make_foxmail_profile(mails=[
        {"subject": "Hello", "body": "world", "date_unix": 1700000000, "folder_id": 1},
        {"subject": "Re: report", "body": "see attached", "date_unix": 1700001000, "folder_id": 1},
    ])
    a = FoxmailMacAdapter(profiles_dir=profiles)
    msgs = a.list_messages(ListFilter(folder="Inbox"))
    assert len(msgs) == 2
    subjects = sorted(m.subject for m in msgs)
    assert subjects == ["Hello", "Re: report"]


def test_list_messages_returns_snippet(make_foxmail_profile):
    """list 场景应该用 abstract 不读 .mail 文件"""
    profiles, _ = make_foxmail_profile(mails=[
        {"subject": "L", "body": "x" * 5000, "abstract": "x" * 200, "folder_id": 1},
    ])
    a = FoxmailMacAdapter(profiles_dir=profiles)
    msgs = a.list_messages(ListFilter(folder="Inbox"))
    assert len(msgs[0].body_text) == 200
    assert msgs[0].body_html == ""


def test_list_messages_unread_filter(make_foxmail_profile):
    profiles, _ = make_foxmail_profile(mails=[
        {"subject": "已读", "is_read": True, "folder_id": 1},
        {"subject": "未读 1", "is_read": False, "folder_id": 1},
        {"subject": "未读 2", "is_read": False, "folder_id": 1},
    ])
    a = FoxmailMacAdapter(profiles_dir=profiles)
    msgs = a.list_messages(ListFilter(folder="Inbox", unread_only=True))
    assert len(msgs) == 2
    assert all(not m.is_read for m in msgs)


def test_list_messages_sender_filter(make_foxmail_profile):
    profiles, _ = make_foxmail_profile(mails=[
        {"subject": "x", "sender": "alice@x.com", "from_": "alice@x.com", "folder_id": 1},
        {"subject": "y", "sender": "bob@x.com", "from_": "bob@x.com", "folder_id": 1},
    ])
    a = FoxmailMacAdapter(profiles_dir=profiles)
    msgs = a.list_messages(ListFilter(folder="Inbox", sender_contains="alice"))
    assert len(msgs) == 1
    assert "alice" in msgs[0].sender.lower()


def test_list_messages_subject_filter(make_foxmail_profile):
    profiles, _ = make_foxmail_profile(mails=[
        {"subject": "周报 4-26", "folder_id": 1},
        {"subject": "月报", "folder_id": 1},
    ])
    a = FoxmailMacAdapter(profiles_dir=profiles)
    msgs = a.list_messages(ListFilter(folder="Inbox", subject_contains="周报"))
    assert len(msgs) == 1


def test_list_messages_has_attachments_filter(make_foxmail_profile):
    profiles, _ = make_foxmail_profile(mails=[
        {"subject": "with att", "folder_id": 1, "attachments": [{"filename": "x.pdf", "filesize": 100}]},
        {"subject": "no att", "folder_id": 1},
    ])
    a = FoxmailMacAdapter(profiles_dir=profiles)
    with_att = a.list_messages(ListFilter(folder="Inbox", has_attachments=True))
    assert len(with_att) == 1
    assert with_att[0].subject == "with att"


def test_list_messages_limit(make_foxmail_profile):
    profiles, _ = make_foxmail_profile(mails=[
        {"subject": f"m{i}", "folder_id": 1, "date_unix": 1700000000 + i}
        for i in range(20)
    ])
    a = FoxmailMacAdapter(profiles_dir=profiles)
    msgs = a.list_messages(ListFilter(folder="Inbox", limit=5))
    assert len(msgs) == 5


def test_list_messages_sorted_desc_by_date(make_foxmail_profile):
    profiles, _ = make_foxmail_profile(mails=[
        {"subject": "old",    "date_unix": 1700000000, "folder_id": 1},
        {"subject": "newest", "date_unix": 1700100000, "folder_id": 1},
        {"subject": "middle", "date_unix": 1700050000, "folder_id": 1},
    ])
    a = FoxmailMacAdapter(profiles_dir=profiles)
    msgs = a.list_messages(ListFilter(folder="Inbox"))
    assert msgs[0].subject == "newest"
    assert msgs[2].subject == "old"


def test_list_messages_since_until_filter(make_foxmail_profile):
    """ISO date 转 unix 在 DB 端筛"""
    profiles, _ = make_foxmail_profile(mails=[
        # 1700000000 = 2023-11-14 22:13:20 UTC
        # 1700100000 = 2023-11-16 02:00:00 UTC
        # 1700200000 = 2023-11-17 05:46:40 UTC
        {"subject": "before", "date_unix": 1700000000, "folder_id": 1},
        {"subject": "in",     "date_unix": 1700100000, "folder_id": 1},
        {"subject": "after",  "date_unix": 1700200000, "folder_id": 1},
    ])
    a = FoxmailMacAdapter(profiles_dir=profiles)
    msgs = a.list_messages(ListFilter(folder="Inbox", since="2023-11-15", until="2023-11-17"))
    subjects = {m.subject for m in msgs}
    assert subjects == {"in"}


def test_list_messages_unknown_folder_returns_empty(make_foxmail_profile):
    """folder 名找不到 → 空 list (不抛)"""
    profiles, _ = make_foxmail_profile(mails=[{"subject": "x", "folder_id": 1}])
    a = FoxmailMacAdapter(profiles_dir=profiles)
    msgs = a.list_messages(ListFilter(folder="NeverExisted"))
    assert msgs == []


def test_list_messages_chinese_alias(make_foxmail_profile):
    """folder='Inbox' 和 '收件箱' 都该 work"""
    profiles, _ = make_foxmail_profile(mails=[{"subject": "x", "folder_id": 1}])
    a = FoxmailMacAdapter(profiles_dir=profiles)
    en = a.list_messages(ListFilter(folder="Inbox"))
    cn = a.list_messages(ListFilter(folder="收件箱"))
    assert len(en) == 1 == len(cn)


# ============================================================
# read_message
# ============================================================


def test_read_message_full_body(make_foxmail_profile):
    """read 场景应该读 .mail 文件拿完整 body"""
    long_body = "字" * 1000
    profiles, _ = make_foxmail_profile(mails=[
        {"subject": "S", "body": long_body, "abstract": "字" * 200, "folder_id": 1, "mailid": 12345},
    ])
    a = FoxmailMacAdapter(profiles_dir=profiles)
    listed = a.list_messages(ListFilter(folder="Inbox"))
    full = a.read_message(listed[0].id)
    # 完整 body 比 abstract 长
    assert len(full.body_text) > 200
    assert long_body[:50] in full.body_text


def test_read_message_html(make_foxmail_profile):
    profiles, _ = make_foxmail_profile(mails=[
        {"subject": "html", "body": "<p>Hello <b>world</b></p>", "is_html": True, "folder_id": 1},
    ])
    a = FoxmailMacAdapter(profiles_dir=profiles)
    listed = a.list_messages(ListFilter(folder="Inbox"))
    full = a.read_message(listed[0].id)
    # body_html 应该有 HTML 原文
    assert "<p>" in full.body_html
    # body_text 是 strip 后的纯文本
    assert "Hello" in full.body_text
    assert "world" in full.body_text
    assert "<p>" not in full.body_text


def test_read_message_with_attachments(make_foxmail_profile):
    profiles, _ = make_foxmail_profile(mails=[
        {
            "subject": "with att", "body": "see file", "folder_id": 1,
            "attachments": [
                {"displayname": "report.pdf", "filename": "report.pdf",
                 "filesize": 1024, "filecontenttype": "application/pdf"},
                {"displayname": "data.xlsx", "filename": "data.xlsx",
                 "filesize": 2048, "filecontenttype": "application/vnd.ms-excel"},
            ],
        },
    ])
    a = FoxmailMacAdapter(profiles_dir=profiles)
    listed = a.list_messages(ListFilter(folder="Inbox"))
    full = a.read_message(listed[0].id)
    assert full.has_attachments is True
    assert len(full.attachments) == 2
    assert full.attachments[0].filename == "report.pdf"
    assert full.attachments[0].size_bytes == 1024
    assert "pdf" in full.attachments[0].content_type.lower()


def test_read_message_invalid_id_raises(make_foxmail_profile):
    profiles, _ = make_foxmail_profile(mails=[{"subject": "x", "folder_id": 1}])
    a = FoxmailMacAdapter(profiles_dir=profiles)
    with pytest.raises(DataNotFoundError):
        a.read_message("foxmail-mac|wrong@x.com|999999")


def test_read_message_malformed_id_raises(make_foxmail_profile):
    profiles, _ = make_foxmail_profile(mails=[{"subject": "x", "folder_id": 1}])
    a = FoxmailMacAdapter(profiles_dir=profiles)
    with pytest.raises(DataNotFoundError):
        a.read_message("not-a-valid-id")
    with pytest.raises(DataNotFoundError):
        a.read_message("foxmail-mac|x@y.com|not-a-number")


def test_read_message_falls_back_to_abstract_when_mail_file_missing(make_foxmail_profile, tmp_path: Path):
    """.mail 文件被 Foxmail 自己清掉了, fallback 用 DB abstract"""
    profiles, _ = make_foxmail_profile(mails=[
        {"subject": "S", "body": "body content", "abstract": "fallback abstract", "folder_id": 1},
    ])
    a = FoxmailMacAdapter(profiles_dir=profiles)
    listed = a.list_messages(ListFilter(folder="Inbox"))
    msg_id = listed[0].id

    # 干掉 .mail 文件
    import shutil
    mail_root = profiles / "hongbo@example.com" / "Mail"
    shutil.rmtree(mail_root)

    full = a.read_message(msg_id)
    # body 应该退回 abstract
    assert "fallback abstract" in full.body_text


# ============================================================
# search (FTS3)
# ============================================================


def test_search_by_subject(make_foxmail_profile):
    profiles, _ = make_foxmail_profile(mails=[
        {"subject": "report Q1", "body": "x", "folder_id": 1},
        {"subject": "report Q2", "body": "y", "folder_id": 1},
        {"subject": "vacation",  "body": "z", "folder_id": 1},
    ])
    a = FoxmailMacAdapter(profiles_dir=profiles)
    hits = a.search("report")
    assert len(hits) == 2


def test_search_by_body(make_foxmail_profile):
    profiles, _ = make_foxmail_profile(mails=[
        {"subject": "S1", "body": "discussing compliance project", "folder_id": 1},
        {"subject": "S2", "body": "vacation request", "folder_id": 1},
    ])
    a = FoxmailMacAdapter(profiles_dir=profiles)
    hits = a.search("compliance")
    assert len(hits) == 1
    assert hits[0].subject == "S1"


def test_search_empty_returns_empty(make_foxmail_profile):
    profiles, _ = make_foxmail_profile(mails=[{"subject": "x", "folder_id": 1}])
    a = FoxmailMacAdapter(profiles_dir=profiles)
    assert a.search("") == []
    assert a.search("   ") == []


def test_search_no_match_returns_empty(make_foxmail_profile):
    profiles, _ = make_foxmail_profile(mails=[
        {"subject": "Hello", "body": "world", "folder_id": 1},
    ])
    a = FoxmailMacAdapter(profiles_dir=profiles)
    assert a.search("nonexistent_term_xyzzy") == []


def test_search_limit(make_foxmail_profile):
    profiles, _ = make_foxmail_profile(mails=[
        {"subject": f"hit topic {i}", "body": "common", "folder_id": 1, "date_unix": 1700000000 + i}
        for i in range(50)
    ])
    a = FoxmailMacAdapter(profiles_dir=profiles)
    hits = a.search("common", limit=10)
    assert len(hits) == 10


def test_search_falls_back_to_like_when_fts_broken(make_foxmail_profile, tmp_path):
    """实测真机 Foxmail 的 mail_fts 用 ICU tokenizer, Python 内置 sqlite3
    不带 ICU 会抛 unknown tokenizer 错。adapter 应自动退化到 LIKE 搜索。"""
    import sqlite3
    profiles, _ = make_foxmail_profile(mails=[
        {"subject": "Superlinear newsletter Q1", "body": "x", "folder_id": 1, "mailid": 100},
        {"subject": "vacation", "body": "y", "folder_id": 1, "mailid": 101},
    ])

    # 把 mail_fts 表改成 ICU tokenizer (模拟真机环境)
    db = profiles / "hongbo@example.com" / "messages.db"
    conn = sqlite3.connect(db)
    conn.execute("DROP TABLE mail_fts")
    try:
        conn.execute(
            "CREATE VIRTUAL TABLE mail_fts USING fts3("
            "TOKENIZE icu,`subject`,`from`,`to`,`text`)"
        )
        conn.commit()
        conn.close()
    except sqlite3.OperationalError:
        # 在 sandbox / 系统 sqlite 没 icu, CREATE 时就会挂; 不影响测试目的
        # (我们要的就是模拟这个挂)
        pass

    a = FoxmailMacAdapter(profiles_dir=profiles)
    # 不应该抛, LIKE 搜 subject 应该命中 "Superlinear"
    hits = a.search("Superlinear")
    assert len(hits) >= 1
    assert any("Superlinear" in h.subject for h in hits)


# ============================================================
# create_draft 红线
# ============================================================


def test_create_draft_raises_not_supported(make_foxmail_profile):
    profiles, _ = make_foxmail_profile(mails=[{"subject": "x", "folder_id": 1}])
    a = FoxmailMacAdapter(profiles_dir=profiles)
    with pytest.raises(NotSupportedError):
        a.create_draft(to=["x@y.com"], subject="r", body="hi")


def test_supports_drafts_flag_is_false():
    assert FoxmailMacAdapter.supports_drafts is False


# ============================================================
# 基本 metadata
# ============================================================


def test_adapter_name(make_foxmail_profile):
    profiles, _ = make_foxmail_profile(mails=[{"subject": "x", "folder_id": 1}])
    a = FoxmailMacAdapter(profiles_dir=profiles)
    assert a.name == "foxmail_mac"


def test_message_id_round_trip(make_foxmail_profile):
    """list 给的 id 能被 read_message 用"""
    profiles, _ = make_foxmail_profile(mails=[
        {"subject": "msg 1", "folder_id": 1, "mailid": 100},
        {"subject": "msg 2", "folder_id": 1, "mailid": 200},
    ])
    a = FoxmailMacAdapter(profiles_dir=profiles)
    listed = a.list_messages(ListFilter(folder="Inbox"))
    for lm in listed:
        full = a.read_message(lm.id)
        assert full.id == lm.id
        assert full.subject == lm.subject
