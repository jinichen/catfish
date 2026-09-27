"""Apple Mail 草稿 / 按 id 找信 (9/27)。

沙箱里没有 osascript, AppleScript 本身跑不了。这里钉两类东西:
  1. 脚本结构: 块配平、调用的 handler 都有定义且只定义一次、占位符全部替换掉
     —— 这几样错了 osascript 整段编译失败, macOS 上邮件整个不能用
  2. Python 侧: 草稿落哪个账号、返回哪个编号、改草稿什么时候删旧稿、发送带什么参数
"""
from __future__ import annotations

import inspect
import re
from pathlib import Path
from unittest.mock import patch

import pytest

from catfish_email.adapters import apple_mail as am
from catfish_email.adapters import apple_mail_drafts as drafts
from catfish_email.adapters import apple_mail_scripts as scripts
from catfish_email.adapters.apple_mail import FS, AppleMailAdapter
from catfish_email.adapters.base import DataNotFoundError, EmailAdapterError


@pytest.fixture(autouse=True)
def _isolated_home(monkeypatch, tmp_path):
    monkeypatch.setenv("CATFISH_HOME", str(tmp_path))


# ── AppleScript 结构 ────────────────────────────────────────

_STRING = re.compile(r'"(?:[^"\\]|\\.)*"')
_BLOCKS = ("tell", "try", "repeat", "if", "script")


def _code(line: str) -> str:
    """去掉字符串字面量和 -- 注释, 只留代码。"""
    no_str = _STRING.sub('""', line)
    return no_str.split("--", 1)[0].strip()


def _lint(script: str) -> tuple[list[str], set[str]]:
    """块配平检查。返回 (定义的 handler, `my x(` 调用到的 handler)。"""
    stack: list[str] = []
    defined: list[str] = []
    called: set[str] = set()
    for n, raw in enumerate(script.splitlines(), 1):
        code = _code(raw)
        if not code:
            continue
        called.update(re.findall(r"\bmy (\w+)\(", code))
        where = f"line {n}: {raw.strip()!r}"
        if code.startswith("on error"):
            assert stack and stack[-1] == "try", where
        elif m := re.match(r"on (\w+)\(", code):
            defined.append(m.group(1))
            stack.append("handler:" + m.group(1))
        elif m := re.fullmatch(r"end (\w+)", code):
            name = m.group(1)
            expected = name if name in _BLOCKS else "handler:" + name
            assert stack and stack[-1] == expected, f"{where} closes {stack[-1:]}"
            stack.pop()
        elif code.startswith("tell ") and " to " not in code:
            stack.append("tell")
        elif code == "try":
            stack.append("try")
        elif code.startswith("repeat"):
            stack.append("repeat")
        elif code.startswith("if ") and code.endswith(" then"):
            stack.append("if")
        elif (code.startswith("else if ") and code.endswith(" then")) or code == "else":
            assert stack and stack[-1] == "if", where
        elif code.startswith("script "):
            stack.append("script")
    assert not stack, f"没闭合: {stack}"
    return defined, called


_TEMPLATES = {
    "account_settings": scripts._AS_ACCOUNT_SETTINGS,
    "list": scripts._AS_LIST_MESSAGES,
    "list_bulk": scripts._AS_LIST_MESSAGES_BULK,
    "get": scripts._AS_GET_MESSAGE,
    "delete": scripts._AS_DELETE_MESSAGE,
    "mark_read": scripts._AS_MARK_READ,
    "search": scripts._AS_SEARCH,
    "create_draft": drafts._AS_CREATE_DRAFT,
    "send": drafts._AS_SEND_MESSAGE,
    "delete_draft": drafts._AS_DELETE_DRAFT,
}


@pytest.mark.parametrize("name", sorted(_TEMPLATES))
def test_script_blocks_balance_and_handlers_resolve(name):
    defined, called = _lint(_TEMPLATES[name])
    dupes = {h for h in defined if defined.count(h) > 1}
    assert not dupes, f"{name}: handler 重复定义 (osascript 编译失败): {dupes}"
    missing = called - set(defined)
    assert not missing, f"{name}: 调用了没定义的 handler {missing}"


@pytest.mark.parametrize("name", sorted(_TEMPLATES))
def test_no_by_id_reference_written_as_message_id(name):
    """9/28 真机: `set m to message id targetIdNum of mb` 编译报 -2741。

    Mail 的词典里 `message id` 是属性 (RFC Message-ID), 编译器把它当属性读,
    后面的变量就成了语法错 —— 整段读信 / 删信 / 标已读 / 存草稿 / 发送脚本
    全部编译不过 (1.0.44–1.0.48)。按编号取信要写成 `«class mssg» id N`。
    `message id of m` (取属性) 是对的, 不在此列。"""
    for n, raw in enumerate(_TEMPLATES[name].splitlines(), 1):
        code = _code(raw)
        assert not re.search(r"\bmessage id (?!of\b)\w", code), f"{name} line {n}: {raw.strip()}"


