"""apple_mail_osascript 单测 —— osascript 执行层与输出解析。

从 test_adapter_apple_mail.py 拆出 (8/13, 源码同步拆成 apple_mail_osascript.py)。
这里全是模块级纯函数: 错误码怎么翻译成人话、FS/RS 协议怎么切、AS 字符串怎么
转义、raw headers 怎么挑出 thread 三件套。都不需要 adapter 实例。

subprocess.run 统一 patch 全局桩 —— 沙箱里没有 osascript, 也没有 Mail.app。
"""
from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from catfish_email.adapters.apple_mail import (
    FS,
    RS,
    _escape_as_string,
    _parse_applescript_date,
    _parse_records,
    _run_osascript,
)
from catfish_email.adapters.apple_mail import _parse_thread_headers as _pth
from catfish_email.adapters.base import (
    ClientNotRunningError,
    DataNotFoundError,
    EmailAdapterError,
)


def _mock_completed_process(stdout: str = "", stderr: str = "", returncode: int = 0):
    """构造 subprocess.run 返的 CompletedProcess 假值."""
    cp = MagicMock(spec=subprocess.CompletedProcess)
    cp.stdout = stdout
    cp.stderr = stderr
    cp.returncode = returncode
    return cp




# ── _run_osascript: 错误映射 ────────────────────────────


def test_run_osascript_success_returns_stdout():
    with patch("subprocess.run", return_value=_mock_completed_process(stdout="hello\n")):
        assert _run_osascript("anything") == "hello"



def test_run_osascript_timeout_raises_adapter_error():
    err = subprocess.TimeoutExpired(cmd=["osascript"], timeout=5)
    with patch("subprocess.run", side_effect=err):
        with pytest.raises(EmailAdapterError, match="超时"):
            _run_osascript("anything", timeout=5)



def test_run_osascript_message_not_found_raises_data_not_found():
    with patch(
        "subprocess.run",
        return_value=_mock_completed_process(
            stderr="execution error: MESSAGE_NOT_FOUND (8001)", returncode=1,
        ),
    ):
        with pytest.raises(DataNotFoundError, match="找不到这条消息"):
            _run_osascript("anything")



def test_run_osascript_permission_denied_raises_client_not_running():
    """Automation 权限缺 → ClientNotRunningError 含人话提示."""
    with patch(
        "subprocess.run",
        return_value=_mock_completed_process(
            stderr="execution error: Not authorized to send Apple events to Mail. (-1743)",
            returncode=1,
        ),
    ):
        with pytest.raises(ClientNotRunningError, match="Privacy & Security"):
            _run_osascript("anything")



def test_run_osascript_mail_not_running_raises_client_not_running():
    """-600 → Mail.app 没开."""
    with patch(
        "subprocess.run",
        return_value=_mock_completed_process(
            stderr="execution error: Application isn't running. (-600)", returncode=1,
        ),
    ):
        with pytest.raises(ClientNotRunningError, match="Mail.app 没在跑"):
            _run_osascript("anything")



def test_run_osascript_missing_osascript_binary_raises():
    """非 macOS 平台 (osascript 不存在) → EmailAdapterError + 友好提示."""
    with patch("subprocess.run", side_effect=FileNotFoundError):
        with pytest.raises(EmailAdapterError, match="仅支持 macOS"):
            _run_osascript("anything")



def test_run_osascript_invalid_index_1719_translates_to_friendly_error():
    """5/18 BL-EMAIL-APPLEMAIL-INVALID-INDEX: -1719 (account 名找不到) 应该
    翻译成 DataNotFoundError + 引导用 list_accounts 拿真实名, 不是原始 AS 报错."""
    raw_err = (
        "322:363: execution error: \"Mail\" 遇到一个错误: "
        "不能获得\"account 1 whose name = \\\"jini.chen@icloud.com\\\"\". "
        "无效的索引。 (-1719)"
    )
    with patch(
        "subprocess.run",
        return_value=_mock_completed_process(stderr=raw_err, returncode=1),
    ):
        with pytest.raises(DataNotFoundError, match="账号"):
            _run_osascript("anything")



def test_run_osascript_invalid_index_english_also_translated():
    """英语 macOS 也认 'Invalid index' 文案"""
    raw_err = (
        "execution error: Mail got an error: "
        "Can't get account 1 whose name = \"foo\". "
        "Invalid index. (-1719)"
    )
    with patch(
        "subprocess.run",
        return_value=_mock_completed_process(stderr=raw_err, returncode=1),
    ):
        with pytest.raises(DataNotFoundError, match="账号"):
            _run_osascript("anything")



