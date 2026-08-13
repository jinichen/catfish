"""EMLX 只读兜底路径单测 (EmlxFallbackMixin)。

从 test_adapter_apple_mail.py 拆出 (8/13, 源码同步拆成 apple_mail_emlx_path.py)。

# 打桩打哪个模块

`_detect_mail_data_dir` 要打 **emlx_path** 的命名空间, 不是 `am` 的 ——
调用方 (`_enable_emlx_fallback_if_available`) 现在住在 emlx_path 里, 它查名字
是查自己模块的 globals。打 `am` 不报错, 只是空打, 测试会以一种跟本意无关的
方式挂掉。判据是"谁在查这个名字", 不是"这个名字在哪定义"。
"""
from __future__ import annotations

import plistlib
from pathlib import Path
from unittest.mock import patch

import pytest

from catfish_email.adapters import apple_mail as am
from catfish_email.adapters import apple_mail_emlx
from catfish_email.adapters import apple_mail_emlx_path as emlx_path
from catfish_email.adapters.apple_mail import AppleMailAdapter
from catfish_email.adapters.base import (
    ClientNotRunningError,
    DataNotFoundError,
    ListFilter,
    NotSupportedError,
)




def test_emlx_is_read_flag():
    """plist trailer flags bit 0 = read."""
    from catfish_email.adapters.apple_mail import _emlx_is_read

    assert _emlx_is_read({"flags": 1}) is True   # bit 0 set
    assert _emlx_is_read({"flags": 0}) is False  # 没标读
    assert _emlx_is_read({"flags": 17}) is True  # 0b10001 read + flagged
    assert _emlx_is_read({"flags": 2}) is False  # bit 1 (deleted) 不是 read
    assert _emlx_is_read(None) is False
    assert _emlx_is_read({}) is False



def test_read_emlx_raw_parses_header_and_trailer(tmp_path):
    """造一个 .emlx, 验证 byte_count + plist trailer 解析."""
    from catfish_email.adapters.apple_mail import _read_emlx_raw

    rfc822 = b"From: a@x.com\r\nSubject: Test\r\n\r\nHello"
    plist_xml = (
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b'<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        b'"http://www.apple.com/DTDs/PropertyList-1.0.dtd">'
        b'<plist version="1.0"><dict>'
        b"<key>flags</key><integer>1</integer>"
        b"</dict></plist>"
    )
    emlx_content = f"{len(rfc822)}\n".encode() + rfc822 + plist_xml
    p = tmp_path / "1.emlx"
    p.write_bytes(emlx_content)

    raw, plist = _read_emlx_raw(p)
    assert raw == rfc822
    assert plist is not None
    assert plist.get("flags") == 1



def test_parse_emlx_summary(tmp_path):
    """轻量解析 emlx → Message snippet."""
    from catfish_email.adapters.apple_mail import _parse_emlx_summary

    rfc822 = (
        b"From: alice@x.com\r\n"
        b"To: me@x.com\r\n"
        b"Subject: " + "周报草稿".encode("utf-8") + b"\r\n"
        b"Date: Sat, 17 May 2026 13:30:00 +0000\r\n"
        b"\r\n"
        b"body content here"
    )
    p = tmp_path / "1.emlx"
    p.write_bytes(f"{len(rfc822)}\n".encode() + rfc822)

    msg = _parse_emlx_summary(p, account_name="工作", folder="Inbox")
    assert msg.subject == "周报草稿"
    assert "alice@x.com" in msg.sender
    assert msg.account == "工作"
    assert msg.folder == "Inbox"
    assert msg.id.startswith("工作|emlx:")
    assert msg.date.startswith("2026-05-17")



