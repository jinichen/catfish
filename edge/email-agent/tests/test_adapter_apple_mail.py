"""Apple Mail adapter 单测 (BL-EMAIL-APPLEMAIL-IMPL 5/18).

测试策略:
  - subprocess.run mock (沙箱没 osascript / Mail.app), 模拟 stdout / stderr / returncode
  - 验证: 数据解析 / 字段映射 / 错误分类 / FS/RS 分隔协议
  - 不测真 Mail.app — 那是 e2e, 手动 macOS 机器跑 (cli `catfish-email list`)
"""
from __future__ import annotations

import os
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


# ── AppleMailAdapter.list_accounts ──────────────────────


def test_list_accounts_parses_records():
    """3 个 account, is_default 全 False (BL-EMAIL-APPLEMAIL-AS-CTRLCHAR 5/18:
    AS 'default account' 语法在 macOS Sequoia 挂 -2741, MVP 不识别默认账号)."""
    stdout = (
        f"工作{FS}work@example.com{FS}0{RS}"
        f"个人{FS}me@gmail.com{FS}0{RS}"
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
    assert accounts[1] == Account(name="个人", address="me@gmail.com", is_default=False)
    assert accounts[2].name == "测试"


def test_list_accounts_mail_not_running_raises():
    # W2 BL-APPLEMAIL-TEST-EMLX-MOCK (7/11): 5/18 加 EMLX fallback 后, 若开发机
    # 本地有 ~/Library/Mail/V10/, _enable_emlx_fallback_if_available 会返 True,
    # list_accounts 走 _list_accounts_emlx() 抢先返, 就不 raise 了.
    # 沙箱 CI runner 上没 V10 目录 test 会过, 但开发机上 test DID NOT RAISE.
    # 补 mock 让 test 语义严格: "AS + EMLX 都不可用" → 才 raise ClientNotRunningError.
    adapter = AppleMailAdapter()
    with (
        patch.object(am, "_is_mail_running", return_value=False),
        patch.object(adapter, "_enable_emlx_fallback_if_available", return_value=False),
    ):
        with pytest.raises(ClientNotRunningError, match="Mail.app 没在跑"):
            adapter.list_accounts()


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
    """list 场景: 2 个 message, 验证字段映射 + body_text 留空.

    P3.5.58 (6/22) 升 AS list 6→8 字段 (加 rfcMsgId + rawHdrs 给 thread parse),
    fixture 跟着补这 2 个字段. P3.5.101 (6/24) 测试与代码补齐.
    """
    list_stdout = (
        f"12345{FS}周报草稿{FS}张总 <zhang@x.com>{FS}"
        f"Saturday, May 17, 2026 at 10:30:00 AM{FS}1{FS}INBOX{FS}"
        f"<msg1@example.com>{FS}{RS}"
        f"12346{FS}EIS 资质方案{FS}周园 <zhou@x.com>{FS}"
        f"Saturday, May 17, 2026 at 11:00:00 AM{FS}0{FS}INBOX{FS}"
        f"<msg2@example.com>{FS}{RS}"
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
    # 5/18 BL-EMAIL-READ-ROUTING-BY-PREFIX: 3 段格式 'apple_mail|account|msg_id'
    assert m0.id.startswith("apple_mail|工作|")
    assert m0.date.startswith("2026-05-17")
    # P3.5.58: rfcMsgId 真透传到 Message.message_id (raw_hdrs 给空, in_reply_to/references 为 None)
    assert m0.message_id == "<msg1@example.com>"
    assert m0.in_reply_to is None
    assert m0.references is None

    m1 = msgs[1]
    assert m1.is_read is False  # 第 2 个未读
    assert m1.message_id == "<msg2@example.com>"


def test_list_messages_subject_contains_filter():
    """Python 端的 sender/subject_contains 后过滤.

    P3.5.101 (6/24): fixture 跟 P3.5.58 8 字段格式 (rfcMsgId + rawHdrs 末尾).
    """
    list_stdout = (
        f"1{FS}周报草稿{FS}a@x.com{FS}date1{FS}1{FS}INBOX{FS}{FS}{RS}"
        f"2{FS}EIS 方案{FS}b@x.com{FS}date2{FS}1{FS}INBOX{FS}{FS}{RS}"
        f"3{FS}周报二稿{FS}c@x.com{FS}date3{FS}1{FS}INBOX{FS}{FS}{RS}"
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
    """5/18 BL-EMAIL-READ-ROUTING-BY-PREFIX: 新 _pack_id 加 apple_mail| 前缀."""
    adapter = AppleMailAdapter()
    packed = adapter._pack_id("工作", "12345")
    assert packed == "apple_mail|工作|12345"
    assert adapter._unpack_id(packed) == ("工作", "12345")


def test_unpack_id_backward_compat_two_segments():
    """5/18 BL-EMAIL-READ-ROUTING-BY-PREFIX: 老 2 段格式 (没 apple_mail 前缀) 仍能 unpack."""
    adapter = AppleMailAdapter()
    assert adapter._unpack_id("工作|12345") == ("工作", "12345")


def test_unpack_id_rejects_malformed():
    adapter = AppleMailAdapter()
    with pytest.raises(ValueError, match="格式错"):
        adapter._unpack_id("no-pipe-here")


# ── AppleMailAdapter.search ─────────────────────────────


def test_search_empty_query_returns_empty_list():
    with patch.object(am, "_is_mail_running", return_value=True):
        msgs = AppleMailAdapter().search("   ")
    assert msgs == []


def _make_search_osa(accounts_stdout: str, search_stdout: str | list[str],
                     captured: dict | None = None):
    """构造一个 script-aware fake osascript:
        - script 含 `every account` → 返 accounts_stdout (_AS_LIST_ACCOUNTS)
        - 其他 (即 _AS_SEARCH) → 返 search_stdout, 顺手 capture
    search_stdout 可传 str (每次同样返) 或 list (按顺序消耗, 跨账号场景).
    """
    state = {"i": 0}
    def fn(script: str) -> str:
        if "every account" in script:
            return accounts_stdout
        if captured is not None:
            captured["script"] = script
        if isinstance(search_stdout, list):
            i = state["i"]
            state["i"] += 1
            return search_stdout[i] if i < len(search_stdout) else ""
        return search_stdout
    return fn


def test_search_returns_results():
    accounts_stdout = f"工作{FS}work@x.com{FS}1{RS}"
    search_stdout = f"99{FS}找资质方案{FS}a@x.com{FS}date1{FS}1{FS}INBOX{RS}"
    with (
        patch.object(am, "_is_mail_running", return_value=True),
        patch.object(
            am, "_run_osascript",
            side_effect=_make_search_osa(accounts_stdout, search_stdout),
        ),
    ):
        msgs = AppleMailAdapter().search("资质")
    assert len(msgs) == 1
    assert msgs[0].subject == "找资质方案"


def test_search_default_account_none_iterates_all_accounts():
    """P3.5.153: account=None 跨所有 Apple Mail 账号搜, 合并 + date 倒序 + 截 limit.

    场景: 鸿波本机有 3 个 apple_mail 账号 (iCloud / Google / Chinatelecom),
    fflijl/ffhongyd 邮件在 Chinatelecom 账号. 老逻辑只搜 iCloud (first
    account) 永远漏. 新逻辑应遍历 3 个 account 都搜.
    """
    accounts_stdout = (
        f"iCloud{FS}a@icloud.com{FS}0{RS}"
        f"Google{FS}b@gmail.com{FS}0{RS}"
        f"Chinatelecom{FS}c@chinatelecom.cn{FS}0{RS}"
    )
    # iCloud 0 命中, Google 0 命中, Chinatelecom 2 命中
    search_outputs = [
        "",   # iCloud
        "",   # Google
        f"100{FS}智能体列表{FS}fflijl@x.com{FS}2026-06-30T04:05:00{FS}1{FS}INBOX{RS}"
        f"101{FS}回复智能体{FS}ffhongyd@x.com{FS}2026-06-30T00:46:00{FS}0{FS}INBOX{RS}",
    ]
    with (
        patch.object(am, "_is_mail_running", return_value=True),
        patch.object(
            am, "_run_osascript",
            side_effect=_make_search_osa(accounts_stdout, search_outputs),
        ),
    ):
        msgs = AppleMailAdapter().search("智能体", account=None, limit=10)
    # 跨 3 账号, 第 3 账号 Chinatelecom 命中 2 封
    assert len(msgs) == 2
    assert {m.account for m in msgs} == {"Chinatelecom"}
    # date 倒序: 04:05 在 00:46 前
    assert msgs[0].subject == "智能体列表"
    assert msgs[1].subject == "回复智能体"


def test_search_explicit_account_unchanged_behaviour():
    """P3.5.153: account=str 路径不变 — 单账号 search 走 _search_as 一次."""
    accounts_stdout = f"Chinatelecom{FS}c@chinatelecom.cn{FS}0{RS}"
    search_stdout = f"99{FS}智能体{FS}x@x.com{FS}date1{FS}1{FS}INBOX{RS}"
    with (
        patch.object(am, "_is_mail_running", return_value=True),
        patch.object(
            am, "_run_osascript",
            side_effect=_make_search_osa(accounts_stdout, search_stdout),
        ),
    ):
        msgs = AppleMailAdapter().search("智能体", account="c@chinatelecom.cn")
    assert len(msgs) == 1


def test_search_wildcard_folder_routes_to_mailboxes_branch():
    """P3.5.152: folder='*' 跨所有 mailbox — 生成的 AS 走 mailboxes of acc 路径,
    不走老 `mailbox '*'` wildcard (Mail.app 返 -1728).

    注: account=None 走 _search_all_accounts 会按 date 倒序 sort, 测试不
    依赖 records 顺序, 只校验 folder set 包含 INBOX/Sent 都在 (P3.5.152
    fix 核心 — wildcard 真 mailbox name per-row, 不是 "*" 字面).
    """
    accounts_stdout = f"工作{FS}work@x.com{FS}1{RS}"
    # 模拟跨 2 mailbox 命中 2 封 — 每条第 6 列是 mailbox name 不是 "*"
    # date 用真 ISO 让 sort 行为稳定 (P3.5.153 sort by date desc)
    search_stdout = (
        f"100{FS}列表如下{FS}fflijl@x.com{FS}2026-06-30T04:05:00{FS}1{FS}INBOX{RS}"
        f"101{FS}回复{FS}ffhongyd@x.com{FS}2026-06-30T00:46:00{FS}0{FS}Sent{RS}"
    )
    captured: dict = {}
    with (
        patch.object(am, "_is_mail_running", return_value=True),
        patch.object(
            am, "_run_osascript",
            side_effect=_make_search_osa(accounts_stdout, search_stdout, captured),
        ),
    ):
        msgs = AppleMailAdapter().search("智能体", folder="*")
    assert len(msgs) == 2
    # folder 集合校 (不依赖 sort 顺序) — 核心: 每行 folder 反映真 mailbox name
    folders = {m.folder for m in msgs}
    assert folders == {"INBOX", "Sent"}
    # 断言生成的 AS script 含跨 mailbox 分支 (P3.5.152 fix 核心)
    script = captured.get("script", "")
    assert 'if folderName is "*"' in script, "AS 缺 wildcard 分支"
    assert "mailboxes of acc" in script, "AS 缺 跨 mailbox 遍历"


# ── AppleMailAdapter._resolve_account_name ──────────────


def test_resolve_account_name_default():
    """None → 走第一个 (BL-EMAIL-APPLEMAIL-AS-CTRLCHAR 5/18: is_default 永远 False,
    AS 'default account' 在 macOS Sequoia 挂; resolve None 退化成 fallback 第一个)."""
    accounts_stdout = (
        f"工作{FS}work@x.com{FS}0{RS}"
        f"个人{FS}me@x.com{FS}0{RS}"
    )
    with (
        patch.object(am, "_is_mail_running", return_value=True),
        patch.object(am, "_run_osascript", return_value=accounts_stdout),
    ):
        # 没默认账号 → 拿第一个
        assert AppleMailAdapter()._resolve_account_name(None) == "工作"


def test_resolve_account_name_by_email():
    """传 email 地址 → 映射回显示名."""
    accounts_stdout = f"工作{FS}work@x.com{FS}0{RS}"
    with (
        patch.object(am, "_is_mail_running", return_value=True),
        patch.object(am, "_run_osascript", return_value=accounts_stdout),
    ):
        assert AppleMailAdapter()._resolve_account_name("work@x.com") == "工作"


def test_resolve_account_name_not_found_raises():
    accounts_stdout = f"工作{FS}work@x.com{FS}0{RS}"
    with (
        patch.object(am, "_is_mail_running", return_value=True),
        patch.object(am, "_run_osascript", return_value=accounts_stdout),
    ):
        with pytest.raises(DataNotFoundError, match="找不到账号"):
            AppleMailAdapter()._resolve_account_name("ghost@nowhere.com")


# ── inbox.py factory: apple-mail 注册 ───────────────────


def test_factory_apple_mail_registered(monkeypatch):
    """工厂能识别 'apple-mail' 串.

    8/8: 加了 monkeypatch。这条测的是**工厂能不能 dispatch 到这个 adapter**,
    跟"跑测试的这台机器装没装 Mail.app"无关。而 8/8 起 `_get_adapter_explicit`
    会先探可用性 (见 adapters/apple_mail_probe.py), 于是在 CI / 没配 Mail.app 的
    开发机上它会抛 DataNotFoundError —— 测试跟着红, 但红的不是它要保的东西。

    把探测钉成 True, 让这条回到它原本的意图上。可用性本身由
    test_apple_mail_probe.py 单独覆盖。
    """
    monkeypatch.setattr(
        "catfish_email.adapters.apple_mail_probe.apple_mail_available",
        lambda: True,
    )
    from catfish_email.inbox import _get_adapter_explicit
    adapter = _get_adapter_explicit("apple-mail")
    assert adapter.name == "apple_mail"
    assert adapter.supports_drafts is True


def test_factory_apple_mail_跳过_当这台机器没在用它(monkeypatch):
    """探测说不可用 → 抛 DataNotFoundError, 且文案要说清楚原因.

    抛这个类型是刻意的: get_adapter() / get_all_adapters() 都已经 catch
    DataNotFoundError 并 continue, 所以自动候选那条路会**静默跳过**它 ——
    这正是要的效果 (Foxmail 单干的机器上不再有 apple_mail 的噪音)。
    """
    import pytest
    from catfish_email.adapters.base import DataNotFoundError
    monkeypatch.setattr(
        "catfish_email.adapters.apple_mail_probe.apple_mail_available",
        lambda: False,
    )
    from catfish_email.inbox import _get_adapter_explicit
    with pytest.raises(DataNotFoundError) as ei:
        _get_adapter_explicit("apple-mail")
    # 显式指定时要问得出"为什么跳过", 不能只说"不可用"
    assert "apple-mail 不可用" in str(ei.value)


def test_自动候选里_apple_mail_不可用时不阻塞_foxmail(monkeypatch):
    """8/8 鸿波实撞: Foxmail 好好的, 邮件页却一堆 apple_mail 的错误.

    这条钉的是那个行为: apple-mail 探测不过时, get_all_adapters() 要**安静地**
    只返回 Foxmail, 而不是把一个注定失败的 adapter 塞进候选让它每次调用都报错。
    """
    monkeypatch.setattr(
        "catfish_email.adapters.apple_mail_probe.apple_mail_available",
        lambda: False,
    )
    import platform
    monkeypatch.setattr(platform, "system", lambda: "Darwin")

    # Foxmail 装了的情形: 让它的构造成功, 返一个占位对象
    class _FakeFoxmail:
        name = "foxmail_mac"

    import catfish_email.inbox as inbox
    real = inbox._get_adapter_explicit

    def fake(client):
        if client == "foxmail-mac":
            return _FakeFoxmail()
        return real(client)

    monkeypatch.setattr(inbox, "_get_adapter_explicit", fake)
    adapters = inbox.get_all_adapters()
    names = [a.name for a in adapters]
    assert names == ["foxmail_mac"], f"apple_mail 不该在里面: {names}"


def test_factory_outlook_mac_alias_to_apple_mail():
    """老 'outlook-mac' alias 到 apple-mail (deprecation period)."""
    from catfish_email.inbox import _get_adapter_explicit
    adapter = _get_adapter_explicit("outlook-mac")
    assert adapter.name == "apple_mail"


# ════════════════════════════════════════════════════════════════════
#                BL-EMAIL-APPLEMAIL-FULL (5/18) — 补足 3 项
# ════════════════════════════════════════════════════════════════════


# ── bcc 字段透传 ────────────────────────────────────────


def test_create_draft_bcc_passes_through_to_as():
    """bcc=[...] 应该出现在 AS 模板 BCC 参数里."""
    captured: list[str] = []

    def fake_run(script, **_):
        captured.append(script)
        # list_accounts (走 _AS_LIST_ACCOUNTS) 返 1 个 default
        if "repeat with acc in every account" in script:
            return f"工作{FS}work@x.com{FS}1{RS}"
        # create_draft 返新 id
        if "make new outgoing message" in script:
            return "12345"
        return ""

    with (
        patch.object(am, "_is_mail_running", return_value=True),
        patch.object(am, "_run_osascript", side_effect=fake_run),
    ):
        AppleMailAdapter().create_draft(
            to=["a@x.com"],
            subject="hi",
            body="body",
            cc=["b@x.com"],
            bcc=["secret@x.com", "stealth@x.com"],
        )
    # 找 create_draft AS 模板
    draft_script = next(s for s in captured if "make new outgoing message" in s)
    assert 'set bccList to "secret@x.com,stealth@x.com"' in draft_script
    # cc 也保持
    assert 'set ccList to "b@x.com"' in draft_script


def test_create_draft_no_bcc_passes_empty_string():
    """没传 bcc → AS 收到空字符串, splitText 返空 list, repeat 跳过."""
    captured: list[str] = []

    def fake_run(script, **_):
        captured.append(script)
        if "repeat with acc in every account" in script:
            return f"工作{FS}work@x.com{FS}1{RS}"
        return "999"

    with (
        patch.object(am, "_is_mail_running", return_value=True),
        patch.object(am, "_run_osascript", side_effect=fake_run),
    ):
        AppleMailAdapter().create_draft(to=["a@x.com"], subject="", body="")
    draft_script = next(s for s in captured if "make new outgoing message" in s)
    assert 'set bccList to ""' in draft_script


# ── body_html 从 RFC822 source 抽 ────────────────────────


def test_extract_html_from_source_multipart():
    """multipart/alternative 邮件 → 抽 text/html 部分."""
    from catfish_email.adapters.apple_mail import _extract_html_from_source_file

    raw = (
        b"From: a@x.com\r\n"
        b"To: b@x.com\r\n"
        b"Subject: Test\r\n"
        b'Content-Type: multipart/alternative; boundary="BOUNDARY"\r\n'
        b"\r\n"
        b"--BOUNDARY\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n"
        b"\r\n"
        b"plain body\r\n"
        b"\r\n"
        b"--BOUNDARY\r\n"
        b"Content-Type: text/html; charset=utf-8\r\n"
        b"\r\n"
        b"<html><body><h1>hi</h1></body></html>\r\n"
        b"--BOUNDARY--\r\n"
    )
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".eml", delete=False) as tf:
        tf.write(raw)
        path = tf.name
    try:
        html = _extract_html_from_source_file(path)
        assert "<html>" in html
        assert "hi" in html
        assert "plain body" not in html  # 只抽 HTML 部分, 不要 plain
    finally:
        os.unlink(path)


def test_extract_html_from_source_plain_only_returns_empty():
    """只有 text/plain 没 HTML → 返空字符串 (caller 用 body_text)."""
    from catfish_email.adapters.apple_mail import _extract_html_from_source_file

    raw = (
        b"From: a@x.com\r\n"
        b"Subject: Test\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n"
        b"\r\n"
        b"just plain text\r\n"
    )
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".eml", delete=False) as tf:
        tf.write(raw)
        path = tf.name
    try:
        assert _extract_html_from_source_file(path) == ""
    finally:
        os.unlink(path)


def test_extract_html_from_missing_file_returns_empty():
    """文件不存在 (AS source 没写) → 返空, 不抛."""
    from catfish_email.adapters.apple_mail import _extract_html_from_source_file
    assert _extract_html_from_source_file("/tmp/no-such-file-xyz.eml") == ""


# ── EMLX fallback ───────────────────────────────────────


def test_parse_email_from_dir_name():
    """Mail.app 账号目录名 → email."""
    from catfish_email.adapters.apple_mail import _parse_email_from_dir_name

    assert _parse_email_from_dir_name(
        "IMAP-hongbo@example.com@imap.example.com",
    ) == "hongbo@example.com"
    # 简单 case
    assert _parse_email_from_dir_name("Exchange-x@y.com@mail.host") == "x@y.com"
    # 不匹配
    assert _parse_email_from_dir_name("MailData") is None


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
        am, "_detect_mail_data_dir", lambda: fake_mail,
    )
    adapter = AppleMailAdapter()
    assert adapter._enable_emlx_fallback_if_available() is True
    assert adapter._use_emlx_fallback is True
    assert adapter.supports_drafts is False


