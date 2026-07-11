"""Outlook COM adapter 骨架单测 (W2 BL-EMAIL-OUTLOOK-WIN 7/11).

测试策略:
    - 沙箱 / macOS CI runner 无 pywin32 / Outlook, 覆盖 platform guard 分支
    - Windows 侧 e2e (list_accounts / list_messages 拉真邮件) 留 Week 3 手测
      (需要装了 Outlook 的 Windows 机器, GitHub Action `runs-on: windows-latest`
      + Outlook 环境搭建成本高, Week 3 手动跑一次)
    - 骨架里的 pack/unpack_id + iso 日期转换是纯函数, 跨平台可测

覆盖范围 (W3 骨架):
    - 非 Windows 平台 import OutlookWinAdapter 立即抛 NotSupportedError
    - inbox.get_adapter('outlook-win') 在非 Win 走 fallback 分支 (不 raise)
    - _pack_id / _unpack_id 3 段 id 序列化对齐 apple_mail 风格
    - _iso_date_to_utc 生成 DASL urn:schemas 期望的 xsd:dateTime 格式

Week 3 集成阶段补:
    - mock win32com.client.Dispatch 抛 pywintypes.com_error → ClientNotRunningError
    - mock ns.Accounts.Item 返 fake Account 对象 → list_accounts 返 Account list
    - mock ns.GetItemFromID 抛 com_error → DataNotFoundError
    - mock ns.GetDefaultFolder + Items.Restrict → list_messages 返 Message list
"""
from __future__ import annotations

import sys

import pytest


# ─── platform guard (macOS CI 主要跑这几个) ─────────────────────


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="平台 guard 分支 (Win 上不 raise); Win 走 W3 集成 e2e",
)
def test_outlook_win_adapter_raises_on_non_windows():
    """非 Windows 平台 __init__ 立即抛 NotSupportedError.

    inbox.get_adapter 自动 fallback 依赖这个 (NotSupportedError 继承
    NotImplementedError, 被 inbox.py:52 except 捕获).
    """
    from catfish_email.adapters.base import NotSupportedError
    from catfish_email.adapters.outlook_win import OutlookWinAdapter

    with pytest.raises(NotSupportedError, match="仅支持 Windows"):
        OutlookWinAdapter()


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="fallback 逻辑测试, Win 上会真装 Outlook adapter",
)
def test_inbox_get_adapter_outlook_win_falls_through_on_non_windows():
    """显式指定 'outlook-win' 在非 Win 应抛 NotSupportedError.

    (不测 platform.system=='Windows' 分支, 那是 get_adapter 自动候选逻辑,
     现有 test 已覆盖.)
    """
    from catfish_email.adapters.base import NotSupportedError
    from catfish_email.inbox import _get_adapter_explicit

    with pytest.raises(NotSupportedError, match="仅支持 Windows"):
        _get_adapter_explicit("outlook-win")


# ─── 纯函数: ID 序列化跨平台可测 ─────────────────────────


def _import_module_bypass_platform_check():
    """在非 Win 平台 import outlook_win 模块本身 (不实例化 Adapter).

    模块 top-level 只有常量 + _import_pywin32 (函数定义, 不执行), safe to import.
    """
    from catfish_email.adapters import outlook_win  # noqa: F401
    return outlook_win


def test_pack_id_three_segment_format():
    """_pack_id 走 'outlook_win|<smtp>|<entry_id>' 3 段, 对齐 apple_mail 前缀风格."""
    ow = _import_module_bypass_platform_check()
    packed = ow.OutlookWinAdapter._pack_id("hongbo@company.com", "0000000012345678")
    assert packed == "outlook_win|hongbo@company.com|0000000012345678"


def test_unpack_id_roundtrip():
    ow = _import_module_bypass_platform_check()
    packed = ow.OutlookWinAdapter._pack_id("a@b.com", "ENTRY123")
    smtp, entry_id = ow.OutlookWinAdapter._unpack_id(packed)
    assert smtp == "a@b.com"
    assert entry_id == "ENTRY123"


def test_unpack_id_rejects_2_segment_legacy_format():
    """Outlook adapter 全新, 不接老 2 段 format (apple_mail 才有历史 id 兼容包袱)."""
    ow = _import_module_bypass_platform_check()
    with pytest.raises(ValueError, match="格式错"):
        ow.OutlookWinAdapter._unpack_id("account|entry_id_only")


def test_unpack_id_rejects_wrong_prefix():
    ow = _import_module_bypass_platform_check()
    with pytest.raises(ValueError, match="格式错"):
        ow.OutlookWinAdapter._unpack_id("apple_mail|a@b|X")


# ─── DASL 日期转换 (locale-independent, 关键 blocker W3-3) ─────


def test_iso_date_to_utc_bare_date_gets_utc_midnight():
    """裸日期 '2026-04-26' → '2026-04-26T00:00:00Z' (DASL xsd:dateTime 期望)."""
    ow = _import_module_bypass_platform_check()
    assert ow.OutlookWinAdapter._iso_date_to_utc("2026-04-26") == "2026-04-26T00:00:00Z"


def test_iso_date_to_utc_with_tz_normalizes_to_utc():
    """带时区的 ISO datetime 规范化到 UTC 输出."""
    ow = _import_module_bypass_platform_check()
    # +08:00 → UTC (减 8h)
    assert ow.OutlookWinAdapter._iso_date_to_utc(
        "2026-04-26T10:00:00+08:00"
    ) == "2026-04-26T02:00:00Z"


def test_iso_date_to_utc_with_z_normalizes():
    ow = _import_module_bypass_platform_check()
    assert ow.OutlookWinAdapter._iso_date_to_utc(
        "2026-04-26T15:30:00Z"
    ) == "2026-04-26T15:30:00Z"


def test_iso_date_to_utc_invalid_returns_as_is():
    """无法解析的字符串原样返 — Outlook 侧 Restrict 会自己抛 com_error."""
    ow = _import_module_bypass_platform_check()
    assert ow.OutlookWinAdapter._iso_date_to_utc("garbage") == "garbage"


# ─── folder enum 常量 ─────────────────────────────────


def test_folder_aliases_covers_apple_mail_convention():
    """FOLDER_ALIASES 至少覆盖 apple_mail / foxmail-mac 外部约定的 4 个 folder."""
    ow = _import_module_bypass_platform_check()
    assert "Inbox" in ow.FOLDER_ALIASES
    assert "Sent" in ow.FOLDER_ALIASES
    assert "Drafts" in ow.FOLDER_ALIASES
    # Trash / Deleted 二选一都行
    assert "Trash" in ow.FOLDER_ALIASES or "Deleted" in ow.FOLDER_ALIASES
    # Microsoft OlDefaultFolders enum 稳定值
    assert ow.FOLDER_ALIASES["Inbox"] == 6
    assert ow.FOLDER_ALIASES["Sent"] == 5
    assert ow.FOLDER_ALIASES["Drafts"] == 16


# ─── W3 集成阶段占位测试 (Windows 侧 mock) ──────────────────


@pytest.mark.skipif(
    sys.platform != "win32",
    reason="Windows-only e2e, 需装了 Outlook 的机器. W3 集成阶段跑.",
)
class TestOutlookWinAdapterWindows:
    """占位: Win 平台 mock win32com.client.Dispatch 走真流程. W3 集成阶段补."""

    def test_placeholder(self):
        pytest.skip("W3 集成阶段补: mock Dispatch → list_accounts")