def test_parse_emlx_full_multipart_extracts_both_text_and_html(tmp_path):
    """multipart 邮件: body_text + body_html 都填."""
    from catfish_email.adapters.apple_mail import _parse_emlx_full

    rfc822 = (
        b"From: alice@x.com\r\n"
        b"To: me@x.com\r\n"
        b"Cc: c@x.com\r\n"
        b"Subject: hi\r\n"
        b"Date: Sat, 17 May 2026 13:30:00 +0000\r\n"
        b'Content-Type: multipart/alternative; boundary="B"\r\n'
        b"\r\n"
        b"--B\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n"
        b"\r\n"
        b"plain version\r\n"
        b"--B\r\n"
        b"Content-Type: text/html; charset=utf-8\r\n"
        b"\r\n"
        b"<p>html version</p>\r\n"
        b"--B--\r\n"
    )
    p = tmp_path / "1.emlx"
    p.write_bytes(f"{len(rfc822)}\n".encode() + rfc822)

    msg = _parse_emlx_full(p, account_name="工作")
    assert "plain version" in msg.body_text
    assert "<p>html version</p>" in msg.body_html
    assert "me@x.com" in msg.recipients
    assert "c@x.com" in msg.cc



def test_enable_emlx_fallback_when_mail_dir_exists(tmp_path, monkeypatch):
    """探测到 ~/Library/Mail/V10 存在 → fallback 激活 + supports_drafts 切 False."""
    # 造一个假 V10
    fake_mail = tmp_path / "Library" / "Mail" / "V10"
    fake_mail.mkdir(parents=True)
    monkeypatch.setattr(
        emlx_path, "_detect_mail_data_dir", lambda: fake_mail,
    )
    adapter = AppleMailAdapter()
    assert adapter._enable_emlx_fallback_if_available() is True
    assert adapter._use_emlx_fallback is True
    assert adapter.supports_drafts is False



def test_no_emlx_fallback_when_no_mail_dir(monkeypatch):
    """探测不到 → fallback 不激活, supports_drafts 不变."""
    monkeypatch.setattr(emlx_path, "_detect_mail_data_dir", lambda: None)
    adapter = AppleMailAdapter()
    assert adapter._enable_emlx_fallback_if_available() is False
    assert adapter._use_emlx_fallback is False
    assert adapter.supports_drafts is True



def test_list_accounts_falls_back_to_emlx_on_permission_denied(tmp_path, monkeypatch):
    """AS 抛 ClientNotRunningError 且本机有 V10 → 自动切 EMLX."""
    # 造假 V10 + 1 个账号目录
    fake_mail = tmp_path / "Mail" / "V10"
    fake_mail.mkdir(parents=True)
    account_dir = fake_mail / "IMAP-test@x.com@imap.x.com"
    account_dir.mkdir()

    monkeypatch.setattr(emlx_path, "_detect_mail_data_dir", lambda: fake_mail)

    def fake_run_os(script, **_):
        raise ClientNotRunningError("没权限")

    with (
        patch.object(am, "_is_mail_running", return_value=True),
        patch.object(am, "_run_osascript", side_effect=fake_run_os),
    ):
        accounts = AppleMailAdapter().list_accounts()
    assert len(accounts) == 1
    assert accounts[0].address == "test@x.com"



def test_create_draft_in_emlx_fallback_raises_not_supported(tmp_path, monkeypatch):
    """EMLX fallback 模式只读 → create_draft 抛 NotSupportedError."""
    from catfish_email.adapters.base import NotSupportedError

    fake_mail = tmp_path / "Mail" / "V10"
    fake_mail.mkdir(parents=True)
    monkeypatch.setattr(emlx_path, "_detect_mail_data_dir", lambda: fake_mail)

    adapter = AppleMailAdapter()
    adapter._enable_emlx_fallback_if_available()
    assert adapter.supports_drafts is False
    with pytest.raises(NotSupportedError, match="EMLX fallback"):
        adapter.create_draft(to=["x@x.com"], subject="hi", body="body")



def test_read_message_emlx_path_directly(tmp_path):
    """ID 带 emlx: 前缀直接走 EMLX 文件读, 不调 AS."""
    rfc822 = (
        b"From: x@x.com\r\n"
        b"Subject: hello\r\n"
        b"Date: Sat, 17 May 2026 13:30:00 +0000\r\n"
        b"\r\n"
        b"the body"
    )
    p = tmp_path / "42.emlx"
    p.write_bytes(f"{len(rfc822)}\n".encode() + rfc822)

    adapter = AppleMailAdapter()
    # 用 emlx 前缀 id 直接走 fallback 路径 (不需要 enable_fallback)
    msg = adapter._read_message_emlx(f"工作|emlx:{p}")
    assert msg.subject == "hello"
    assert "the body" in msg.body_text