def test_lint_catches_what_it_should():
    """检查器自己得能抓到错, 否则上面全绿没意义。"""
    with pytest.raises(AssertionError):
        _lint('tell application "Mail"\n    try\n        get 1\nend tell\n')
    with pytest.raises(AssertionError):
        _lint("on a(x)\n    return x\nend b\n")
    _lint('tell application "Mail" to get 1\nif x then set y to 1\n')


def test_id_lookup_has_paths_that_do_not_depend_on_whose():
    """whose 在这台 Mac 的 Mail 上有据可查地漏信 (见 apple_mail_as_handlers 说明)。"""
    for name in ("get", "delete", "mark_read"):
        body = _TEMPLATES[name]
        assert "my findMessage(acc, targetIdStr)" in body, name
        assert "whose id is targetIdNum" in body  # 老路径仍在最前
    finder = scripts.AS_FIND_MESSAGE
    assert "«class mssg» id targetIdNum of mb" in finder
    assert "id of every message of mb" in finder


def test_list_survives_messages_without_date_received():
    """一封取不到 date received 不能让整个文件夹失败 (防御; 9/27 草稿箱实际是空的)。"""
    body = scripts._AS_LIST_MESSAGES
    assert "isoDate(date received" not in body
    assert "my msgDate(m)" in body
    assert "date sent of m" in body


def test_drafts_folder_is_found_under_gmail_parent():
    """Gmail 的草稿 / 已发送在 [Gmail] 下面: 顶层按名字找不到时要往下找。"""
    resolver = scripts.AS_RESOLVE_INBOX
    assert "name of mailboxes of acc" in resolver
    assert "of parentBox" in resolver
    assert '"已发邮件"' in resolver


# ── 起草 ───────────────────────────────────────────────────


def _run_recorder(create_out: str, *, delete_raises: Exception | None = None):
    calls: list[str] = []

    def fake_run(script, **_):
        calls.append(script)
        if "repeat with acc in every account" in script:
            return f"Google{FS}me@gmail.com{FS}1\x1eChinatelecom{FS}me@corp.cn{FS}0\x1e"
        if "make new outgoing message" in script and "save newMsg in mbx" in script:
            return create_out
        if "delete d" in script and "draftById" in script:
            if delete_raises:
                raise delete_raises
            return "OK"
        return "OK"

    return calls, fake_run


def _create(fake_run, **kwargs):
    with (
        patch.object(am, "_is_mail_running", return_value=True),
        patch.object(am, "_run_osascript", side_effect=fake_run),
    ):
        return AppleMailAdapter().create_draft(to=["a@x.cn"], subject="报价", body="正文", **kwargs)


def _assign(script: str, var: str) -> str:
    m = re.search(rf'set {var} to "((?:[^"\\]|\\.)*)"', script)
    assert m, var
    return m.group(1)


def test_new_draft_returns_the_saved_id_and_the_account_it_landed_in():
    calls, fake_run = _run_recorder(f"Chinatelecom{FS}2801{FS}4")
    draft_id = _create(fake_run)
    assert draft_id == "apple_mail|Chinatelecom|2801"
    script = calls[-1]
    # 没指定账号: 交给 Mail 的默认发件账号, 不再硬塞第一个账号 (Google)
    assert _assign(script, "accName") == ""
    assert not re.search(r"\{[A-Z_]+\}", script), "占位符没替换完"
    assert "visible:false" in script and "activate" not in script
    assert drafts.outgoing_for(draft_id) == "4"


def test_reply_draft_goes_to_the_account_of_the_original_mail():
    calls, fake_run = _run_recorder(f"Chinatelecom{FS}2802{FS}5")
    _create(fake_run, in_reply_to="apple_mail|Chinatelecom|2760")
    assert _assign(calls[-1], "accName") == "Chinatelecom"
    assert not any("repeat with acc in every account" in c for c in calls)


def test_explicit_account_address_is_resolved_to_the_mail_account_name():
    calls, fake_run = _run_recorder(f"Chinatelecom{FS}2803{FS}6")
    _create(fake_run, account="me@corp.cn")
    create_script = next(c for c in calls if "save newMsg in mbx" in c)
    assert _assign(create_script, "accName") == "Chinatelecom"


