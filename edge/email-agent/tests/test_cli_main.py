"""catfish-email CLI 单元测试 (focus: cross-adapter 韧性 + JSON 字段).

不跑真 osascript / 真 sqlite — 用 fake adapter 测 _cmd_list 的合并 / 错误兜底 /
adapter 字段注入逻辑.

5/18 BL-EMAIL-LIST-ADAPTER-FIELD + BL-EMAIL-ACCOUNT-CROSS-ADAPTER-CRASH.
"""
from __future__ import annotations

import argparse
import io
import json
import sys
from typing import Sequence

import pytest

from catfish_email.__main__ import (
    _cmd_delete,
    _cmd_list,
    _cmd_mark_read,
    _cmd_read,
    _cmd_send,
    _msg_to_dict,
)
from catfish_email.adapters.base import (
    Account,
    DataNotFoundError,
    EmailAdapter,
    EmailAdapterError,
    ListFilter,
    Message,
    NotSupportedError,
)


class _FakeAdapter(EmailAdapter):
    """可控的内存 adapter — list_messages 返预设邮件 / 预设异常."""

    def __init__(
        self,
        *,
        name: str,
        accounts: Sequence[Account] = (),
        messages: Sequence[Message] = (),
        raise_on_list: Exception | None = None,
        raise_on_mark_read: Exception | None = None,
        raise_on_delete: Exception | None = None,
        raise_on_send: Exception | None = None,
    ) -> None:
        self._name = name
        self._accounts = list(accounts)
        self._messages = list(messages)
        self._raise = raise_on_list
        self._raise_mark = raise_on_mark_read
        self._raise_delete = raise_on_delete
        self._raise_send = raise_on_send
        self.mark_read_calls: list[tuple[str, bool]] = []
        self.delete_calls: list[str] = []
        self.send_calls: list[str] = []

    @property
    def name(self) -> str:
        return self._name

    def list_accounts(self) -> list[Account]:
        return list(self._accounts)

    def list_messages(self, filt: ListFilter) -> list[Message]:
        if self._raise is not None:
            raise self._raise
        return list(self._messages)

    def search(self, q, *, account=None, folder="Inbox", limit=30):
        return []

    def read_message(self, message_id):
        for m in self._messages:
            if m.id == message_id:
                return m
        raise DataNotFoundError(message_id)

    def create_draft(self, draft):
        raise EmailAdapterError("not implemented in fake")

    def mark_read(self, message_id, *, read: bool = True) -> None:
        if self._raise_mark is not None:
            raise self._raise_mark
        self.mark_read_calls.append((message_id, read))

    def delete_message(self, message_id) -> None:
        if self._raise_delete is not None:
            raise self._raise_delete
        self.delete_calls.append(message_id)

    def send_message(self, message_id) -> None:
        if self._raise_send is not None:
            raise self._raise_send
        self.send_calls.append(message_id)


def _make_args(**kw):
    """构造 _cmd_list 用的 args. 默认值跟 argparse default 对齐."""
    return argparse.Namespace(
        folder=kw.get("folder", "Inbox"),
        account=kw.get("account"),
        since=kw.get("since"),
        until=kw.get("until"),
        sender=kw.get("sender"),
        subject=kw.get("subject"),
        body=kw.get("body"),
        unread=kw.get("unread", False),
        has_attachments=kw.get("has_attachments", False),
        limit=kw.get("limit", 50),
        json=kw.get("json", True),
    )


def _make_msg(subject: str, account: str, date: str) -> Message:
    return Message(
        id=f"id-{subject}",
        account=account,
        folder="Inbox",
        subject=subject,
        sender="x@y.com",
        date=date,
    )


# ============================================================
# BL-EMAIL-LIST-ADAPTER-FIELD: JSON 输出含 adapter 字段
# ============================================================


def test_msg_to_dict_includes_adapter_name():
    """_msg_to_dict(m, adapter_name='apple_mail') → dict 含 adapter 字段"""
    m = _make_msg("hello", "a@x.com", "2026-05-18T10:00:00")
    d = _msg_to_dict(m, adapter_name="apple_mail")
    assert d["adapter"] == "apple_mail"
    assert d["subject"] == "hello"
    assert d["account"] == "a@x.com"