def test_no_emlx_fallback_when_no_mail_dir(monkeypatch):
    """探测不到 → fallback 不激活, supports_drafts 不变."""
    monkeypatch.setattr(am, "_detect_mail_data_dir", lambda: None)
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

    monkeypatch.setattr(am, "_detect_mail_data_dir", lambda: fake_mail)

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
    monkeypatch.setattr(am, "_detect_mail_data_dir", lambda: fake_mail)

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

    monkeypatch.setattr(am, "_detect_mail_data_dir", lambda: fake_mail)

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


# ════════════════════════════════════════════════════════════════════
#         BL-EMAIL-APPLEMAIL-AS-CTRLCHAR (5/18) 防回归
# ════════════════════════════════════════════════════════════════════


def test_no_raw_control_chars_in_as_templates():
    """静态检查: AS 字符串模板里不能内嵌 raw 0x1f / 0x1e.

    osascript 解析 AS string literal 里出现 ASCII control char (< 0x20, 除 \\t \\n \\r)
    会挂 -2741 "expected end of line, found class name". 老 f-string 把 FS/RS 直接
    interpolate 进去就踩这坑 (实盘 5/18 鸿波报 'catfish-email accounts 挂').

    所有 AS 模板必须在 AS 内部用 (ASCII character 31) 等表达式重建分隔符,
    Python f-string / .replace 不能注入 raw \\x1f / \\x1e.
    """
    for name, tmpl in [
        ("_AS_PING", am._AS_PING),
        ("_AS_LIST_ACCOUNTS", am._AS_LIST_ACCOUNTS),
        ("_AS_LIST_MESSAGES", am._AS_LIST_MESSAGES),
        ("_AS_GET_MESSAGE", am._AS_GET_MESSAGE),
        ("_AS_SEARCH", am._AS_SEARCH),
        ("_AS_CREATE_DRAFT", am._AS_CREATE_DRAFT),
    ]:
        for ch_code in range(0x20):
            if ch_code in (0x09, 0x0A, 0x0D):
                continue  # tab / LF / CR 允许
            assert chr(ch_code) not in tmpl, (
                f"{name} 内嵌 raw control char U+{ch_code:04X}, "
                f"会让 osascript 挂 (-2741). 用 (ASCII character N) 替代."
            )


