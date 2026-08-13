"""拆出去的每个模块，模块作用域里不许有用而未定义的名字。

# 这条测试是被两次真事故逼出来的，都出自同一个提交

`4cdf122`（8/8「拆 plugin.py 第一轮 —— 微信两块出去」）把函数搬进新模块，
但没搬它们在 plugin.py 模块作用域里用到的东西：

  · `plugin_weixin_zh.py` 用 `@functools.wraps` 却没 `import functools`
    → P28 **每次启动都失败**，日志里 19 次 `name 'functools' is not defined`，
      从 8/9 到 8/13 整整四天。表现是微信那边一直看到未翻译的英文串。

  · `plugin_wechat_qr.py` 用 `web.json_response(...)` 却没 `from aiohttp import web`，
    而 `_wechat_qr_sessions` 的**定义还留在 plugin.py 里**
    → 三个 handler 一跑就 NameError。**五天没人发现**，因为 P30 的 patch 只
      注册路由（日志 "routes registered ✓" 打了 19 次），处理器要等员工点
      「微信扫码」才第一次执行 —— 而这段时间没人点过（20 条日志全是注册，0 条调用）。

两次都不是逻辑写错，是**搬运时只盯着函数体**。这类错的共同点：

  · 不在 import 期暴露（名字在函数体里，Python 到调用时才解析）
  · 而调用可能很久之后才发生，甚至从来不发生
  · 于是"装载成功"的日志照打 ✓

pyflakes 一秒就能看出来。所以钉在这里，而不是靠人记得。

# 边界

`--select` 只看 undefined name（F821）。unused import 之类不管 —— 那是风格，
这里只挡"会在运行时炸"的。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_DIR = Path(__file__).resolve().parent.parent

#: 本插件包里所有会被 hermes 装载的 .py（不含 tests/）。
#: 用 glob 而不是写死名单 —— 写死的话新拆出来的模块不会自动被盯上，
#: 而"新拆出来的模块"恰恰就是这两次事故的来源。
def _modules() -> list[Path]:
    return sorted(
        p for p in _DIR.glob("*.py")
        if p.name != "__init__.py" and not p.name.startswith("test_")
    )


def test_module_discovery_actually_finds_modules():
    """先证明扫描是活的。

    glob 写错时返空列表，下面每条参数化测试会**一条都不生成**，pytest 报 0 passed
    而不是红 —— 那是最坏的绿。
    """
    mods = _modules()
    assert len(mods) >= 8, f"只找到 {len(mods)} 个模块, 扫描大概率坏了: {[m.name for m in mods]}"
    names = {m.name for m in mods}
    for must in ("plugin.py", "plugin_wechat_qr.py", "plugin_weixin_zh.py"):
        assert must in names, f"{must} 没被扫到"


@pytest.mark.parametrize("mod", _modules(), ids=lambda p: p.name)
def test_no_undefined_names(mod: Path):
    """F821 undefined name —— 就是 P28 / P30 那两次事故的错误码。"""
    pyflakes = pytest.importorskip("pyflakes", reason="本机没装 pyflakes")
    del pyflakes
    r = subprocess.run(
        [sys.executable, "-m", "pyflakes", str(mod)],
        capture_output=True, text=True, timeout=60,
    )
    bad = [ln for ln in r.stdout.splitlines() if "undefined name" in ln]
    assert not bad, (
        f"{mod.name} 里有用而未定义的名字 —— 拆模块时漏搬 import / 模块级状态了:\n"
        + "\n".join("  " + b for b in bad)
        + "\n\n（这类错不在装载期暴露，要等那段代码真被调用才炸；"
        "P30 的 handler 因此死了五天没人知道。）"
    )


def test_injected_names_are_declared_at_module_level():
    """靠 plugin.py 注入的依赖，必须在模块层先声明成 None。

    `plugin_codex_session` 的 resolver / model_authority 是 plugin.py 装载后写进去的。
    要是不先声明，pyflakes 会把它们报成 undefined（上面那条就会红），而更实际的
    问题是：读代码的人看不出这个模块需要外部接线。

    声明成 None + `_require_wiring()` 才构成完整契约。
    """
    src = (_DIR / "plugin_codex_session.py").read_text(encoding="utf-8")
    for name in ("resolver", "model_authority"):
        assert f"{name}: Any = None" in src, (
            f"plugin_codex_session 没在模块层声明注入位 {name}"
        )
    assert "_require_wiring" in src
