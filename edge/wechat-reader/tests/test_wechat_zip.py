from __future__ import annotations

from pathlib import Path
import zipfile

import pytest

from catfish_wechat_reader import wechat_zip
from catfish_wechat_reader.readers import ReaderFailure
from catfish_wechat_reader.wechat_zip import read_export

from wechat_fixtures import GROUP_A, GROUP_A_MEDIA, MEDIA_DIR, make_export


def _by_text(parsed, prefix: str):
    return next(m for m in parsed.messages if m.text.startswith(prefix))


def test_group_export_parses_every_message_and_sender(tmp_path: Path) -> None:
    parsed = read_export(make_export(tmp_path / "a.zip", GROUP_A, GROUP_A_MEDIA))
    assert len(parsed.messages) == len(GROUP_A)
    assert dict(parsed.senders) == {"测试甲": 2, "测试乙": 2, "测试丙": 2, "测试丁": 1, "我自己": 1}
    assert parsed.start.isoformat().startswith("2026-09-14T15:06")
    assert parsed.end.isoformat().startswith("2026-09-14T15:12")
    assert parsed.transcript_path == "聊天记录.txt"
    assert len(parsed.sha256) == 64


def test_known_tags_and_attachment_presence(tmp_path: Path) -> None:
    parsed = read_export(make_export(tmp_path / "a.zip", GROUP_A, GROUP_A_MEDIA))
    pdf = _by_text(parsed, "[文件] 年审材料")
    assert (pdf.type, pdf.attachment_name, pdf.attachment_present) == (
        "file", "年审材料-盖章版.pdf", True)
    image = _by_text(parsed, "[图片]")
    assert (image.type, image.attachment_present) == ("image", True)
    # 压缩包附件不随导出: 名字在 TXT 里, 包里没有
    rar = _by_text(parsed, "[文件] 原始底稿")
    assert (rar.type, rar.attachment_name, rar.attachment_present) == ("file", "原始底稿.rar", False)
    assert _by_text(parsed, "[小程序]").type == "miniprogram"
    assert _by_text(parsed, "[语音通话]").type == "call"
    assert parsed.attachment_summary() == {"in_archive": 2, "referenced": 3, "missing": 1}


def test_emoji_shorthand_and_unknown_tags_stay_text(tmp_path: Path) -> None:
    messages = [("测试甲", "2026年9月1日 09:00", "[OK]"), ("测试乙", "2026年9月1日 09:01", "[没见过] 东西")]
    parsed = read_export(make_export(tmp_path / "a.zip", messages))
    assert [(m.type, m.text, m.attachment_name) for m in parsed.messages] == [
        ("text", "[OK]", None), ("text", "[没见过] 东西", None)]


def test_unknown_tag_links_only_when_archive_has_the_file(tmp_path: Path) -> None:
    messages = [("测试甲", "2026年9月1日 09:00", "[视频] clip.mp4")]
    parsed = read_export(make_export(tmp_path / "a.zip", messages, {"clip.mp4": b"x"}))
    assert (parsed.messages[0].type, parsed.messages[0].attachment_name) == ("text", "clip.mp4")
    assert parsed.messages[0].attachment_present is True


def test_mention_space_is_normalised_and_multiline_body_kept(tmp_path: Path) -> None:
    parsed = read_export(make_export(tmp_path / "a.zip", GROUP_A, GROUP_A_MEDIA))
    mention = _by_text(parsed, "@测试甲")
    assert mention.text == "@测试甲 收到\n第二行说明"


def test_bom_crlf_and_gb18030_are_accepted(tmp_path: Path) -> None:
    body = "\ufeff·测试甲\r\n2026年9月1日 09:00\r\n你好\r\n\r\n".encode("utf-8")
    path = tmp_path / "bom.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("聊天记录.txt", body)
    assert read_export(path).messages[0].text == "你好"
    gbk = make_export(tmp_path / "gbk.zip", [("测试甲", "2026年9月1日 09:00", "中文")], encoding="gb18030")
    assert read_export(gbk).messages[0].sender == "测试甲"


def test_misaligned_transcript_is_rejected_not_guessed(tmp_path: Path) -> None:
    path = tmp_path / "bad.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("聊天记录.txt", "导出说明\n·测试甲\n2026年9月1日 09:00\n你好\n")
    with pytest.raises(ReaderFailure) as error:
        read_export(path)
    assert error.value.reason_code == "unsupported_format"


@pytest.mark.parametrize("name", ["../evil.txt", "/abs.txt", "a\\b.txt", "c:/x.txt"])
def test_unsafe_paths_are_rejected(tmp_path: Path, name: str) -> None:
    path = tmp_path / "evil.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("聊天记录.txt", "·测试甲\n2026年9月1日 09:00\n你好\n")
        archive.writestr(zipfile.ZipInfo(name), b"x")
    with pytest.raises(ReaderFailure):
        read_export(path)


def test_encrypted_entry_is_rejected(tmp_path: Path) -> None:
    path = make_export(tmp_path / "enc.zip", [("测试甲", "2026年9月1日 09:00", "你好")])
    data = bytearray(path.read_bytes())
    # 把中央目录里的 general purpose flag 加上加密位
    offset = data.find(b"PK\x01\x02")
    data[offset + 8] |= 0x1
    path.write_bytes(bytes(data))
    with pytest.raises(ReaderFailure) as error:
        read_export(path)
    assert error.value.reason_code == "unsupported_format"


def test_limits_fail_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = make_export(tmp_path / "a.zip", GROUP_A, GROUP_A_MEDIA)
    monkeypatch.setattr(wechat_zip, "MAX_ENTRIES", 2)
    with pytest.raises(ReaderFailure):
        read_export(path)
    monkeypatch.setattr(wechat_zip, "MAX_ENTRIES", 1000)
    monkeypatch.setattr(wechat_zip, "MAX_TRANSCRIPT_BYTES", 10)
    with pytest.raises(ReaderFailure) as error:
        read_export(path)
    assert error.value.reason_code == "source_too_large"


def test_corrupt_or_textless_archives_fail(tmp_path: Path) -> None:
    broken = tmp_path / "broken.zip"
    broken.write_bytes(b"PK\x03\x04not really a zip")
    with pytest.raises(ReaderFailure):
        read_export(broken)
    empty = tmp_path / "media_only.zip"
    with zipfile.ZipFile(empty, "w") as archive:
        archive.writestr(f"{MEDIA_DIR}/a.jpg", b"x")
    with pytest.raises(ReaderFailure) as error:
        read_export(empty)
    assert error.value.reason_code == "unsupported_format"


def test_non_native_txt_name_is_used_when_it_parses(tmp_path: Path) -> None:
    path = make_export(tmp_path / "a.zip", [("测试甲", "2026年9月1日 09:00", "你好")],
                       transcript_name="chat.txt")
    assert read_export(path).transcript_path == "chat.txt"
