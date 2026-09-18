"""通用 .eml 目录适配器。

# 为什么有它 (9/18)

原先是一整套 Foxmail Windows 私有存储解析。一天踩了六个 bug, 前五个都是
"把某一版的私有布局当判据写死", 第六个把路堵死了: **Foxmail 7.2 把邮件文件
加密了** (实测五个样本 16 KB–262 MB, 熵 7.96–7.97, 彼此无共同前缀)。
没有正文, 邮件进知识库这件事就没价值, 于是整条线删掉, 改读导出的 .eml。

# 这个文件钉什么

  ① 三种目录形态都要认: 平铺 / 分账号 / 分账号再分文件夹
  ② 文件夹名归一 (收件箱=Inbox), 认不出的保留原名
  ③ =?GB2312?B?= 主题解成中文
  ④ 起始偏移靠嗅探 —— 带专有前缀的导出文件也要能读
  ⑤ 列清单只读头部, 不整份读 (导出目录可能几个 GB)
  ⑥ 坏文件跳过, 不能让一封坏的把整个收件箱搞空
  ⑦ 只读: 写操作一律 NotSupportedError
  ⑧ id 稳定且不越界

夹具用**真实 RFC822**造, 不用被测模块自己的常量 —— 老 conftest 留过一句
"这些测试不证明能读真文件", 后来应验了。.eml 是公开标准, 这里造的就是真格式。
"""
from __future__ import annotations

import base64
from pathlib import Path

import pytest

from catfish_email.adapters.base import DataNotFoundError, ListFilter, NotSupportedError
from catfish_email.adapters.eml_dir import EmlDirAdapter, sniff_rfc822_offset

#: "在建项目清单" 的 GB2312 base64 —— 真机上企业邮件主题就是这种形态
_SUBJ_CN = "=?GB2312?B?1Nq9qM/uxL/H5bWl?="


def _eml(
    subject: str = "测试主题",
    sender: str = '"张三" <zhang@example.com>',
    to: str = '"陈鸿波" <hongbo@example.com>',
    body: str = "你好, 这是正文。",
    day: int = 15,
    mid: str = "m1",
    attachment: tuple[str, bytes] | None = None,
) -> bytes:
    if attachment is None:
        return (
            f"Received: from mx.example.com by mail.example.com; "
            f"Mon, {day} Sep 2026 10:00:00 +0800\r\n"
            f"Date: Mon, {day} Sep 2026 10:00:00 +0800\r\n"
            f"From: {sender}\r\nTo: {to}\r\nSubject: {subject}\r\n"
            f"Message-ID: <{mid}@example.com>\r\nMIME-Version: 1.0\r\n"
            f'Content-Type: text/plain; charset="utf-8"\r\n'
            f"Content-Transfer-Encoding: 8bit\r\n\r\n{body}\r\n"
        ).encode("utf-8")
    name, blob = attachment
    return (
        f"Date: Mon, {day} Sep 2026 10:00:00 +0800\r\n"
        f"From: {sender}\r\nTo: {to}\r\nSubject: {subject}\r\n"
        f"Message-ID: <{mid}@example.com>\r\nMIME-Version: 1.0\r\n"
        f'Content-Type: multipart/mixed; boundary="BND"\r\n\r\n'
        f"--BND\r\nContent-Type: text/plain; charset=\"utf-8\"\r\n\r\n{body}\r\n"
        f"--BND\r\nContent-Type: application/octet-stream\r\n"
        f'Content-Disposition: attachment; filename="{name}"\r\n'
        f"Content-Transfer-Encoding: base64\r\n\r\n"
        f"{base64.b64encode(blob).decode()}\r\n--BND--\r\n"
    ).encode("utf-8")


@pytest.fixture
def flat_dir(tmp_path: Path) -> Path:
    """① 形态一: 导出到一个目录, 平铺一堆 .eml。"""
    root = tmp_path / "邮件导出"
    root.mkdir()
    (root / "a.eml").write_bytes(_eml(_SUBJ_CN, day=15, mid="a"))
    (root / "b.eml").write_bytes(_eml("第二封", day=16, mid="b"))
    return root