def test_msg_to_dict_omits_adapter_when_none():
    """老调用方不传 adapter_name → 不带 adapter key (向后兼容)"""
    m = _make_msg("hello", "a@x.com", "2026-05-18T10:00:00")
    d = _msg_to_dict(m)
    assert "adapter" not in d
    assert d["subject"] == "hello"


def test_cmd_list_json_includes_adapter_per_message(capsys):
    """跨 adapter list, JSON 输出每条都带正确 adapter 字段, jq group_by 能区分"""
    a = _FakeAdapter(
        name="apple_mail",
        accounts=[Account("iCloud", "alice@icloud.com", is_default=True)],
        messages=[_make_msg("from-apple", "alice@icloud.com", "2026-05-18T10:00:00")],
    )
    b = _FakeAdapter(
        name="foxmail_mac",
        accounts=[Account("hongbo@qq.com", "hongbo@qq.com", is_default=True)],
        messages=[_make_msg("from-foxmail", "hongbo@qq.com", "2026-05-18T09:00:00")],
    )
    rc = _cmd_list([a, b], _make_args(json=True))
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    by_adapter = {item["adapter"]: item["subject"] for item in out}
    assert by_adapter == {
        "apple_mail": "from-apple",
        "foxmail_mac": "from-foxmail",
    }


def test_cmd_list_json_table_sorted_by_date_desc(capsys):
    """跨 adapter date 降序合并, adapter 字段对每条都正确 (不是 first-wins 错配)"""
    a = _FakeAdapter(
        name="apple_mail",
        accounts=[Account("iCloud", "alice@icloud.com", is_default=True)],
        messages=[_make_msg("old", "alice@icloud.com", "2026-01-01T00:00:00")],
    )
    b = _FakeAdapter(
        name="foxmail_mac",
        accounts=[Account("hongbo@qq.com", "hongbo@qq.com", is_default=True)],
        messages=[_make_msg("new", "hongbo@qq.com", "2026-05-18T12:00:00")],
    )
    rc = _cmd_list([a, b], _make_args(json=True))
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    # date desc 排
    assert [item["subject"] for item in out] == ["new", "old"]
    # 但 adapter 字段跟着 msg 走, 不是 sort 后乱串
    assert out[0]["adapter"] == "foxmail_mac"
    assert out[1]["adapter"] == "apple_mail"


# ============================================================
# BL-EMAIL-ACCOUNT-CROSS-ADAPTER-CRASH: 单 adapter 挂不阻塞其他
# ============================================================


def test_cmd_list_single_adapter_data_not_found_does_not_crash(capsys):
    """显式 --account, 一个 adapter 没这账号 → 跳过, 另一个正常返"""
    a = _FakeAdapter(
        name="apple_mail",
        accounts=[Account("iCloud", "alice@icloud.com", is_default=True)],
        messages=[_make_msg("from-apple", "alice@icloud.com", "2026-05-18T10:00:00")],
    )
    b = _FakeAdapter(
        name="foxmail_mac",
        accounts=[],
        messages=[],
        raise_on_list=DataNotFoundError("Foxmail 没账号 'Google'"),
    )
    rc = _cmd_list([a, b], _make_args(account="alice@icloud.com", json=True))
    assert rc == 0
    captured = capsys.readouterr()
    out = json.loads(captured.out)
    assert [item["subject"] for item in out] == ["from-apple"]
    # 错误信息进 stderr
    assert "foxmail_mac" in captured.err
    assert "没账号" in captured.err


