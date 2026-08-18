"""浏览器 helper 必须从真的定义它的模块拿 (8/18)。

# 病历: 两个工具从上线起就没成功过一次

`catfish_recognize_captcha` 和 `catfish_browser_locate` 里都写着:

    from . import catfish_tools
    catfish_tools._import_playwright()             ← 不存在
    catfish_tools._connect_playwright_browser(p)   ← 不存在

`catfish_tools.py` 只从 `catfish_tools_browser` re-export 了 7 个**公开**函数
(browser_click / browser_fill / browser_goto / …), 这两个下划线私有的一个都没导。
所以必然 AttributeError。

## 为什么没人发现

AttributeError 不是 RuntimeError, 穿过 `except RuntimeError` 那层, 被最外面的
`except Exception` 吞成一条 logger.warning, 函数返 None。上层看到 None 就报:

    "无法截 selector='#captchaImg' 的图. selector 写错? 页面没开 browser?
     Playwright 死了? 先 catfish_browser_snapshot 查."

**三个猜测全是错的方向。** 8/18 排查时靠这句话先怀疑页面改版、又怀疑 selector
过期, 还据此判定"EIS skill 整条链都坏了" —— 全是被这句误导。

真相是: 同一个页面同一个 selector, `catfish_browser_screenshot` 每次都成功
(它直接用本模块的函数), 这两个每次都失败。

跟 8/15 那几个是同一族: **except 把失败变成了沉默**。只是这个更狠 ——
它不只沉默, 还主动给了三个错误的排查方向。

## 代价

SOUL.md 的浏览器铁律三步里坏了两步:

    找按钮优先 find_by_text            ← 通 (在 re-export 名单里)
    找不到走 catfish_browser_locate     ← 坏
    验证码必走 catfish_recognize_captcha ← 坏

# 这个文件钉什么

  1. 两个 helper 在**它们真正被 import 的那个模块**里拿得到
  2. 不许再退回"从 catfish_tools 拿"的写法
  3. 连不上浏览器时的失败原因要带上真异常, 不能只给猜测
"""
from __future__ import annotations

import ast
import importlib
import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(_SRC))

#: 用到这两个 helper 的模块。加新的调用点时顺手加一行。
_CONSUMERS = ["catfish_tool_bridge.recognize_captcha",
              "catfish_tool_bridge.browser_locate"]
_HELPERS = ["_import_playwright", "_connect_playwright_browser"]


@pytest.mark.parametrize("name", _HELPERS)
def test_helper_定义在_catfish_tools_browser(name: str):
    """★ 先钉住"它到底在哪" —— 后面几条都建立在这个前提上。"""
    mod = importlib.import_module("catfish_tool_bridge.catfish_tools_browser")
    assert hasattr(mod, name), f"catfish_tools_browser 里没有 {name}"


@pytest.mark.parametrize("name", _HELPERS)
def test_catfish_tools_上没有这两个_所以不能从那儿拿(name: str):
    """★★★ 这条就是那个 bug 的复现。

    修之前 recognize_captcha / browser_locate 都从 catfish_tools 拿这两个,
    而它压根没 re-export —— 每次调用必 AttributeError。

    ⚠ 这条**故意断言"没有"**。哪天有人给 catfish_tools 补上 re-export,
      这条会红 —— 那时去掉它就行, 但要顺手确认两个 consumer 到底从哪拿的。
      重点不是"catfish_tools 该不该有", 是"别再靠一个没验证过的 re-export"。
    """
    mod = importlib.import_module("catfish_tool_bridge.catfish_tools")
    assert not hasattr(mod, name), (
        f"catfish_tools 现在有 {name} 了 —— 这条测试的前提变了, 回去确认 "
        "recognize_captcha / browser_locate 是从哪个模块拿的"
    )


