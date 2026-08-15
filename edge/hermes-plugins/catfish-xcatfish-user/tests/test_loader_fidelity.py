"""按 **hermes 真实的加载方式** 把这个 plugin 载一遍 (8/15 晚)。

# 病历: 一个我自己埋、当天晚上才炸出来的 bug

8/15 早上把 plugin.py 从 3226 行拆成 8 个模块。拆的时候给几个子模块写了个
延迟取兄弟模块的 helper:

    def _sib(name):
        from plugin import _import_sibling      # ← 裸 absolute import
        return _import_sibling(name)

**那正是这个 plugin 唯一不能用的写法。** 目录名带 dash
(`catfish-xcatfish-user`), hermes 用 `spec_from_file_location` +
`submodule_search_locations` 加载, sys.modules 里的名字是
`catfish-xcatfish-user.plugin` —— 没有任何模块叫 `plugin`。

讽刺在于 plugin.py 里的 `_import_sibling` 有三段 fallback, 存在理由就是这个
(5/28 那次「装好 9 天没工作」之后加的)。而我写的 helper 名义上"复用那三段",
实际用的是三段要绕开的那一段。

## 为什么隔了一整天才发现

hermes 进程从拆分之前就一直跑着, Python 把老模块缓存在内存。18:03 重启
gateway 之后才第一次加载新模块, 日志里立刻冒出 4 条:

    P23 inbound: picker ... failed: No module named 'plugin'
    P1 post-init apply_headers failed: No module named 'plugin'
    P6/P11 _create_agent post-init failed: No module named 'plugin'

三处全被 `except Exception` 包着, **只 warning 不抛**。员工侧看不出任何异常,
只是 header 注入 / picker 模型覆盖这些悄悄不干活了。要不是当晚为了别的事去翻
agent.log, 这个状态会一直挂到下次有人手工比对。

## 为什么原有的 262 条单测没抓住

它们都是 `sys.path.insert(0, PLUGIN_DIR)` 之后直接 `import plugin_runtime` ——
在那个环境里裸 import **是通的**。生产的加载方式不通。

**判据比真事窄**: 测试环境让被测代码走上了一条生产上不存在的路。

catfish-memory 那边 8/15 早上就补过同款的 test_loader_fidelity.py, 这个 plugin
漏了 —— 所以同一天的同一类 bug, 一个被挡住, 一个漏进生产。

# 这个文件做什么

不 mock 任何东西, 直接按 hermes 的方式 (spec_from_file_location +
submodule_search_locations) 把 plugin.py 载起来, 然后逐个调各模块的 `_sib`。
只要生产上会炸, 这里就会炸。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

PLUGIN_DIR = Path(__file__).resolve().parent.parent
PKG = PLUGIN_DIR.name          # "catfish-xcatfish-user" —— 带 dash, 这是全部问题的根

#: 各子模块实际会去取的兄弟。加新的 _sib 调用时顺手加一行。
SIB_CALLS = [
    ("plugin_runtime", "plugin_ctx"),
    ("plugin_runtime", "model_authority"),
    ("plugin_runtime", "resolver"),
    ("plugin_cors", "plugin_approval"),
    ("plugin_cors", "plugin_ctx"),
    ("plugin_misc", "plugin_ctx"),
    ("plugin_session", "resolver"),
]


@pytest.fixture(scope="module")
def hermes_loaded_plugin():
    """按 hermes 的方式加载 plugin.py, 返回 module 对象。

    关键有两条:

    1. `submodule_search_locations` —— 有它子模块才能走相对 import,
       没它就只能靠 fallback。hermes 是给的, 所以这里也给。

    2. ★ **把 PLUGIN_DIR 从 sys.path 里摘掉。**

       tests/conftest.py 第 19 行做了 `sys.path.insert(0, _plugin_dir)`,
       让老单测能直接 `import plugin`。但生产上 hermes **不会**这么干 ——
       裸 `import plugin` 在那里必然 ModuleNotFoundError。

       不摘的话这个 fixture 就是假的: 8/15 晚上第一版就没摘, 结果把 _sib
       改回坏版本之后, 下面 7 条参数化断言**全绿** —— 因为 conftest 让裸
       import 通了。测试环境替被测代码铺了一条生产上不存在的路。

       这跟当天在别处反复栽的是同一条 (判据比真事窄), 只是这次栽在
       "复现生产环境"这件事本身上。
    """
    import sys as _s
    _saved = list(_s.path)
    _d = str(PLUGIN_DIR)
    _s.path[:] = [x for x in _s.path if x not in (_d, _d + "/")]
    # 顺带把 conftest 可能已经载进来的裸名模块清掉, 否则 from plugin import
    # 会直接命中缓存, 摘 sys.path 也没用。
    _dropped = {k: _s.modules.pop(k) for k in list(_s.modules)
                if k == "plugin" or k.startswith("plugin_")}
    try:
        yield _load(PLUGIN_DIR)
    finally:
        _s.path[:] = _saved
        _s.modules.update(_dropped)


def _load(plugin_dir):
    spec = importlib.util.spec_from_file_location(
        PKG, str(PLUGIN_DIR / "__init__.py"),
        submodule_search_locations=[str(plugin_dir)],
    )
    pkg = importlib.util.module_from_spec(spec)
    sys.modules[PKG] = pkg
    try:
        spec.loader.exec_module(pkg)
    except Exception:
        pass          # __init__.py 可能要 hermes 环境, 不影响子模块加载

    spec2 = importlib.util.spec_from_file_location(
        f"{PKG}.plugin", str(plugin_dir / "plugin.py"))
    mod = importlib.util.module_from_spec(spec2)
    sys.modules[f"{PKG}.plugin"] = mod
    spec2.loader.exec_module(mod)
    return mod


def test_plugin本身能按hermes的方式载起来(hermes_loaded_plugin):
    m = hermes_loaded_plugin
    assert m.__package__ == PKG, (
        f"__package__ 是 {m.__package__!r}, 期望 {PKG!r} —— "
        "没有它子模块的相对 import 就走不了"
    )
    assert hasattr(m, "_import_sibling"), "plugin.py 少了 _import_sibling"


@pytest.mark.parametrize("mod_name,sib_name", SIB_CALLS,
                         ids=[f"{a}->{b}" for a, b in SIB_CALLS])
def test_每个子模块的_sib在生产加载方式下能用(hermes_loaded_plugin, mod_name, sib_name):
    """★★★ 这条就是 8/15 晚上那个 bug 的复现。

    修之前跑这条: ModuleNotFoundError: No module named 'plugin'
    """
    sib_owner = hermes_loaded_plugin._import_sibling(mod_name)
    got = sib_owner._sib(sib_name)
    assert got is not None
    assert sib_name in got.__name__, f"{mod_name}._sib({sib_name!r}) 拿回了 {got.__name__}"


def test_不许再出现裸的from_plugin_import():
    """★★ 源码级: 拦住写法退化。

    运行时那几条只在**加载方式对**的时候才测得出来。而 262 条老单测跑在
    sys.path 模式下, 裸 import 在那里是通的 —— 也就是说光靠运行时断言,
    有人改回裸写法时只有这一个文件会红, 别的全绿。直接看源码更硬。
    """
    bad = []
    for p in sorted(PLUGIN_DIR.glob("plugin*.py")):
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            s = line.strip()
            if s.startswith("#") or s.startswith("*"):
                continue                       # 注释里讲这个写法为什么不行是正当的
            if s == "from plugin import _import_sibling":
                # plugin_*.py 的 _sib 里作为**段 2 兜底**出现是允许的,
                # 但不能是唯一一条路 —— 段 1 (相对 import) 必须在它前面。
                src = p.read_text(encoding="utf-8")
                seg1 = 'import_module(f".{name}", package=__package__)'
                if seg1 not in src:
                    bad.append(f"{p.name}:{i} 只有裸 import, 没有相对 import 那段")
    assert not bad, (
        "\n".join(bad) + "\n\n"
        "这个 plugin 目录名带 dash, hermes 用 spec_from_file_location 加载, "
        "sys.modules 里没有叫 'plugin' 的模块。裸 import 会 ModuleNotFoundError, "
        "而调用点全被 except Exception 包着 —— 只 warning 不抛, 现场看不出来。"
    )


def test_同一个兄弟模块不会被载成两份(hermes_loaded_plugin):
    """双 module 对象是这一族最阴的失效: 两份各自自洽, 测试看不出来。

    _sib 段 1 走的是跟 plugin._import_sibling 段 1 同一条相对 import,
    所以命中 sys.modules 里同一个对象。这条钉住这件事。
    """
    m = hermes_loaded_plugin
    a = m._import_sibling("plugin_ctx")
    b = m._import_sibling("plugin_runtime")._sib("plugin_ctx")
    assert a is b, (
        f"plugin_ctx 被载成了两份: {a} vs {b}\n"
        "两份模块级状态各自飘, 而且自洽 —— 这种病现场根本查不出来。"
    )