def test_cmd_list_unexpected_filenotfound_is_caught(capsys):
    """adapter 漏 FileNotFoundError 不在 EmailAdapterError 体系 → 兜底层接住, 不挂"""
    a = _FakeAdapter(
        name="apple_mail",
        accounts=[Account("iCloud", "alice@icloud.com", is_default=True)],
        messages=[_make_msg("ok", "alice@icloud.com", "2026-05-18T10:00:00")],
    )
    b = _FakeAdapter(
        name="foxmail_mac",
        accounts=[Account("hongbo@qq.com", "hongbo@qq.com", is_default=True)],
        raise_on_list=FileNotFoundError("/path/to/some.db"),
    )
    rc = _cmd_list([a, b], _make_args(json=True))
    assert rc == 0
    captured = capsys.readouterr()
    out = json.loads(captured.out)
    assert [item["subject"] for item in out] == ["ok"]
    assert "FileNotFoundError" in captured.err
    assert "foxmail_mac" in captured.err


def test_cmd_list_all_adapters_fail_still_returns_empty_not_crash(capsys):
    """所有 adapter 都挂 → 返空 list 不爆, errors 全进 stderr"""
    a = _FakeAdapter(
        name="apple_mail",
        accounts=[Account("iCloud", "alice@icloud.com", is_default=True)],
        raise_on_list=DataNotFoundError("无数据"),
    )
    b = _FakeAdapter(
        name="foxmail_mac",
        accounts=[Account("hongbo@qq.com", "hongbo@qq.com", is_default=True)],
        raise_on_list=FileNotFoundError("/missing.db"),
    )
    rc = _cmd_list([a, b], _make_args(json=True))
    assert rc == 0
    captured = capsys.readouterr()
    out = json.loads(captured.out)
    assert out == []
    # 两个 adapter 错误都在 stderr
    assert "apple_mail" in captured.err
    assert "foxmail_mac" in captured.err


# ============================================================
# 5/18 BL-EMAIL-MARK-READ: read 自动标已读 + mark-read subcommand
# ============================================================


def _make_read_args(msg_id: str, *, mark_read: bool = True, json_out: bool = True):
    return argparse.Namespace(id=msg_id, json=json_out, mark_read=mark_read)


def _make_mark_args(msg_id: str, *, unread: bool = False, json_out: bool = True):
    return argparse.Namespace(id=msg_id, unread=unread, json=json_out)


def test_cmd_read_auto_marks_as_read(capsys):
    """默认 mark_read=True: 读完后 adapter.mark_read 被调一次, JSON 反映新状态"""
    m = Message(
        id="apple_mail|alice@icloud.com|123",
        account="alice@icloud.com",
        folder="Inbox",
        subject="hi",
        sender="x@y.com",
        date="2026-05-18T10:00:00",
        is_read=False,
    )
    a = _FakeAdapter(name="apple_mail", messages=[m])
    rc = _cmd_read([a], _make_read_args(m.id, mark_read=True))
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["is_read"] is True  # 输出反映新状态
    assert a.mark_read_calls == [(m.id, True)]


def test_cmd_read_no_mark_read_flag_skips(capsys):
    """--no-mark-read → 不调 adapter.mark_read, JSON is_read 保持原值"""
    m = Message(
        id="apple_mail|alice@icloud.com|123",
        account="alice@icloud.com",
        folder="Inbox",
        subject="hi",
        sender="x@y.com",
        date="2026-05-18T10:00:00",
        is_read=False,
    )
    a = _FakeAdapter(name="apple_mail", messages=[m])
    rc = _cmd_read([a], _make_read_args(m.id, mark_read=False))
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["is_read"] is False
    assert a.mark_read_calls == []


def test_cmd_read_already_read_message_skips_mark(capsys):
    """已读邮件再读不需要再标 (省一次 AS / sqlite write)"""
    m = Message(
        id="apple_mail|alice@icloud.com|123",
        account="alice@icloud.com",
        folder="Inbox",
        subject="hi",
        sender="x@y.com",
        date="2026-05-18T10:00:00",
        is_read=True,  # 已读
    )
    a = _FakeAdapter(name="apple_mail", messages=[m])
    rc = _cmd_read([a], _make_read_args(m.id, mark_read=True))
    assert rc == 0
    assert a.mark_read_calls == []