def test_editing_a_draft_removes_the_old_one_only_after_the_new_one_is_confirmed():
    calls, fake_run = _run_recorder(f"Chinatelecom{FS}2805{FS}7")
    new_id = _create(fake_run, replaces="apple_mail|Chinatelecom|2801")
    assert new_id == "apple_mail|Chinatelecom|2805"
    delete_script = calls[-1]
    assert "draftById(mbx, targetIdStr)" in delete_script
    assert _assign(delete_script, "targetIdStr") == "2801"
    # 旧稿只在草稿箱里找 —— 不走 findMessage 那种全账号查找
    assert "set d to my draftById(mbx, targetIdStr)" in delete_script
    assert "my findMessage" not in delete_script.split("end tell", 1)[0]


def test_unconfirmed_save_keeps_the_old_draft_and_returns_a_non_numeric_id():
    calls, fake_run = _run_recorder(f"Chinatelecom{FS}{FS}8")
    new_id = _create(fake_run, replaces="apple_mail|Chinatelecom|2801")
    assert new_id == "apple_mail|Chinatelecom|outgoing-8"
    assert not any("delete d" in c for c in calls[1:])


def test_old_draft_that_cannot_be_removed_does_not_lose_the_new_one():
    calls, fake_run = _run_recorder(
        f"Chinatelecom{FS}2806{FS}9", delete_raises=DataNotFoundError("gone"),
    )
    assert _create(fake_run, replaces="apple_mail|Chinatelecom|2801") == "apple_mail|Chinatelecom|2806"


def test_garbled_answer_from_mail_is_an_error_not_a_fake_id():
    _, fake_run = _run_recorder("12345")
    with pytest.raises(EmailAdapterError):
        _create(fake_run)


def test_cli_no_longer_refuses_to_edit_apple_mail_drafts():
    assert "replaces" in inspect.signature(AppleMailAdapter.create_draft).parameters


# ── 发送 ───────────────────────────────────────────────────


def test_send_uses_the_compose_window_the_draft_was_saved_from():
    drafts.remember_outgoing("apple_mail|Chinatelecom|2801", "4")
    seen: list[str] = []

    def fake_run(script, **_):
        seen.append(script)
        return "OK"

    with patch.object(am, "_run_osascript", side_effect=fake_run):
        AppleMailAdapter().send_message("apple_mail|Chinatelecom|2801")
    assert _assign(seen[0], "outIdStr") == "4"
    assert _assign(seen[0], "targetIdStr") == "2801"
    assert drafts.outgoing_for("apple_mail|Chinatelecom|2801") == ""  # 发完就忘


def test_send_checks_the_compose_window_subject_and_sender_before_sending():
    body = drafts._AS_SEND_MESSAGE
    assert "if omSubj is draftSubj then" in body
    assert '"SENDER_MISMATCH"' in body
    assert '"DRAFT_HAS_ATTACHMENTS"' in body
    # 重建只针对草稿箱里的信
    assert "set d to my draftById(mbx, targetIdStr)" in body


@pytest.mark.parametrize("code", ["DRAFT_HAS_ATTACHMENTS", "SENDER_MISMATCH", "SEND_FAILED"])
def test_send_refusals_are_explained(code):
    def fake_run(script, **_):
        raise EmailAdapterError(f"AppleScript 失败 (exit=1): execution error: {code} (8002)")

    with patch.object(am, "_run_osascript", side_effect=fake_run):
        with pytest.raises(EmailAdapterError) as info:
            AppleMailAdapter().send_message("apple_mail|Chinatelecom|2801")
    assert "AppleScript" not in str(info.value)


def test_send_gives_up_after_three_not_found():
    attempts: list[int] = []

    def fake_run(script, **_):
        attempts.append(1)
        raise DataNotFoundError("Mail 里找不到这条消息")

    with (
        patch.object(am, "_run_osascript", side_effect=fake_run),
        patch.object(drafts.time, "sleep"),
    ):
        with pytest.raises(DataNotFoundError):
            AppleMailAdapter().send_message("apple_mail|Chinatelecom|2801")
    assert len(attempts) == 3


# ── 列表 (9/27: 撤掉 emlx 索引, AppleScript 成批取) ──────────


def _list_run(bulk_out=None, bulk_error=None):
    calls: list[str] = []

    def fake_run(script, **_):
        calls.append(script)
        if "repeat with acc in every account" in script:
            return f"Google{FS}me@gmail.com{FS}1\x1e"
        if "messages lo thru total of mb" in script:
            if bulk_error:
                raise bulk_error
            return bulk_out
        return f"7{FS}逐封{FS}a@b.c{FS}2026-09-27T10:00:00{FS}1{FS}Inbox{FS}{FS}\x1e"

    return calls, fake_run