@pytest.mark.parametrize("consumer", _CONSUMERS)
def test_不许再从_catfish_tools_拿这两个_helper(consumer: str):
    """★★ 源码级拦写法退化。

    运行时那条只在真去调浏览器时才炸, 而单测环境没有 Chrome, 谁也不会去调 ——
    所以光靠运行时断言, 有人改回旧写法时全绿。直接看源码更硬。
    """
    path = _SRC / (consumer.replace(".", "/") + ".py")
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    bad = [
        f"{path.name}:{n.lineno} catfish_tools.{n.attr}"
        for n in ast.walk(tree)
        if isinstance(n, ast.Attribute)
        and n.attr in _HELPERS
        and isinstance(n.value, ast.Name)
        and n.value.id == "catfish_tools"
    ]
    assert not bad, (
        "\n".join(bad) + "\n\n"
        "catfish_tools 没有 re-export 这两个私有函数, 这么写必 AttributeError, "
        "而且会被 except 吞成'截图失败 / selector 写错', 排查方向全被带偏。"
        "直接 from .catfish_tools_browser import 它们。"
    )


@pytest.mark.parametrize("consumer", _CONSUMERS)
def test_连浏览器那层不许只catch_RuntimeError(consumer: str):
    """★★ 只 catch RuntimeError 会让"代码写错了"伪装成"浏览器连不上"。

    AttributeError / ImportError 都不是 RuntimeError —— 8/18 那个 bug 正是
    这么溜过去的: 真异常穿过这层, 被更外面的 except Exception 吞成 warning,
    上层只看到"截图失败"。
    """
    path = _SRC / (consumer.replace(".", "/") + ".py")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    bad = []
    for n in ast.walk(tree):
        if not isinstance(n, ast.Try):
            continue
        calls = {
            c.func.id
            for c in ast.walk(n)
            if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
        }
        if "_connect_playwright_browser" not in calls:
            continue
        for h in n.handlers:
            if isinstance(h.type, ast.Name) and h.type.id == "RuntimeError":
                bad.append(f"{path.name}:{h.lineno} except RuntimeError")
    assert not bad, (
        "\n".join(bad) + "\n\n"
        "连浏览器那层要 catch Exception —— 只 catch RuntimeError 的话, "
        "AttributeError/ImportError 这类会被误报成'连不上浏览器'。"
    )


def test_成功路径也必须返两元组():
    """★★ 上面那条只走失败路径 —— 沙箱没有 Chrome, 成功分支根本进不去。

    ⚠ 第一版只有失败路径那条, 做变异 (把成功分支的 `return png, ""` 改成
      `return png`) 时它**绿着过去了**: 那条断言的是失败时的返回, 成功分支
      改坏了它一点感觉都没有。而调用方 `png_bytes, why = _capture_via_playwright(...)`
      在成功时会直接 ValueError 解包失败 —— 线上第一次截图成功就炸。

    所以直接看 AST: 函数里每一条 return 都得是两元组 (或 None 那种不返值的)。
    """
    src = (_SRC / "catfish_tool_bridge/recognize_captcha.py").read_text(encoding="utf-8")
    fn = next(
        n for n in ast.walk(ast.parse(src))
        if isinstance(n, ast.FunctionDef) and n.name == "_capture_via_playwright"
    )
    bad = [
        f"第 {r.lineno} 行 return {ast.unparse(r.value)}"
        for r in ast.walk(fn)
        if isinstance(r, ast.Return) and r.value is not None
        and not (isinstance(r.value, ast.Tuple) and len(r.value.elts) == 2)
    ]
    assert not bad, (
        "\n".join(bad) + "\n\n"
        "_capture_via_playwright 每条 return 都得是 (bytes|None, 原因)。"
        "调用方是 `png_bytes, why = _capture_via_playwright(...)` —— "
        "少返一个值就是解包 ValueError, 而且只在**截图成功**时才炸。"
    )


def test_截图失败要把真异常带给上层():
    """★★ 老版只给三个猜测 (selector 写错 / 没开浏览器 / Playwright 死了),

    而 8/18 真因一个都不在里面。失败原因必须原样上浮。
    """
    from catfish_tool_bridge.recognize_captcha import _capture_via_playwright

    got = _capture_via_playwright("#definitely-not-there")
    assert isinstance(got, tuple) and len(got) == 2, (
        f"应该返 (bytes|None, 原因), 得到 {type(got).__name__} —— "
        "只返 None 的话上层就只能猜"
    )
    png, why = got
    assert png is None
    assert why and ":" in why, f"原因要带异常类型和消息, 得到 {why!r}"