def test_cmd_read_mark_failure_warns_but_returns_success(capsys):
    """标已读挂 → stderr 警告, 但正文已读取, 不让 read 命令返非零"""
    m = Message(
        id="apple_mail|alice@icloud.com|123",
        account="alice@icloud.com",
        folder="Inbox",
        subject="hi",
        sender="x@y.com",
        date="2026-05-18T10:00:00",
        is_read=False,
    )
    a = _FakeAdapter(
        name="apple_mail",
        messages=[m],
        raise_on_mark_read=EmailAdapterError("AS 挂了"),
    )
    rc = _cmd_read([a], _make_read_args(m.id, mark_read=True))
    assert rc == 0  # 正文读到了就算成功
    captured = capsys.readouterr()
    out = json.loads(captured.out)
    assert out["is_read"] is False  # mark 失败 → 输出仍是 unread
    assert "标已读失败" in captured.err
    assert "apple_mail" in captured.err


def test_cmd_mark_read_routes_by_prefix(capsys):
    """mark-read subcommand 按 id 前缀路由到正确 adapter"""
    a = _FakeAdapter(name="apple_mail")
    b = _FakeAdapter(name="foxmail_mac")
    rc = _cmd_mark_read(
        [a, b],
        _make_mark_args("foxmail-mac|hongbo@qq.com|999"),
    )
    assert rc == 0
    # 只 foxmail_mac 被调
    assert a.mark_read_calls == []
    assert b.mark_read_calls == [("foxmail-mac|hongbo@qq.com|999", True)]


def test_cmd_mark_read_unread_flag_passes_false(capsys):
    """--unread → mark_read(read=False)"""
    a = _FakeAdapter(name="apple_mail")
    rc = _cmd_mark_read(
        [a],
        _make_mark_args("apple_mail|alice@icloud.com|123", unread=True),
    )
    assert rc == 0
    assert a.mark_read_calls == [("apple_mail|alice@icloud.com|123", False)]


def test_cmd_mark_read_missing_id_falls_through_adapters(capsys):
    """无前缀 / 不存在的 id: 逐 adapter try, 全 DataNotFoundError → 返 3"""
    a = _FakeAdapter(name="apple_mail", raise_on_mark_read=DataNotFoundError("nope"))
    b = _FakeAdapter(name="foxmail_mac", raise_on_mark_read=DataNotFoundError("nope"))
    rc = _cmd_mark_read([a, b], _make_mark_args("unknown-id-without-prefix"))
    assert rc == 3
    captured = capsys.readouterr()
    assert "邮件不存在" in captured.err


# ============================================================
# 5/18 BL-EMAIL-MARK-READ-MSG / BL-EMAIL-ID-FORMAT-UX: 文案与 ValueError 兜底
# ============================================================


def test_cmd_mark_read_prefix_routed_error_msg_says_specific_adapter(capsys):
    """按前缀路由到 foxmail_mac 但 id 不存在 → 报"[foxmail_mac] 邮件不存在", 不是"跨 1 个"."""
    a = _FakeAdapter(name="apple_mail")  # 不被路由到
    b = _FakeAdapter(name="foxmail_mac", raise_on_mark_read=DataNotFoundError("mailid 1 不在"))
    rc = _cmd_mark_read([a, b], _make_mark_args("foxmail-mac|hongbo@qq.com|1"))
    assert rc == 3
    captured = capsys.readouterr()
    assert "[foxmail_mac]" in captured.err
    assert "邮件不存在" in captured.err
    # 重点: 不再有"跨 1 个客户端"误导文案
    assert "跨 1 个" not in captured.err


def test_cmd_mark_read_value_error_gives_format_hint(capsys):
    """adapter 漏 ValueError (id 格式) → 友好提示让员工 list 拷, 不是 traceback"""
    a = _FakeAdapter(
        name="apple_mail",
        raise_on_mark_read=ValueError("Apple Mail message_id 格式错: '...'"),
    )
    rc = _cmd_mark_read([a], _make_mark_args("apple_mail|...|..."))
    assert rc == 2  # 区分于 3 (不存在)
    captured = capsys.readouterr()
    assert "id 格式不对" in captured.err
    assert "catfish-email list" in captured.err  # 引导员工正确做法


