"""Apple Mail adapter 单测 (BL-EMAIL-APPLEMAIL-IMPL 5/18).

测试策略:
  - subprocess.run mock (沙箱没 osascript / Mail.app), 模拟 stdout / stderr / returncode
  - 验证: 数据解析 / 字段映射 / 错误分类 / FS/RS 分隔协议
  - 不测真 Mail.app — 那是 e2e, 手动 macOS 机器跑 (cli `catfish-email list`)
"""
from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from catfish_email.adapters import apple_mail as am
from catfish_email.adapters.apple_mail import (
    FS,
    RS,
    AppleMailAdapter,
    _escape_as_string,
    _parse_applescript_date,
    _parse_records,
    _run_osascript,
)
from catfish_email.adapters.base import (
    Account,
    ClientNotRunningError,
    DataNotFoundError,
    EmailAdapterError,
    ListFilter,
)


# ── 工具函数 ────────────────────────────────────────────


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


# ── AppleMailAdapter.list_accounts ──────────────────────


def test_list_accounts_parses_records():
    """3 个 account, 第 2 个是 default."""
    stdout = (
        f"工作{FS}work@example.com{FS}0{RS}"
        f"个人{FS}me@gmail.com{FS}1{RS}"
        f"测试{FS}test@test.cn{FS}0{RS}"
    )
    # mock _is_mail_running True + _run_osascript 第 2 次调返 stdout
    with (
        patch.object(am, "_is_mail_running", return_value=True),
        patch.object(am, "_run_osascript", return_value=stdout),
    ):
        adapter = AppleMailAdapter()
        accounts = adapter.list_accounts()
    assert len(accounts) == 3
    assert accounts[0] == Account(name="工作", address="work@example.com", is_default=False)
    assert accounts[1] == Account(name="个人", address="me@gmail.com", is_default=True)
    assert accounts[2].name == "测试"


def test_list_accounts_mail_not_running_raises():
    with patch.object(am, "_is_mail_running", return_value=False):
        with pytest.raises(ClientNotRunningError, match="Mail.app 没在跑"):
            AppleMailAdapter().list_accounts()


def test_list_accounts_empty_raises_data_not_found():
    """Mail 跑着但员工没配过邮箱."""
    with (
        patch.object(am, "_is_mail_running", return_value=True),
        patch.object(am, "_run_osascript", return_value=""),
    ):
        with pytest.raises(DataNotFoundError, match="没配过任何邮箱"):
            AppleMailAdapter().list_accounts()


# ── AppleMailAdapter.list_messages ──────────────────────


def test_list_messages_returns_snippets():
    """list 场景: 6 个 message, 验证字段映射 + body_text 留空."""
    list_stdout = (
        f"12345{FS}周报草稿{FS}张总 <zhang@x.com>{FS}"
        f"Saturday, May 17, 2026 at 10:30:00 AM{FS}1{FS}INBOX{RS}"
        f"12346{FS}EIS 资质方案{FS}周园 <zhou@x.com>{FS}"
        f"Saturday, May 17, 2026 at 11:00:00 AM{FS}0{FS}INBOX{RS}"
    )
    accounts_stdout = f"工作{FS}work@x.com{FS}1{RS}"
    with (
        patch.object(am, "_is_mail_running", return_value=True),
        patch.object(am, "_run_osascript", side_effect=[accounts_stdout, list_stdout]),
    ):
        adapter = AppleMailAdapter()
        msgs = adapter.list_messages(ListFilter(folder="INBOX", limit=10))

    assert len(msgs) == 2
    m0 = msgs[0]
    assert m0.subject == "周报草稿"
    assert m0.sender == "张总 <zhang@x.com>"
    assert m0.is_read is True
    assert m0.folder == "INBOX"
    assert m0.account == "工作"
    assert m0.body_text == ""  # list 场景不带 body
    assert m0.id.startswith("工作|")
    assert m0.date.startswith("2026-05-17")

    m1 = msgs[1]
    assert m1.is_read is False  # 第 2 个未读


def test_list_messages_subject_contains_filter():
    """Python 端的 sender/subject_contains 后过滤."""
    list_stdout = (
        f"1{FS}周报草稿{FS}a@x.com{FS}date1{FS}1{FS}INBOX{RS}"
        f"2{FS}EIS 方案{FS}b@x.com{FS}date2{FS}1{FS}INBOX{RS}"
        f"3{FS}周报二稿{FS}c@x.com{FS}date3{FS}1{FS}INBOX{RS}"
    )
    accounts_stdout = f"工作{FS}work@x.com{FS}1{RS}"
    with (
        patch.object(am, "_is_mail_running", return_value=True),
        patch.object(am, "_run_osascript", side_effect=[accounts_stdout, list_stdout]),
    ):
        msgs = AppleMailAdapter().list_messages(
            ListFilter(folder="INBOX", subject_contains="周报", limit=10),
        )
    assert len(msgs) == 2
    assert all("周报" in m.subject for m in msgs)