@pytest.fixture
def account_dir(tmp_path: Path) -> Path:
    """② ③ 形态二/三: 分账号, 其中一个再分文件夹。"""
    root = tmp_path / "导出"
    inbox = root / "hongbo@example.com" / "收件箱"
    sent = root / "hongbo@example.com" / "已发送"
    inbox.mkdir(parents=True)
    sent.mkdir(parents=True)
    (inbox / "1.eml").write_bytes(_eml("收到的", day=10, mid="i1"))
    (inbox / "2.eml").write_bytes(
        _eml("带附件的", day=11, mid="i2", attachment=("报价.pdf", b"%PDF-1.4 fake"))
    )
    (sent / "1.eml").write_bytes(
        _eml("发出的", sender='"陈鸿波" <hongbo@example.com>', day=12, mid="s1")
    )
    other = root / "work@example.com"
    other.mkdir()
    (other / "x.eml").write_bytes(_eml("另一个账号", day=13, mid="o1"))
    return root


# ─────────────────────────────────────────────────────────────
# ① 三种目录形态
# ─────────────────────────────────────────────────────────────


def test_flat_dir_is_one_account(flat_dir: Path):
    adapter = EmlDirAdapter(profiles_dir=flat_dir)
    assert [a.address for a in adapter.list_accounts()] == ["邮件导出"]
    messages = adapter.list_messages(ListFilter(folder="Inbox", limit=10))
    assert {m.subject for m in messages} == {"在建项目清单", "第二封"}


def test_subdirs_become_accounts(account_dir: Path):
    adapter = EmlDirAdapter(profiles_dir=account_dir)
    assert [a.address for a in adapter.list_accounts()] == [
        "hongbo@example.com", "work@example.com",
    ]


def test_messages_sort_newest_first(flat_dir: Path):
    adapter = EmlDirAdapter(profiles_dir=flat_dir)
    messages = adapter.list_messages(ListFilter(folder="Inbox", limit=10))
    assert [m.subject for m in messages] == ["第二封", "在建项目清单"]


def test_empty_dir_says_what_to_do(tmp_path: Path):
    empty = tmp_path / "空的"
    empty.mkdir()
    with pytest.raises(DataNotFoundError, match="没找到 .eml"):
        EmlDirAdapter(profiles_dir=empty)


# ─────────────────────────────────────────────────────────────
# ② 文件夹
# ─────────────────────────────────────────────────────────────


def test_chinese_folder_names_are_normalised(account_dir: Path):
    adapter = EmlDirAdapter(profiles_dir=account_dir)
    inbox = adapter.list_messages(
        ListFilter(account="hongbo@example.com", folder="Inbox", limit=10)
    )
    sent = adapter.list_messages(
        ListFilter(account="hongbo@example.com", folder="Sent", limit=10)
    )
    assert {m.subject for m in inbox} == {"收到的", "带附件的"}
    assert [m.subject for m in sent] == ["发出的"]


def test_unknown_folder_name_is_kept_as_is(tmp_path: Path):
    root = tmp_path / "导出" / "a@b.com" / "项目资料"
    root.mkdir(parents=True)
    (root / "1.eml").write_bytes(_eml("归档的"))
    adapter = EmlDirAdapter(profiles_dir=tmp_path / "导出")
    assert [m.folder for m in adapter.list_messages(ListFilter(folder="*", limit=5))] == [
        "项目资料"
    ]


# ─────────────────────────────────────────────────────────────
# ③ ④ 编码与嗅探
# ─────────────────────────────────────────────────────────────


def test_gb2312_subject_is_decoded(flat_dir: Path):
    adapter = EmlDirAdapter(profiles_dir=flat_dir)
    assert "在建项目清单" in {
        m.subject for m in adapter.list_messages(ListFilter(folder="Inbox", limit=10))
    }


def test_sniff_finds_headers_at_offset_zero():
    assert sniff_rfc822_offset(_eml()) == 0


def test_sniff_skips_a_proprietary_prefix(tmp_path: Path):
    """有的客户端会在 .eml 前面挂一段自己的东西, 不该因此读不了。"""
    prefix = b"\x01\x02\x03\x04" + b"\x00" * 60  # 不以换行结尾 —— 会跟 From 粘一行
    assert sniff_rfc822_offset(prefix + _eml()) == len(prefix)


