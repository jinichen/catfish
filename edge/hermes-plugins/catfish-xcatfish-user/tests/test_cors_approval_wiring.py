"""plugin_cors 读 plugin_approval 的可变全局, 必须走**模块属性**。

# 守的是什么

`_chat_approval_middleware` 的生命周期是:

    plugin_approval.py 顶层:  _chat_approval_middleware = None
    P15.2 装完之后:           global _chat_approval_middleware; 赋成真中间件
    plugin_cors 挂中间件链时:  读它, 非 None 就 append 进 aiohttp 的 middlewares

8/15 把 CORS 和审批拆成两个模块之前, 这三步在同一个文件里, `global` 天然可见。
拆开之后, 如果 plugin_cors 写成

    from plugin_approval import _chat_approval_middleware   # ← 错

拿到的是 **import 那一刻的快照 (None)**, P15.2 后来的 global 赋值它永远看不见。
后果: 审批中间件不进链 —— 员工点"同意"没反应, 而**启动日志一切正常, 没有任何
报错**。这跟今天反复踩的是同一条: `from X import name` 建的是新绑定不是别名。

# 两条测法, 缺一不可

  · 运行时: 改了 plugin_approval 的值, plugin_cors 那边读得到 → 证明机制通
  · 源码级: plugin_cors 里不许出现对这个名字的 from-import → 证明**将来**
    也不会被改回快照写法 (运行时那条测不出"写法退化", 因为退化后
    _approval_mod() 路径仍然对)
"""
from __future__ import annotations

import re
from pathlib import Path

_DIR = Path(__file__).resolve().parent.parent


def test_cors读得到approval后来赋的值():
    """运行时: 模块属性访问能看见 global 赋值。"""
    import plugin_approval
    import plugin_cors

    sentinel = object()
    old = plugin_approval._chat_approval_middleware
    try:
        plugin_approval._chat_approval_middleware = sentinel
        got = plugin_cors._approval_mod()._chat_approval_middleware
        assert got is sentinel, (
            "plugin_cors 读到的不是 plugin_approval 当前的值 —— "
            "多半是拿了快照, 审批中间件会静默不进链"
        )
    finally:
        plugin_approval._chat_approval_middleware = old


def test_cors源码里不许from_import那个可变全局():
    """源码级: 防将来被改回快照写法。

    运行时那条测不出这个 —— 就算有人加了 from-import, 只要 _approval_mod()
    那条路还在, 运行时断言照样绿。要拦"写法退化"只能直接看源码。
    """
    src = (_DIR / "plugin_cors.py").read_text(encoding="utf-8")
    # 只剔行注释; 模块 docstring 里提到这个写法是**在讲为什么不能这么写**,
    # 所以下面用"必须是 import 语句"的形状来判, 不是裸子串。
    bad = [
        ln for ln in src.splitlines()
        if re.match(r"\s*from\s+\.?plugin_approval\s+import\b", ln)
        and "_chat_approval_middleware" in ln
    ]
    assert not bad, (
        f"plugin_cors 用 from-import 拿了 _chat_approval_middleware: {bad}\n"
        "那是快照, 看不见 P15.2 的 global 赋值。改成 _approval_mod()._chat_approval_middleware"
    )


def test_三个可变全局的持有者没搬家():
    """`_chat_approval_middleware` 必须**只**由 plugin_approval 定义。

    两个模块各定义一份的话, cors 读自己那份 (永远 None), approval 写自己那份,
    表面全绿实际断链 —— 跟今天 catfish-memory 那个"同一份源码两个 module 对象"
    是同型的病。
    """
    owners = [
        f.name for f in sorted(_DIR.glob("plugin*.py"))
        if re.search(r"^_chat_approval_middleware\s*=", f.read_text(encoding="utf-8"), re.M)
    ]
    assert owners == ["plugin_approval.py"], (
        f"_chat_approval_middleware 的模块级定义出现在 {owners} —— 只该有 plugin_approval.py"
    )