def test_cmd_read_null_id_friendly_message(capsys):
    """5/18 BL-EMAIL-ID-EMPTY-SENTINEL: shell `$(... | jq -r '.[0].id')` 空数组返
    "null" → CLI 给友好提示而不是去 adapter loop 然后报"非法 id"."""
    a = _FakeAdapter(name="apple_mail")
    rc = _cmd_read([a], _make_read_args("null", mark_read=False))
    assert rc == 2
    captured = capsys.readouterr()
    assert "邮件 id 不能为空" in captured.err
    assert "jq -r" in captured.err  # 引导员工修 shell 用法


def test_cmd_mark_read_null_id_friendly_message(capsys):
    a = _FakeAdapter(name="apple_mail")
    rc = _cmd_mark_read([a], _make_mark_args("null"))
    assert rc == 2
    captured = capsys.readouterr()
    assert "邮件 id 不能为空" in captured.err


def test_cmd_read_empty_id_friendly_message(capsys):
    """空字符串也走友好路径"""
    a = _FakeAdapter(name="apple_mail")
    rc = _cmd_read([a], _make_read_args("", mark_read=False))
    assert rc == 2


# ============================================================
# 5/18 BL-EMAIL-DELETE: delete subcommand
# ============================================================


def _make_delete_args(msg_id: str, *, json_out: bool = True):
    return argparse.Namespace(id=msg_id, json=json_out)


def test_cmd_delete_routes_by_prefix_and_succeeds(capsys):
    """按 id 前缀路由到正确 adapter, 删除成功返 0 + ok JSON"""
    a = _FakeAdapter(name="apple_mail")
    b = _FakeAdapter(name="foxmail_mac")
    rc = _cmd_delete(
        [a, b],
        _make_delete_args("apple_mail|alice@icloud.com|123"),
    )
    assert rc == 0
    assert a.delete_calls == ["apple_mail|alice@icloud.com|123"]
    assert b.delete_calls == []
    out = json.loads(capsys.readouterr().out)
    assert out == {
        "adapter": "apple_mail",
        "id": "apple_mail|alice@icloud.com|123",
        "deleted": True,
        "ok": True,
    }


def test_cmd_delete_foxmail_not_supported_returns_4(capsys):
    """Foxmail Mac 不支持 delete → NotSupportedError → 返码 4 + 引导文案"""
    a = _FakeAdapter(
        name="foxmail_mac",
        raise_on_delete=NotSupportedError("Foxmail Mac 不支持自动删除"),
    )
    rc = _cmd_delete([a], _make_delete_args("foxmail-mac|hongbo@qq.com|999"))
    assert rc == 4  # 区分 1/2/3
    captured = capsys.readouterr()
    assert "不支持自动删除" in captured.err
    assert "请去客户端" in captured.err  # 引导用户去 Foxmail 自己删


def test_cmd_delete_unknown_id_returns_3(capsys):
    """id 不存在 → DataNotFoundError → 返 3"""
    a = _FakeAdapter(
        name="apple_mail",
        raise_on_delete=DataNotFoundError("没这邮件"),
    )
    rc = _cmd_delete([a], _make_delete_args("apple_mail|alice@icloud.com|999"))
    assert rc == 3
    captured = capsys.readouterr()
    assert "邮件不存在" in captured.err
    assert "[apple_mail]" in captured.err  # 按前缀路由的清晰提示


def test_cmd_delete_null_id_returns_2(capsys):
    """null sentinel → 返 2 (跟 read/mark-read 一致)"""
    a = _FakeAdapter(name="apple_mail")
    rc = _cmd_delete([a], _make_delete_args("null"))
    assert rc == 2
    assert "邮件 id 不能为空" in capsys.readouterr().err


def test_cmd_delete_value_error_returns_2(capsys):
    """id 格式错 ValueError → 友好提示 + 返 2"""
    a = _FakeAdapter(
        name="apple_mail",
        raise_on_delete=ValueError("id 格式错"),
    )
    rc = _cmd_delete([a], _make_delete_args("apple_mail|...|..."))
    assert rc == 2
    captured = capsys.readouterr()
    assert "id 格式不对" in captured.err
    assert "list --json" in captured.err


