"""box_parser 单测 —— 核心 Foxmail .box 格式解析。"""
from __future__ import annotations

import struct
from email.utils import formatdate
from pathlib import Path

import pytest

from catfish_email import box_parser
from catfish_email.box_parser import (
    HEADER_SIZE,
    MAGIC_FOXM,
    MAGIC_LEGACY,
    parse_box_file,
    parse_eml_directory,
    detect_storage_mode,
    extract_header,
    parse_date_to_iso,
    is_read,
    extract_body_text,
    extract_body_html,
    extract_attachment_metadata,
    has_attachments,
)


# ============================================================
# parse_box_file: 基本路径
# ============================================================


def test_parse_box_file_single_message(make_box_file):
    """1 封正常邮件 → 1 条 ParsedMessage"""
    path = make_box_file([
        {"subject": "Hello", "sender": "a@x.com", "to": "b@x.com", "body": "Hi"},
    ])
    msgs = list(parse_box_file(path))
    assert len(msgs) == 1
    assert msgs[0].message["Subject"] == "Hello"
    assert "Hi" in extract_body_text(msgs[0].message)


def test_parse_box_file_multiple_messages(make_box_file):
    """多封连续 → 按顺序 yield"""
    path = make_box_file([
        {"subject": "A"}, {"subject": "B"}, {"subject": "C"},
    ])
    msgs = list(parse_box_file(path))
    subjects = [m.message["Subject"] for m in msgs]
    assert subjects == ["A", "B", "C"]
    # raw_offset 单调递增
    offsets = [m.raw_offset for m in msgs]
    assert offsets == sorted(offsets)
    assert offsets[0] == 0


def test_parse_box_file_flags_propagated(make_box_file):
    """flags 应该原样传到 ParsedMessage"""
    path = make_box_file([
        {"subject": "Read", "flags": 0x01},      # 已读
        {"subject": "Unread", "flags": 0x00},    # 未读
        {"subject": "Read+Star", "flags": 0x09}, # 已读 + 标星
    ])
    msgs = list(parse_box_file(path))
    assert msgs[0].flags == 0x01
    assert msgs[1].flags == 0x00
    assert msgs[2].flags == 0x09
    # is_read helper 跟着对
    assert is_read(msgs[0].flags) is True
    assert is_read(msgs[1].flags) is False
    assert is_read(msgs[2].flags) is True


# ============================================================
# parse_box_file: 容错 (历史 box 各种坑)
# ============================================================


def test_parse_box_file_missing_file_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        list(parse_box_file(tmp_path / "nope.box"))


def test_parse_box_file_skip_bad_magic(make_box_file, tmp_path: Path):
    """坏 magic 应该被跳过, 不抛异常 (一封坏邮件不该掀翻整个收件箱)"""
    # 先做一个正常 .box, 再在前面塞 100 字节垃圾
    good = make_box_file([{"subject": "Good"}])
    bad_data = b"\x00\xFF" * 50 + good.read_bytes()
    bad_path = tmp_path / "bad.box"
    bad_path.write_bytes(bad_data)
    msgs = list(parse_box_file(bad_path))
    # 至少能拿到那封 "Good"
    assert any(m.message["Subject"] == "Good" for m in msgs)


def test_parse_box_file_truncated_message(tmp_path: Path):
    """header 说有 1000 字节但文件只有 100 → 不 yield, 不 crash"""
    header = MAGIC_FOXM + struct.pack("<I", 10000) + struct.pack("<I", 0) + b"\x00\x00"
    truncated = header + b"only 100 bytes" * 10
    path = tmp_path / "trunc.box"
    path.write_bytes(truncated)
    msgs = list(parse_box_file(path))
    assert msgs == []


def test_parse_box_file_zero_length_skipped(tmp_path: Path):
    """length=0 的 header 应跳过, 不无限循环"""
    header = MAGIC_FOXM + struct.pack("<I", 0) + struct.pack("<I", 0) + b"\x00\x00"
    path = tmp_path / "zero.box"
    path.write_bytes(header * 3)
    msgs = list(parse_box_file(path))
    assert msgs == []


def test_parse_box_file_legacy_magic(tmp_path: Path):
    """旧版 magic 也要认"""
    body = b"Subject: Legacy\r\n\r\nold magic test\r\n"
    header = MAGIC_LEGACY + struct.pack("<I", len(body)) + struct.pack("<I", 0) + b"\x00\x00"
    path = tmp_path / "legacy.box"
    path.write_bytes(header + body)
    msgs = list(parse_box_file(path))
    assert len(msgs) == 1
    assert msgs[0].message["Subject"] == "Legacy"


def test_parse_box_file_cjk_subject(make_box_file):
    """中文主题 / 正文不能乱码"""
    path = make_box_file([
        {"subject": "周报 - 鲶鱼平台进展", "body": "本周完成 P0-1 ~ P0-5"},
    ])
    msgs = list(parse_box_file(path))
    # 主题里有"鲶鱼"
    assert "鲶鱼" in msgs[0].message["Subject"]
    assert "周报" in msgs[0].message["Subject"]
    # 正文里有"本周完成"
    body = extract_body_text(msgs[0].message)
    assert "本周完成" in body
    assert "P0-1" in body


# ============================================================
# parse_eml_directory
# ============================================================