# ── 8/6: _parse_thread_headers 从 email.parser 改成逐行扫 ──────────
#
# 起因: 鸿波本机 5 封实测, 3 封连 Message-ID 都读不出来, 而 rawHdrs 有 5000+ 字符。
# 真因: email.parser 见到「没有冒号又不以空白开头」的裸行就认为 header 段结束,
# 剩下全当 body。营销/通知邮件的 X- 头塞满 JSON / base64, 被中转 MTA 折一次
# 就会出现裸行, 折断点之后的三件套全读不到。
#
# 这坑没被发现是因为 message_id 上层有 `or rfc_msg_id` 兜底, 只有 in_reply_to /
# references 裸奔 → 现象是「Message-ID 有值、另两个永远空」, 而 isReplied 失败
# 时只是角标不亮, 跟「这封确实没人回」看起来一模一样。

from catfish_email.adapters.apple_mail import _parse_thread_headers as _pth


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


# ── 8/8: 探测不许把邮件整个搞挂 ───────────────────────────────
#
# 这两条钉的是一次真实的回归: 加可用性探测之后, Companion 里邮件页从"有噪音"
# 变成"拉取失败 + 一屏 traceback"。真因是 ~/Library/Mail 受 macOS TCC 保护,
# 没有完全磁盘访问权限的进程 is_dir() 过得去、iterdir() 抛 PermissionError,
# 而新调用点 (adapter 构造) 外面的 except 不含 OSError。