def test_cmd_delete_all_adapters_dont_support_returns_4(capsys):
    """无前缀 + 全 adapter NotSupportedError → 返 4"""
    a = _FakeAdapter(
        name="apple_mail",
        raise_on_delete=NotSupportedError("Apple Mail EMLX fallback 不支持"),
    )
    b = _FakeAdapter(
        name="foxmail_mac",
        raise_on_delete=NotSupportedError("Foxmail 不支持"),
    )
    rc = _cmd_delete([a, b], _make_delete_args("no-prefix-id-here"))
    assert rc == 4


def test_cmd_read_value_error_gives_format_hint(capsys):
    """_cmd_read 同样兜 ValueError"""
    a = _FakeAdapter(
        name="apple_mail",
        accounts=[Account("iCloud", "alice@icloud.com", is_default=True)],
    )
    # _FakeAdapter.read_message 在 messages 里找不到 → 抛 DataNotFoundError;
    # 真实 adapter 漏 ValueError 我们用 monkey-patch 模拟:
    def boom(_):
        raise ValueError("Apple Mail message_id 格式错")
    a.read_message = boom  # type: ignore[method-assign]
    rc = _cmd_read([a], _make_read_args("apple_mail|...|...", mark_read=False))
    assert rc == 2
    captured = capsys.readouterr()
    assert "id 格式不对" in captured.err
    assert "list --json" in captured.err


# ============================================================
# 5/18 BL-EMAIL-COMPOSE-SEND: send subcommand
# ============================================================


def _make_send_args(msg_id: str, *, json_out: bool = True):
    return argparse.Namespace(id=msg_id, json=json_out)


def test_cmd_send_routes_by_prefix_and_succeeds(capsys):
    """按 id 前缀路由到 Apple Mail, 发送成功返 0"""
    a = _FakeAdapter(name="apple_mail")
    b = _FakeAdapter(name="foxmail_mac")
    rc = _cmd_send([a, b], _make_send_args("apple_mail|alice@x.com|drafts-1"))
    assert rc == 0
    assert a.send_calls == ["apple_mail|alice@x.com|drafts-1"]
    assert b.send_calls == []
    out = json.loads(capsys.readouterr().out)
    assert out["sent"] is True
    assert out["adapter"] == "apple_mail"


def test_cmd_send_foxmail_not_supported_returns_4(capsys):
    """Foxmail 不支持 send → NotSupportedError → 返 4 + 友好引导"""
    a = _FakeAdapter(
        name="foxmail_mac",
        raise_on_send=NotSupportedError("Foxmail Mac 不支持自动发送"),
    )
    rc = _cmd_send([a], _make_send_args("foxmail-mac|x@x.com|99"))
    assert rc == 4
    captured = capsys.readouterr()
    assert "不支持自动发送" in captured.err
    assert "请去客户端" in captured.err


def test_cmd_send_unknown_id_returns_3(capsys):
    """id 不存在 → DataNotFoundError → 返 3"""
    a = _FakeAdapter(
        name="apple_mail",
        raise_on_send=DataNotFoundError("没这草稿"),
    )
    rc = _cmd_send([a], _make_send_args("apple_mail|x@x.com|999"))
    assert rc == 3
    captured = capsys.readouterr()
    assert "草稿不存在" in captured.err


def test_cmd_send_null_id_returns_2(capsys):
    """null sentinel → 返 2"""
    a = _FakeAdapter(name="apple_mail")
    rc = _cmd_send([a], _make_send_args("null"))
    assert rc == 2
    assert "草稿 id 不能为空" in capsys.readouterr().err


def test_cmd_send_value_error_returns_2(capsys):
    """id 格式错 ValueError → 返 2 + 友好提示"""
    a = _FakeAdapter(
        name="apple_mail",
        raise_on_send=ValueError("id 格式错"),
    )
    rc = _cmd_send([a], _make_send_args("apple_mail|...|..."))
    assert rc == 2
    captured = capsys.readouterr()
    assert "id 格式不对" in captured.err