# ── _parse_records ──────────────────────────────────────


def test_parse_records_basic():
    text = f"a{FS}b{FS}c{RS}d{FS}e{FS}f{RS}"
    records = _parse_records(text, n_fields=3)
    assert records == [["a", "b", "c"], ["d", "e", "f"]]



def test_parse_records_empty_input():
    assert _parse_records("", n_fields=3) == []
    assert _parse_records(f"{RS}", n_fields=3) == []



def test_parse_records_skips_short_records():
    """字段数不够的记录跳过, 不抛."""
    text = f"a{FS}b{FS}c{RS}only2{FS}fields{RS}"
    records = _parse_records(text, n_fields=3)
    assert records == [["a", "b", "c"]]



def test_parse_records_trims_record_whitespace():
    """末尾换行 / 空白 不影响切分."""
    text = f"a{FS}b{FS}c\n{RS}\nd{FS}e{FS}f{RS}\n"
    records = _parse_records(text, n_fields=3)
    assert records == [["a", "b", "c"], ["d", "e", "f"]]



# ── _parse_applescript_date ─────────────────────────────


def test_parse_applescript_date_english_locale():
    """英文 locale: 'Friday, May 17, 2026 at 1:30:00 PM'."""
    iso = _parse_applescript_date("Friday, May 17, 2026 at 1:30:00 PM")
    assert iso.startswith("2026-05-17")



def test_parse_applescript_date_rfc2822_passthrough():
    """RFC 2822 也能解 (Mail header 有时是这格式)."""
    iso = _parse_applescript_date("Sat, 17 May 2026 13:30:00 +0000")
    assert iso.startswith("2026-05-17")



def test_parse_applescript_date_unparseable_returns_original():
    """解析不了原样返, 不抛."""
    weird = "完全乱来的日期文字"
    assert _parse_applescript_date(weird) == weird



def test_parse_applescript_date_empty_returns_empty():
    assert _parse_applescript_date("") == ""



# ── _escape_as_string ───────────────────────────────────


def test_escape_as_string_quotes():
    """双引号要 escape (AS 字符串字面量)."""
    assert _escape_as_string('hello "world"') == 'hello \\"world\\"'



def test_escape_as_string_newlines_replaced_with_space():
    """换行替成空格 (AS 不允许字面量里 raw newline)."""
    assert _escape_as_string("line1\nline2\r\nline3") == "line1 line2  line3"



def test_escape_as_string_backslash():
    """backslash 要 escape."""
    assert _escape_as_string(r"C:\path") == r"C:\\path"



def test_thread_headers_干净的块():
    assert _pth(
        "Delivered-To: a@x\nMessage-ID: <m@x>\nIn-Reply-To: <p@x>\nReferences: <r@x> <p@x>\n"
    ) == ("<m@x>", "<p@x>", "<r@x> <p@x>")



def test_thread_headers_中间有裸行时仍读得到():
    """★ 回归: 旧的 email.parser 实现在这里返 (None, None, None)。"""
    raw = "A: 1\nwLwYWlsZ3VuLVRhZzogZXZlbnQ=\nMessage-ID: <m@x>\nIn-Reply-To: <p@x>\n"
    assert _pth(raw) == ("<m@x>", "<p@x>", None)



def test_thread_headers_裸行含_json():
    raw = 'X-T: {"click_tracking":false}\n{"nested":true}\nMessage-ID: <m@x>\n'
    assert _pth(raw)[0] == "<m@x>"



def test_thread_headers_头在最末尾也读得到():
    raw = "A: 1\n裸行\n" * 20 + "In-Reply-To: <p@x>\n"
    assert _pth(raw)[1] == "<p@x>"



def test_thread_headers_大小写不敏感():
    assert _pth("message-id: <m@x>\nIN-REPLY-TO: <p@x>\nreFerenCes: <r@x>\n") == (
        "<m@x>", "<p@x>", "<r@x>",
    )



def test_thread_headers_折行续行():
    # 长 References 被折成多行, 续行以 space/tab 开头
    assert _pth("References: <a@x>\n <b@x>\n\t<c@x>\n")[2] == "<a@x> <b@x> <c@x>"



def test_thread_headers_同名头取第一个():
    assert _pth("Message-ID: <first@x>\nMessage-ID: <second@x>\n")[0] == "<first@x>"



def test_thread_headers_空值与空输入():
    assert _pth("Message-ID: \nIn-Reply-To: <p@x>\n") == (None, "<p@x>", None)
    assert _pth("") == (None, None, None)
    assert _pth("aaa\nbbb\n") == (None, None, None)

