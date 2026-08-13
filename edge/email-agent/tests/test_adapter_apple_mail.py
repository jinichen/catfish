"""AppleMailAdapter 单测 —— AppleScript 主路径 (BL-EMAIL-APPLEMAIL-IMPL 5/18).

测试策略:
  - subprocess.run mock (沙箱没 osascript / Mail.app), 模拟 stdout / stderr / returncode
  - 验证: 数据解析 / 字段映射 / 错误分类 / FS/RS 分隔协议
  - 不测真 Mail.app — 那是 e2e, 手动 macOS 机器跑 (cli `catfish-email list`)

8/13 拆分 (源码同步拆): 本文件只留 adapter 类本身的行为。另两块在
  - test_apple_mail_osascript.py  osascript 执行层 + 输出解析的纯函数
  - test_apple_mail_emlx.py       EMLX 只读兜底路径
"""
from __future__ import annotations

import os
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from catfish_email.adapters import apple_mail as am
from catfish_email.adapters.apple_mail import FS, RS, AppleMailAdapter
from catfish_email.adapters.base import (
    Account,
    ClientNotRunningError,
    DataNotFoundError,
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


def test_split_files_stay_under_the_line():
    """三个文件都得在 800 行红线下 (CLAUDE.md §1)。

    apple_mail.py 上一次拆是 5/20 (1538 → 1045), 之后一路长回 1160 才被发现。
    钉在测试里, 下次越线是当场红而不是半年后。
    """
    from pathlib import Path
    base = Path(am.__file__).parent
    for fname in ("apple_mail.py", "apple_mail_osascript.py",
                  "apple_mail_emlx_path.py", "apple_mail_scripts.py",
                  "apple_mail_emlx.py"):
        n = len((base / fname).read_text(encoding="utf-8").splitlines())
        assert n < 800, f"{fname} {n} 行, 越过 800 红线"
