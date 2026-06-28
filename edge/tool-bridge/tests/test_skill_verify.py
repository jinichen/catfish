"""P3.5.128 E1 — verify gate β 单测.

Audit-first: 这里只测 skill_verify.verify_frozen_script 的 4 个分支
(语法 / 找 render_fn / call 第一参数 / 白名单), 不动 freeze_skill 整体 pipeline
(那是 test_skill_freeze.py 的事).
"""
from __future__ import annotations

import pytest

from catfish_tool_bridge.skill_verify import verify_frozen_script


ALLOWED = frozenset({
    "catfish_browser_goto",
    "catfish_browser_click",
    "catfish_browser_fill",
    "catfish_recognize_captcha",
})


def _wrap(body: str, fn_name: str = "eis_login") -> str:
    """生成一个最小可 verify 的 script.py 模板."""
    return f'''"""auto"""
def _call(t, a):
    return {{}}


def render_{fn_name}(username: str = "x"):
{body}
'''


def test_valid_script_passes():
    body = """    _call("catfish_browser_goto", {"url": "x"})
    _call("catfish_browser_fill", {"selector": "#a", "text": "b"})"""
    r = verify_frozen_script(_wrap(body), ALLOWED, "eis_login")
    assert r.ok, r.errors
    assert r.call_count == 2
    assert r.found_render_fn is True
    assert r.errors == []


def test_syntax_error_caught():
    bad = 'def x(:\n    pass'  # broken
    r = verify_frozen_script(bad, ALLOWED, "eis_login")
    assert r.ok is False
    assert any("语法错" in e for e in r.errors)


def test_missing_render_fn():
    body = '    _call("catfish_browser_goto", {"url": "x"})'
    # 用错的 fn_name 触发找不到
    r = verify_frozen_script(_wrap(body, "eis_login"), ALLOWED, "wrong_name")
    assert r.ok is False
    assert any("找不到入口函数" in e and "render_wrong_name" in e for e in r.errors)
    assert r.found_render_fn is False


def test_unknown_tool_caught():
    body = '    _call("catfish_evil_tool", {})'
    r = verify_frozen_script(_wrap(body), ALLOWED, "eis_login")
    assert r.ok is False
    assert any("catfish_evil_tool" in e and "白名单" in e for e in r.errors)


def test_non_constant_first_arg_caught():
    # 第一参数是变量, 不是字符串常量 — 模板永远不应该出这种, 但 verify 要 catch
    body = """    tool_name = "catfish_browser_goto"
    _call(tool_name, {"url": "x"})"""
    r = verify_frozen_script(_wrap(body), ALLOWED, "eis_login")
    assert r.ok is False
    assert any("第一参数必须是字符串常量" in e for e in r.errors)


def test_empty_render_fn_caught():
    # render fn 空壳, 没 _call — 模板没出 step
    body = "    pass"
    r = verify_frozen_script(_wrap(body), ALLOWED, "eis_login")
    assert r.ok is False
    assert any("空壳" in e for e in r.errors)
    assert r.call_count == 0


def test_multiple_errors_collected():
    # 一次报全 — 不 short-circuit
    body = """    _call("catfish_evil_a", {})
    _call("catfish_evil_b", {})"""
    r = verify_frozen_script(_wrap(body), ALLOWED, "eis_login")
    assert r.ok is False
    # 两个 evil tool 都报
    assert sum(1 for e in r.errors if "白名单" in e) == 2


def test_call_missing_args():
    body = "    _call()"
    r = verify_frozen_script(_wrap(body), ALLOWED, "eis_login")
    assert r.ok is False
    assert any("缺第一参数" in e for e in r.errors)


def test_realistic_eis_login_passes():
    """模拟真 freeze 出来的 eis-login script 结构."""
    body = '''    _call("catfish_browser_goto", {"url": "http://eis.ffcs.cn"})
    _call("catfish_browser_fill", {"selector": "#name", "text": username})
    _call("catfish_browser_fill", {"selector": "#pwd", "secret_ref": "keychain://eis"})
    _call("catfish_recognize_captcha", {"selector": "#captchaImg"})
    _call("catfish_browser_fill", {"selector": "#captcha", "text": "captcha_value"})
    _call("catfish_browser_click", {"selector": "div.button-login"})'''
    r = verify_frozen_script(_wrap(body), ALLOWED, "eis_login")
    assert r.ok, r.errors
    assert r.call_count == 6