def test_parse_eml_directory_basic(tmp_path: Path):
    from email.message import EmailMessage
    d = tmp_path / "drafts"
    d.mkdir()
    for i in range(3):
        m = EmailMessage()
        m["Subject"] = f"Draft {i}"
        m["From"] = "me@x.com"
        m.set_content(f"Body {i}")
        (d / f"{i}.eml").write_bytes(m.as_bytes())
    msgs = list(parse_eml_directory(d))
    assert len(msgs) == 3
    subjects = sorted(m.message["Subject"] for m in msgs)
    assert subjects == ["Draft 0", "Draft 1", "Draft 2"]


def test_parse_eml_directory_empty(tmp_path: Path):
    d = tmp_path / "empty_drafts"
    d.mkdir()
    assert list(parse_eml_directory(d)) == []


def test_parse_eml_directory_skip_bad_eml(tmp_path: Path):
    d = tmp_path / "drafts"
    d.mkdir()
    (d / "bad.eml").write_bytes(b"\x00\xFF this is not RFC822")
    # email.parser 是非常宽容的, 大多数 garbage 都能解析为空 message
    # 我们至少验证不 crash
    msgs = list(parse_eml_directory(d))
    # 可能是 0 (rejected) 或 1 (parsed as empty), 都行 —— 关键是不抛
    assert isinstance(msgs, list)


# ============================================================
# detect_storage_mode
# ============================================================


def test_detect_storage_mode_box(make_box_file):
    p = make_box_file([{"subject": "x"}])
    assert detect_storage_mode(p.parent) == "box"


def test_detect_storage_mode_eml(tmp_path: Path):
    d = tmp_path / "drafts"
    d.mkdir()
    (d / "1.eml").write_bytes(b"Subject: x\r\n\r\n")
    assert detect_storage_mode(d) == "eml"


def test_detect_storage_mode_unknown(tmp_path: Path):
    d = tmp_path / "empty"
    d.mkdir()
    assert detect_storage_mode(d) == "unknown"


def test_detect_storage_mode_nonexistent(tmp_path: Path):
    assert detect_storage_mode(tmp_path / "nope") == "unknown"


# ============================================================
# extract_header / parse_date / is_read
# ============================================================


def test_extract_header_safe_default(make_box_file):
    path = make_box_file([{"subject": "S"}])
    msgs = list(parse_box_file(path))
    assert extract_header(msgs[0].message, "Nonexistent") == ""
    assert extract_header(msgs[0].message, "Nonexistent", "default") == "default"


def test_parse_date_to_iso():
    raw = formatdate(timeval=1777161600.0, usegmt=True)  # 2026-04-26 00:00 UTC
    iso = parse_date_to_iso(raw)
    assert iso.startswith("2026-04-26")


def test_parse_date_to_iso_empty():
    assert parse_date_to_iso("") == ""
    assert parse_date_to_iso("garbage") == ""


# ============================================================
# 正文 / HTML / 附件
# ============================================================


def test_extract_body_text_truncates(make_box_file):
    long_body = "字" * 1000
    path = make_box_file([{"body": long_body}])
    msgs = list(parse_box_file(path))
    snippet = extract_body_text(msgs[0].message, max_chars=200)
    assert len(snippet) <= 201  # 200 + '…'
    assert snippet.endswith("…")


def test_extract_body_text_no_truncate_when_short(make_box_file):
    path = make_box_file([{"body": "short"}])
    msgs = list(parse_box_file(path))
    txt = extract_body_text(msgs[0].message, max_chars=200)
    assert "short" in txt
    assert not txt.endswith("…")


def test_html_message_strip(tmp_path: Path):
    """HTML 正文 → 纯文本, 简陋去 tag"""
    from email.message import EmailMessage
    import struct as _struct
    m = EmailMessage()
    m["Subject"] = "html"
    m["From"] = "a@x.com"
    m.set_content("<p>Hello <strong>world</strong></p><script>evil()</script>")
    m.replace_header("Content-Type", 'text/html; charset="utf-8"')
    body = m.as_bytes()
    header = MAGIC_FOXM + _struct.pack("<I", len(body)) + _struct.pack("<I", 0) + b"\x00\x00"
    path = tmp_path / "html.box"
    path.write_bytes(header + body)
    msgs = list(parse_box_file(path))
    txt = extract_body_text(msgs[0].message)
    assert "Hello" in txt
    assert "world" in txt
    assert "evil()" not in txt
    assert "<p>" not in txt


def test_attachment_metadata_extraction(tmp_path: Path):
    """带附件的 multipart 邮件"""
    from email.message import EmailMessage
    import struct as _struct
    m = EmailMessage()
    m["Subject"] = "with attachment"
    m["From"] = "a@x.com"
    m.set_content("see attachment")
    m.add_attachment(b"PDF content", maintype="application", subtype="pdf", filename="report.pdf")
    body = m.as_bytes()
    header = MAGIC_FOXM + _struct.pack("<I", len(body)) + _struct.pack("<I", 0) + b"\x00\x00"
    path = tmp_path / "att.box"
    path.write_bytes(header + body)
    msgs = list(parse_box_file(path))
    assert has_attachments(msgs[0].message)
    metas = extract_attachment_metadata(msgs[0].message)
    assert len(metas) == 1
    fn, sz, ct = metas[0]
    assert fn == "report.pdf"
    assert sz > 0
    assert "pdf" in ct.lower()


def test_no_attachments_for_plain_text(make_box_file):
    path = make_box_file([{"body": "plain"}])
    msgs = list(parse_box_file(path))
    assert not has_attachments(msgs[0].message)
    assert extract_attachment_metadata(msgs[0].message) == []
