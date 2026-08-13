"""P18 压缩拆出 plugin_compress 之后的结构不变式。

拆红线的第三块。这块跟前两块不同的地方：**handler 和它的路由注册不在同一个
模块里**，而这正是最容易在下一次重构里被搞坏的点。

    plugin_compress._handle_compress_session_stream   ← handler 本体
    plugin.py  _patched_app_init 里的 add_post        ← 路由注册

之所以分开，是 6/17 22:16 踩过的坑：老写法在 wrap connect 里 add_post，那时
`runner.setup()` → `app.freeze()` 已经跑完，撞 `Cannot register a resource into
frozen router`。改成在 `Application.__init__` 之后注册才对得上时机。

两边靠 P7 stash 的 adapter 实例接起来（`request.app["_catfish_apiserver_adapter"]`
→ `adapter._handle_compress_session_stream(request)`），所以 handler 住哪个模块
都行 —— 但**必须有人把它 attach 到 APIServerAdapter 类上**，那是
`_patch_p18_compress_endpoint` 干的活。这条链断任何一环，症状都是"点压缩没反应"
或者 503，而装载日志照样 ✓。
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_DIR))

import plugin_compress as pcz  # noqa: E402

_SRC = (_DIR / "plugin_compress.py").read_text(encoding="utf-8")
_PLUGIN_SRC = (_DIR / "plugin.py").read_text(encoding="utf-8")

SYMBOLS = ["_handle_compress_session_stream", "_patch_p18_compress_endpoint"]


def _code_only(src: str) -> str:
    """去掉所有 docstring 和注释，只留代码。

    ⚠ 这个 helper 是被同一个坑咬第三次之后加的：写"源码里不许出现 X"这类检查时，
    docstring 和注释里**正当地**写着 X（它们在解释为什么不许出现 X），于是注释
    写得越清楚，测试越容易红在一个跟本意完全相反的地方。

    box_parser 那次（test_plugin_delegates_instead_of_reimplementing）已经踩过，
    这次写 P18 又踩了两条。所以抽成 helper，别再靠记性。
    """
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)) and node.body:
            first = node.body[0]
            if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)):
                node.body = node.body[1:] or [ast.Pass()]
    return ast.unparse(ast.fix_missing_locations(tree))


_CODE = _code_only(_SRC)
_PLUGIN_CODE = _code_only(_PLUGIN_SRC)


@pytest.mark.parametrize("name", SYMBOLS)
def test_symbol_moved_here(name):
    assert hasattr(pcz, name), f"{name} 没搬过来"


def test_plugin_keeps_only_reexports():
    for node in ast.parse(_PLUGIN_SRC).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            assert node.name not in SYMBOLS, f"plugin.py 里还定义着 {node.name}"


def test_no_wiring_slot_because_no_sibling_deps():
    """这块不该有注入位 —— 它不依赖任何兄弟模块。

    多余的注入位不是无害的：它让读代码的人以为这里有外部契约，下次拆别的块时
    照抄一份没用的 `_require_wiring`，久了就没人分得清哪个是真的。
    """
    assert "_require_wiring" not in _CODE
    assert "model_authority" not in _CODE
    assert "plugin_compress.model_authority" not in _PLUGIN_CODE


# ── ★ handler 和路由注册分居两处，链条要完整 ────────────────────

def test_patch_attaches_handler_to_the_adapter_class():
    """`_patch_p18_compress_endpoint` 必须把 handler 挂到 APIServerAdapter 上。

    路由注册那段调的是 `adapter._handle_compress_session_stream(...)` —— 实例
    方法。没人 attach 的话就是 AttributeError，而那要等员工真点"压缩"才发生。
    """
    node = next(n for n in ast.parse(_SRC).body
                if getattr(n, "name", None) == "_patch_p18_compress_endpoint")
    body = ast.get_source_segment(_SRC, node)
    assert "APIServerAdapter._handle_compress_session_stream" in body
    assert "= _handle_compress_session_stream" in body


def test_route_registration_stays_in_plugin_and_calls_via_adapter():
    """路由注册留在 plugin.py，并且必须通过 adapter 实例调。

    改成直接引用模块函数（`plugin_compress._handle_compress_session_stream(request)`）
    看着更直接，但那样就绕过了 self —— handler 是实例方法，第一个参数是 adapter。
    """
    assert "adapter._handle_compress_session_stream(request)" in _PLUGIN_CODE, (
        "路由注册没通过 adapter 实例调 handler"
    )
    # 注册动作本身不许跟着搬走
    assert "compress/stream" not in _CODE, (
        "路由注册被搬进 plugin_compress 了 —— 它必须留在 _patched_app_init 里，"
        "那是 router 未 freeze 的唯一时机 (6/17 踩过 frozen router)"
    )
    assert "compress/stream" in _PLUGIN_CODE


def test_route_registered_at_app_init_not_at_connect():
    """注册点必须在 `Application.__init__` 的 patch 里，不能挪回 connect。

    6/17 22:16 实撞：wrap connect 时 `runner.setup()` 已经调过 `app.freeze()`，
    add_post 抛 `Cannot register a resource into frozen router`。这条把那次事故
    的结论钉住 —— 症状是员工点压缩直接 500，而 hermes 启动日志一切正常。
    """
    tree = ast.parse(_PLUGIN_SRC)

    # 先精确定位那次 `add_post("/api/sessions/{session_id}/compress/stream", ...)`,
    # 再往上找**包着它的最内层函数**。
    #
    # ⚠ 不要用"源码片段里同时出现这两个词"来找 —— 外层函数的片段当然也包含嵌套
    #    在里面的那段; 也不要用"行数最小的那个"当内层, `_apply_patches` (138 行)
    #    比 `_patched_app_init` (170 行) 短, 但它根本不是内层。启发式在这里
    #    刚好给出反的答案。
    target = None
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "add_post" and node.args
                and isinstance(node.args[0], ast.Constant)
                and "compress/stream" in str(node.args[0].value)):
            target = node
            break
    assert target is not None, "找不到 add_post('/api/sessions/{id}/compress/stream')"

    enclosing = [
        n for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        and n.lineno <= target.lineno <= n.end_lineno
    ]
    holder = min(enclosing, key=lambda n: n.end_lineno - n.lineno).name
    assert "app_init" in holder, (
        f"注册点在 {holder!r}，不是 Application.__init__ 的 patch —— "
        f"router 可能已经 freeze 了 (包着它的函数: {[n.name for n in enclosing]})"
    )


# ── 不许顺手改行为 ──────────────────────────────────────────────

def test_function_local_imports_stay_local():
    """asyncio / json / web 三个都在函数体内 import，保持原样。

    提到模块层是行为改动（import 时机变了）。plugin_wechat_qr 刚因为模块层缺
    import 挂了五天；反过来把函数内 import 上提同样是没必要的风险。
    """
    tree = ast.parse(_SRC)
    for node in tree.body:                      # 模块层只该有 logging
        if isinstance(node, ast.Import):
            for a in node.names:
                assert a.name == "logging", f"模块层多了 import {a.name}"
        if isinstance(node, ast.ImportFrom):
            assert node.module == "__future__", f"模块层多了 from {node.module}"

    handler = next(n for n in tree.body
                   if getattr(n, "name", None) == "_handle_compress_session_stream")
    local = set()
    for s in ast.walk(handler):
        if isinstance(s, ast.Import):
            local |= {a.name.split(".")[0] for a in s.names}
        if isinstance(s, ast.ImportFrom):
            local |= {a.name for a in s.names}
    for must in ("asyncio", "json", "web"):
        assert must in local, f"{must} 的函数内 import 丢了"