def _list(fake_run, **filt):
    from catfish_email.adapters.base import ListFilter

    with (
        patch.object(am, "_is_mail_running", return_value=True),
        patch.object(am, "_run_osascript", side_effect=fake_run),
    ):
        return AppleMailAdapter().list_messages(ListFilter(account="me@gmail.com", **filt))


def test_list_fetches_in_bulk_and_asks_mail_for_accounts_once():
    out = f"2753{FS}Thanks AI{FS}Reddit{FS}2026-09-27T09:00:00{FS}0{FS}Inbox{FS}<m@x>{FS}{FS}\x1e"
    calls, fake_run = _list_run(bulk_out=out)
    msgs = _list(fake_run)
    assert [m.id for m in msgs] == ["apple_mail|Google|2753"]
    assert not msgs[0].is_read
    assert sum("repeat with acc in every account" in c for c in calls) == 1
    assert not any("repeat with idx from total to 1" in c for c in calls)


def test_list_falls_back_to_one_by_one_when_bulk_fails():
    calls, fake_run = _list_run(bulk_error=EmailAdapterError("LIST_CHANGED (8101)"))
    msgs = _list(fake_run)
    assert [m.subject for m in msgs] == ["逐封"]
    assert any("repeat with idx from total to 1" in c for c in calls)


def test_unread_only_uses_the_one_by_one_script():
    calls, fake_run = _list_run(bulk_out="")
    _list(fake_run, unread_only=True)
    assert not any("messages lo thru total of mb" in c for c in calls)


def test_list_no_longer_reads_the_emlx_index_while_mail_is_running(monkeypatch, tmp_path):
    """8/21 的索引读侧在真机上从没走通 (V10 账号目录是 UUID), 撤掉后不许再碰磁盘。"""
    from catfish_email.adapters import apple_mail_emlx

    monkeypatch.setattr(apple_mail_emlx, "_detect_mail_data_dir", lambda: tmp_path)
    monkeypatch.setattr(am, "_detect_mail_data_dir", lambda: tmp_path)
    out = f"1{FS}s{FS}f{FS}{FS}1{FS}Inbox{FS}{FS}\x1e"
    _, fake_run = _list_run(bulk_out=out)
    assert [m.id for m in _list(fake_run)] == ["apple_mail|Google|1"]
    assert not hasattr(AppleMailAdapter, "_list_messages_indexed")


# ── 账号设置带入 (9/28: 配 IMAP 直连时不用再敲一遍服务器) ─────────


def test_account_settings_are_read_without_passwords(capsys):
    import json

    from catfish_email.cli_action import _cmd_mail_accounts

    rows = (
        f"Chinatelecom{FS}me@example.cn{FS}imap{FS}imap.example.cn{FS}me@example.cn{FS}993{FS}1\x1e"
        f"Google{FS}me@gmail.com{FS}imap{FS}imap.gmail.com{FS}{FS}993{FS}1\x1e"
        f"Old{FS}old@example.cn{FS}pop{FS}pop.example.cn{FS}old{FS}995{FS}1\x1e"
    )
    with (
        patch.object(am, "_is_mail_running", return_value=True),
        patch.object(am, "_run_osascript", return_value=rows),
    ):
        assert _cmd_mail_accounts([AppleMailAdapter()], None) == 0
    got = json.loads(capsys.readouterr().out)
    assert [g["host"] for g in got] == ["imap.example.cn", "imap.gmail.com", "pop.example.cn"]
    assert got[1]["user"] == "me@gmail.com", "用户名空着时用邮箱地址"
    assert [g["protocol"] for g in got] == ["imap", "imap", "pop"]
    assert got[0]["port"] == 993 and got[0]["client"] == "apple_mail"
    assert not any("password" in g for g in got)


def test_build_time_compile_check_covers_every_template():
    """打 Mac 包时 scripts/check_applescript.py 用 osacompile 编译每一段 (9/28)。
    这里 (Linux) 编译不了, 只核对它确实收齐了所有模板、占位符都有替换值 ——
    漏一段, 那一段的语法错就又只能等员工机上暴露。"""
    import importlib.util

    path = Path(__file__).resolve().parents[1] / "scripts" / "check_applescript.py"
    spec = importlib.util.spec_from_file_location("check_applescript", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    rendered = module.templates()
    names = {k.split(".", 1)[1] for k in rendered}
    for expected in ("_AS_GET_MESSAGE", "_AS_LIST_MESSAGES_BULK", "_AS_CREATE_DRAFT",
                     "_AS_SEND_MESSAGE", "_AS_DELETE_DRAFT", "_AS_ACCOUNT_SETTINGS"):
        assert expected in names
    assert all(not re.search(r"\{[A-Z_]+\}", v) for v in rendered.values())