def test_prefixed_file_is_still_listed(tmp_path: Path):
    root = tmp_path / "导出"
    root.mkdir()
    (root / "odd.eml").write_bytes(b"\xff" * 32 + _eml("带前缀的"))
    adapter = EmlDirAdapter(profiles_dir=root)
    assert [m.subject for m in adapter.list_messages(ListFilter(folder="Inbox"))] == [
        "带前缀的"
    ]


def test_plain_text_is_not_mistaken_for_headers():
    assert sniff_rfc822_offset(b"note: hello\nthis is not mail\n") is None


# ─────────────────────────────────────────────────────────────
# ⑤ ⑥ 性能与容错
# ─────────────────────────────────────────────────────────────


def test_listing_reads_only_the_head(tmp_path: Path):
    """导出目录可能几个 GB, 列清单不该整份读。"""
    root = tmp_path / "导出"
    root.mkdir()
    (root / "big.eml").write_bytes(_eml("大邮件", body="正文" + "填充" * 200000))
    adapter = EmlDirAdapter(profiles_dir=root)
    listed = adapter.list_messages(ListFilter(folder="Inbox"))[0]
    assert listed.subject == "大邮件"
    assert len(listed.body_text) <= 201, "列清单只给摘要"
    assert len(adapter.read_message(listed.id).body_text) > 1000, "打开才给全文"


def test_one_bad_file_does_not_empty_the_inbox(flat_dir: Path, caplog):
    (flat_dir / "broken.eml").write_bytes(b"\x00" * 4096)
    adapter = EmlDirAdapter(profiles_dir=flat_dir)
    with caplog.at_level("WARNING"):
        messages = adapter.list_messages(ListFilter(folder="Inbox", limit=10))
    assert len(messages) == 2, "好的两封照样出来"
    assert "broken.eml" in caplog.text


# ─────────────────────────────────────────────────────────────
# ⑦ ⑧ 只读边界与 id
# ─────────────────────────────────────────────────────────────


def test_attachments_are_reported(account_dir: Path):
    adapter = EmlDirAdapter(profiles_dir=account_dir)
    listed = adapter.list_messages(
        ListFilter(account="hongbo@example.com", folder="Inbox", has_attachments=True)
    )
    assert [m.subject for m in listed] == ["带附件的"]
    full = adapter.read_message(listed[0].id)
    assert [a.filename for a in full.attachments] == ["报价.pdf"]


def test_reading_returns_the_same_message(account_dir: Path):
    adapter = EmlDirAdapter(profiles_dir=account_dir)
    listed = adapter.list_messages(ListFilter(folder="*", limit=10))
    target = next(m for m in listed if m.subject == "收到的")
    full = adapter.read_message(target.id)
    assert full.message_id == target.message_id
    assert full.body_text.strip() == "你好, 这是正文。"


def test_id_cannot_escape_the_account_dir(account_dir: Path):
    adapter = EmlDirAdapter(profiles_dir=account_dir)
    with pytest.raises(DataNotFoundError):
        adapter.read_message("eml-dir|hongbo%40example.com|..%2F..%2Fsecret.eml")


def test_malformed_id_is_rejected(account_dir: Path):
    adapter = EmlDirAdapter(profiles_dir=account_dir)
    with pytest.raises(DataNotFoundError):
        adapter.read_message("foxmail-win|a|b|0")


def test_writes_are_not_claimed(flat_dir: Path):
    adapter = EmlDirAdapter(profiles_dir=flat_dir)
    message = adapter.list_messages(ListFilter(folder="Inbox"))[0]
    with pytest.raises(NotSupportedError):
        adapter.delete_message(message.id)
    with pytest.raises(NotSupportedError):
        adapter.mark_read(message.id)


def test_search_matches_subject_and_body(account_dir: Path):
    adapter = EmlDirAdapter(profiles_dir=account_dir)
    assert [m.subject for m in adapter.search("带附件", account="hongbo@example.com")] == [
        "带附件的"
    ]
    assert len(adapter.search("这是正文", account="hongbo@example.com")) == 2