def test_list_messages_emlx_filters_by_subject(tmp_path, monkeypatch):
    """EMLX list_messages 用 Python 端 subject_contains 后过滤."""
    fake_mail = tmp_path / "Mail" / "V10"
    fake_mail.mkdir(parents=True)
    account_dir = fake_mail / "IMAP-test@x.com@imap.x.com"
    inbox = account_dir / "INBOX.mbox" / "msgdata" / "Messages"
    inbox.mkdir(parents=True)
    # 写 3 封不同 subject
    for i, subj in enumerate(["周报草稿", "EIS 方案", "周报二稿"]):
        rfc822 = (
            f"From: a{i}@x.com\r\nSubject: {subj}\r\n"
            "Date: Sat, 17 May 2026 13:30:00 +0000\r\n\r\nbody"
        ).encode()
        path = inbox / f"{i+1}.emlx"
        path.write_bytes(f"{len(rfc822)}\n".encode() + rfc822)

    monkeypatch.setattr(emlx_path, "_detect_mail_data_dir", lambda: fake_mail)

    adapter = AppleMailAdapter()
    adapter._enable_emlx_fallback_if_available()
    msgs = adapter._list_messages_emlx(
        am.ListFilter(folder="Inbox", subject_contains="周报", limit=10),
    )
    assert len(msgs) == 2
    assert all("周报" in m.subject for m in msgs)



def test_detect_mail_data_dir_returns_latest_version(tmp_path, monkeypatch):
    """V10 / V9 / V8 都存在 → 取 V10 (最高数字)."""
    home = tmp_path / "home"
    mail = home / "Library" / "Mail"
    for v in ("V8", "V9", "V10"):
        (mail / v).mkdir(parents=True)
    monkeypatch.setattr("pathlib.Path.home", classmethod(lambda cls: home))

    result = am._detect_mail_data_dir()
    assert result is not None
    assert result.name == "V10"



def test_detect_mail_data_dir_returns_none_when_no_mail_app(tmp_path, monkeypatch):
    """没装 Mail / 没同步过 → None, fallback 不激活."""
    home = tmp_path / "empty_home"
    home.mkdir()
    monkeypatch.setattr("pathlib.Path.home", classmethod(lambda cls: home))
    assert am._detect_mail_data_dir() is None



# ============================================================
# 8/13 拆分后的结构不变式
# ============================================================


def test_emlx_mixin_contract_is_satisfied_by_host():
    """EmlxFallbackMixin 对宿主类的隐式契约必须成立。

    mixin 的方法体里直接写 `self._emlx_mail_dir` 这类属性, 但 mixin 自己不定义
    它们 —— 契约靠文档维系。宿主类哪天把 `_emlx_mail_dir` 改名, mixin 里所有
    读它的地方会在**运行时**才炸, 而 EMLX 兜底只在"员工不给 Automation 权限"
    时才走到, 平时测不到、也不会有人发现。

    这条把契约变成可执行的。
    """
    adapter = am.AppleMailAdapter()
    for attr in ("_emlx_mail_dir", "_use_emlx_fallback", "supports_drafts"):
        assert hasattr(adapter, attr), (
            f"EmlxFallbackMixin 依赖 self.{attr}, 宿主类没提供 —— "
            "见 apple_mail_emlx_path.py 文件头的契约表"
        )
    assert callable(getattr(adapter, "_unpack_id", None)), (
        "_export_attachment_emlx 用 self._unpack_id() 拆 id"
    )



def test_emlx_methods_come_from_the_mixin_not_the_adapter():
    """兜底方法必须来自 mixin —— 有人手滑在 AppleMailAdapter 里又写一份就红。

    两份实现同名时 MRO 让宿主类的那份赢, mixin 那份变成死代码, 而测试照样绿
    (调用方拿到的是能跑的那份)。这条盯的就是这种静默的重复。
    """
    from catfish_email.adapters.apple_mail_emlx_path import EmlxFallbackMixin
    own = set(vars(am.AppleMailAdapter))
    for name in vars(EmlxFallbackMixin):
        if name.startswith("__"):
            continue
        assert name not in own, (
            f"{name} 在 AppleMailAdapter 里也定义了一份, 会盖住 mixin 的实现"
        )