def test_list_messages_unread_only_passes_through_to_as():
    """unread_only=True 会进 AS, 但 AS mock 不感知 — 我们验 Python 端把它正确转 'true'."""
    captured: list[str] = []

    def fake_run(script, **_):
        captured.append(script)
        # 第 1 次调 _is_mail_running → 不进这里 (mock 了)
        # 第 1 次 list_accounts AS
        if "first account whose name" not in script:
            return f"工作{FS}work@x.com{FS}1{RS}"
        return ""  # list_messages 返空

    with (
        patch.object(am, "_is_mail_running", return_value=True),
        patch.object(am, "_run_osascript", side_effect=fake_run),
    ):
        AppleMailAdapter().list_messages(ListFilter(unread_only=True, limit=5))
    # 找 list_messages 用的 script
    list_script = next(s for s in captured if "set unreadOnly" in s)
    assert "set unreadOnly to true" in list_script
    assert "set limitN to 5" in list_script


# ── AppleMailAdapter._pack_id / _unpack_id ──────────────


def test_pack_unpack_id_roundtrip():
    adapter = AppleMailAdapter()
    packed = adapter._pack_id("工作", "12345")
    assert packed == "工作|12345"
    assert adapter._unpack_id(packed) == ("工作", "12345")


def test_unpack_id_rejects_malformed():
    adapter = AppleMailAdapter()
    with pytest.raises(ValueError, match="格式错"):
        adapter._unpack_id("no-pipe-here")


# ── AppleMailAdapter.search ─────────────────────────────


def test_search_empty_query_returns_empty_list():
    with patch.object(am, "_is_mail_running", return_value=True):
        msgs = AppleMailAdapter().search("   ")
    assert msgs == []


def test_search_returns_results():
    accounts_stdout = f"工作{FS}work@x.com{FS}1{RS}"
    search_stdout = f"99{FS}找资质方案{FS}a@x.com{FS}date1{FS}1{FS}INBOX{RS}"
    with (
        patch.object(am, "_is_mail_running", return_value=True),
        patch.object(am, "_run_osascript", side_effect=[accounts_stdout, search_stdout]),
    ):
        msgs = AppleMailAdapter().search("资质")
    assert len(msgs) == 1
    assert msgs[0].subject == "找资质方案"


# ── AppleMailAdapter._resolve_account_name ──────────────


def test_resolve_account_name_default():
    """None → 走 is_default=True 的那个."""
    accounts_stdout = (
        f"工作{FS}work@x.com{FS}0{RS}"
        f"个人{FS}me@x.com{FS}1{RS}"
    )
    with (
        patch.object(am, "_is_mail_running", return_value=True),
        patch.object(am, "_run_osascript", return_value=accounts_stdout),
    ):
        assert AppleMailAdapter()._resolve_account_name(None) == "个人"


def test_resolve_account_name_by_email():
    """传 email 地址 → 映射回显示名."""
    accounts_stdout = f"工作{FS}work@x.com{FS}1{RS}"
    with (
        patch.object(am, "_is_mail_running", return_value=True),
        patch.object(am, "_run_osascript", return_value=accounts_stdout),
    ):
        assert AppleMailAdapter()._resolve_account_name("work@x.com") == "工作"


def test_resolve_account_name_not_found_raises():
    accounts_stdout = f"工作{FS}work@x.com{FS}1{RS}"
    with (
        patch.object(am, "_is_mail_running", return_value=True),
        patch.object(am, "_run_osascript", return_value=accounts_stdout),
    ):
        with pytest.raises(DataNotFoundError, match="找不到账号"):
            AppleMailAdapter()._resolve_account_name("ghost@nowhere.com")


# ── inbox.py factory: apple-mail 注册 ───────────────────


def test_factory_apple_mail_registered():
    """工厂能识别 'apple-mail' 串."""
    from catfish_email.inbox import _get_adapter_explicit
    adapter = _get_adapter_explicit("apple-mail")
    assert adapter.name == "apple_mail"
    assert adapter.supports_drafts is True


def test_factory_outlook_mac_alias_to_apple_mail():
    """老 'outlook-mac' alias 到 apple-mail (deprecation period)."""
    from catfish_email.inbox import _get_adapter_explicit
    adapter = _get_adapter_explicit("outlook-mac")
    assert adapter.name == "apple_mail"
