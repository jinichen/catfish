"""不去碰注定连不上的 Outlook COM。

# 这不是界面洁癖, 是一个真的把安装器搞挂的 bug

9/21 真机 bootstrap 日志:

    error: failed to remove file `...\\site-packages\\pywin32_system32/
           pythoncom311.dll`: 拒绝访问。 (os error 5)
    uv 安装 catfish-email 失败: 2
    Component check result: ["catfish-email: 安装/更新 catfish-email失败"]

链条:

  1. 邮件页每次挂载/重扫, discovery 给三个来源各起一个子进程
  2. outlook-win 那个 import pywin32 → **加载 pythoncom311.dll**
  3. 新版 Outlook 不提供 COM, 这次尝试注定失败 —— 但 DLL 已经加载进去了
  4. 同时 bootstrap 在升级 catfish-email, uv 要替换 pywin32, DLL 被占用
     → 装不上 → 整个邮件功能起不来

一次注定失败的扫描, 代价是把安装器搞挂。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from catfish_email.adapters import outlook_win as OW
from catfish_email.adapters.base import NotSupportedError


def test_precheck_runs_before_pywin32_is_imported(monkeypatch):
    """**这条是全部要点。**

    检查必须在 import pythoncom 之前。顺序反了的话 DLL 照样被加载,
    安装器照样挂 —— 而且所有别的测试都还是绿的, 因为功能上看不出区别。

    这里把 import 本身做成地雷: 只要被执行到就炸。
    """
    monkeypatch.setattr(OW.sys, "platform", "win32")
    monkeypatch.setattr(OW, "classic_outlook_registered", lambda: False)

    def landmine(name, *a, **kw):
        if name in ("pythoncom", "win32com.client", "pywintypes"):
            raise AssertionError(f"检查之前就 import 了 {name} —— DLL 已经被加载了")
        return real_import(name, *a, **kw)

    import builtins
    real_import = builtins.__import__
    monkeypatch.setattr(builtins, "__import__", landmine)

    with pytest.raises(NotSupportedError):
        OW._import_pywin32()


def test_message_tells_the_employee_what_to_do(monkeypatch):
    """"不支持"四个字没用。得说清楚是新版 Outlook 的问题, 以及该走哪条路。"""
    monkeypatch.setattr(OW.sys, "platform", "win32")
    monkeypatch.setattr(OW, "classic_outlook_registered", lambda: False)
    with pytest.raises(NotSupportedError) as got:
        OW._import_pywin32()
    text = str(got.value)
    assert "IMAP" in text, "得指一条真能走的路"
    assert "经典" in text or "OutlookForWindows" in text, "得说清楚为什么"


def test_registered_outlook_is_not_blocked(monkeypatch):
    """经典 Outlook 装着的机器上不能被这道检查拦掉。

    方向要对: 这是一道**只能否定不能肯定**的快速判断, 只拦注定失败的,
    不拦可能成功的。拦错了等于把还能用的客户端砍掉。
    """
    monkeypatch.setattr(OW.sys, "platform", "win32")
    monkeypatch.setattr(OW, "classic_outlook_registered", lambda: True)
    # 注册表说有 → 放行到 import。这里 pywin32 不一定装得上, 但**不能**是
    # NotSupportedError(没注册) 这个原因。
    try:
        OW._import_pywin32()
    except NotSupportedError as e:
        pytest.fail(f"经典 Outlook 注册了却被拦: {e}")
    except ImportError:
        pass  # 沙箱没有 pywin32, 正常


def test_non_windows_returns_false_without_touching_winreg():
    """非 Windows 上 winreg 根本不存在, 不能 import 就炸。"""
    assert OW.classic_outlook_registered() is False


def test_registry_errors_mean_not_registered(monkeypatch):
    """注册表读不了(权限/键不在) → 当成没注册。

    方向同上: 拿不准就跳过。跳过的代价是"Outlook 这条路没试", 而员工还有
    IMAP; 反过来硬试的代价是那个 DLL 被加载, 安装器挂掉。
    """
    monkeypatch.setattr(OW.sys, "platform", "win32")

    class FakeWinreg:
        HKEY_CLASSES_ROOT = 0
        HKEY_CURRENT_USER = 1

        @staticmethod
        def OpenKey(*a, **kw):
            raise OSError("拒绝访问")

    monkeypatch.setitem(sys.modules, "winreg", FakeWinreg)
    assert OW.classic_outlook_registered() is False


def test_a_registered_progid_is_detected(monkeypatch):
    """键在 + CLSID 非空 → True。"""
    monkeypatch.setattr(OW.sys, "platform", "win32")

    class FakeKey:
        def __enter__(self): return self
        def __exit__(self, *a): return False

    class FakeWinreg:
        HKEY_CLASSES_ROOT = 0
        HKEY_CURRENT_USER = 1

        @staticmethod
        def OpenKey(hive, sub):
            if hive == 0 and sub == r"Outlook.Application\CLSID":
                return FakeKey()
            raise OSError("没有这个键")

        @staticmethod
        def QueryValueEx(key, name):
            return "{0006F03A-0000-0000-C000-000000000046}", 1

    monkeypatch.setitem(sys.modules, "winreg", FakeWinreg)
    assert OW.classic_outlook_registered() is True


def test_empty_clsid_is_not_registered(monkeypatch):
    """键在但 CLSID 是空串 —— 卸载后留下的死键, 不算注册。"""
    monkeypatch.setattr(OW.sys, "platform", "win32")

    class FakeKey:
        def __enter__(self): return self
        def __exit__(self, *a): return False

    class FakeWinreg:
        HKEY_CLASSES_ROOT = 0
        HKEY_CURRENT_USER = 1
        OpenKey = staticmethod(lambda hive, sub: FakeKey())
        QueryValueEx = staticmethod(lambda key, name: ("", 1))

    monkeypatch.setitem(sys.modules, "winreg", FakeWinreg)
    assert OW.classic_outlook_registered() is False