def test_没有磁盘权限时_探测返回不可用而不是抛(monkeypatch):
    """iterdir 抛 PermissionError → _detect_mail_data_dir 返 None, 不冒泡。"""
    from pathlib import Path
    from catfish_email.adapters import apple_mail_emlx

    class _Denied:
        def is_dir(self):
            return True          # 目录 stat 得到 —— 正是 TCC 下的表现

        def iterdir(self):
            raise PermissionError(13, "Operation not permitted")

    monkeypatch.setattr(Path, "home", staticmethod(lambda: Path("/fake")))
    monkeypatch.setattr(
        apple_mail_emlx, "Path",
        type("P", (), {"home": staticmethod(lambda: type("H", (), {
            "__truediv__": lambda self, _o: _Denied() if _o == "Mail" else self,
        })())}),
    )
    assert apple_mail_emlx._detect_mail_data_dir() is None


def test_探测炸了也不许让邮件不可用(monkeypatch):
    """探测抛任意异常 → 退回"可用", 保住老行为 (有噪音但能收信)。

    宁可回到有噪音, 也不能什么都收不到 —— 这正是 8/8 那次回归的教训。
    """
    import catfish_email.adapters.apple_mail_probe as probe

    def _boom():
        raise RuntimeError("探测自己炸了")

    monkeypatch.setattr(probe, "apple_mail_available", _boom)
    from catfish_email.inbox import _get_adapter_explicit
    adapter = _get_adapter_explicit("apple-mail")   # 不该抛
    assert adapter.name == "apple_mail"
