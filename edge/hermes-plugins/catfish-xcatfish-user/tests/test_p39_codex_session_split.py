"""P39 拆出 plugin_codex_session 之后的结构不变式。

plugin.py 4349 行越过 CLAUDE.md §1 的 800 行硬红线，P39 是拆的第一块。这个文件
钉住拆分本身不会静默失效的几件事。

拆的时候真踩到一个: `_require_wiring()` 写好了却**没有任何地方调它** —— 一个
定义完整、注释详尽、永远不会执行的护栏。这跟这两天查出来的几件事是同一类
（`|| echo` 吞掉 cargo test 退出码、`MCP_CATFISH_PREFIX` 单双下划线写错三个月
没匹配过、`make_box_file` 夹具压根不存在）。所以下面专门有一条盯它。
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_DIR))

import plugin_codex_session as pcs  # noqa: E402


# ── 注入契约 ────────────────────────────────────────────────────

def test_module_imports_without_siblings():
    """能独立 import —— 兄弟模块是注入的, 不是 import 的。

    要是哪天有人在模块层写了 `import resolver`, 这条不一定红 (sys.path 里恰好
    找得到), 但 test_no_module_level_sibling_import 会红。
    """
    assert pcs is not None


def test_no_module_level_sibling_import():
    """模块层不许直接 import 兄弟模块 —— 包名带 dash, 那条路不稳。

    plugin.py 的 _import_sibling 有三段 fallback 就是为这个写的; 拆出去的模块
    不能自己发明一套, 也不能 import plugin.py (循环)。所以只能靠注入。
    """
    src = (_DIR / "plugin_codex_session.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    siblings = {"resolver", "model_authority", "session_registry", "plugin"}
    for node in tree.body:  # 只看模块层, 函数体内的延迟 import 不算
        if isinstance(node, ast.Import):
            for a in node.names:
                assert a.name.split(".")[0] not in siblings, f"模块层 import 了兄弟 {a.name}"
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            assert root not in siblings, f"模块层 from {node.module} import ..."
            assert node.level == 0, "模块层用了相对 import —— 包名带 dash, 不稳"


def test_wiring_guard_raises_when_not_wired(monkeypatch):
    monkeypatch.setattr(pcs, "resolver", None)
    monkeypatch.setattr(pcs, "model_authority", None)
    with pytest.raises(RuntimeError, match="没接上依赖"):
        pcs._require_wiring()


def test_wiring_guard_passes_when_wired(monkeypatch):
    monkeypatch.setattr(pcs, "resolver", object())
    monkeypatch.setattr(pcs, "model_authority", object())
    pcs._require_wiring()  # 不该抛


def test_wiring_guard_is_actually_called_by_plugin():
    """★ 护栏必须**有人调**。

    写这个文件时的真实情况: `_require_wiring()` 定义好了、注释写得很详细、
    然后没有任何地方调用它。测试全绿, 因为它测的是"这个函数行为对"而不是
    "这个函数会被执行"。

    这条查 plugin.py 的源码 —— 调用点必须在模块层 (加载期就验), 不是等 patch
    真跑的时候才验; P39 的 patch 要到第一次 _resolve_runtime_agent_kwargs 才
    执行, 那时候报错已经在员工的请求路径上了。
    """
    src = (_DIR / "plugin.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    called_at_module_level = False
    for node in tree.body:
        if not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Call):
            continue
        f = node.value.func
        if (isinstance(f, ast.Attribute) and f.attr == "_require_wiring"
                and isinstance(f.value, ast.Name)
                and f.value.id == "plugin_codex_session"):
            called_at_module_level = True
    assert called_at_module_level, (
        "plugin.py 没在模块层调 plugin_codex_session._require_wiring() —— "
        "护栏定义了但不执行, 等于没有"
    )


# ── 搬运完整性 ──────────────────────────────────────────────────

P39_SYMBOLS = [
    "_P39_CODEX_CACHE", "_P39_CODEX_CACHE_LOCK", "_P39_CODEX_CACHE_TTL_SECONDS",
    "_P39_CODEX_CACHE_MAX", "_P39_CODEX_JANITOR_STARTED",
    "_p39_close_cache_entries", "_p39_prune_codex_cache",
    "_p39_close_all_codex_sessions", "_p39_start_codex_janitor",
    "_patch_p39_codex_app_server_auth_bypass",
]


@pytest.mark.parametrize("name", P39_SYMBOLS)
def test_symbol_moved_here(name):
    assert hasattr(pcs, name), f"{name} 没搬过来"


def test_plugin_still_reexports_the_patch_entry():
    """plugin.py 必须把入口 re-export 回去 —— 老 caller 不能破。

    仓库里已经有这个协议 (plugin_weixin_zh / plugin_memory_gate / plugin_core_tools
    都这么做), 注释写着"免得下次谁 from plugin import xxx 又踩空"。
    """
    src = (_DIR / "plugin.py").read_text(encoding="utf-8")
    assert "_patch_p39_codex_app_server_auth_bypass = plugin_codex_session." in src


def test_p39_definitions_are_gone_from_plugin():
    """plugin.py 里不能还留一份 —— 两份同名定义时后定义的赢, 另一份变死代码。"""
    src = (_DIR / "plugin.py").read_text(encoding="utf-8")
    defined = set()
    for node in ast.parse(src).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            defined.add(node.name)
    for name in P39_SYMBOLS:
        if name.startswith("_p39_") or name.startswith("_patch_"):
            assert name not in defined, f"plugin.py 里还定义着 {name}"


# ── 会话池的行为红线 ────────────────────────────────────────────

def test_janitor_never_tears_down_a_busy_session():
    """正在出结果的 Codex session 绝不能被回收。

    `_p39_close_cache_entries` 拿不到 entry 的 lock 时必须把它**放回池子**,
    而不是丢掉或者硬关。硬关的后果是员工正在看的回答中途断掉, 而且日志里只会
    留一条"session closed"看不出异常。
    """
    import threading

    busy_lock = threading.Lock()
    busy_lock.acquire()          # 模拟"有一轮对话正占着它"
    closed = []

    class _FakeSession:
        def close(self):
            closed.append(True)

    entry = {"lock": busy_lock, "session": _FakeSession(), "last_used": 0.0}
    pcs._P39_CODEX_CACHE.clear()
    try:
        pcs._p39_close_cache_entries([("k", entry)], "test")
        assert not closed, "占用中的 session 被关掉了"
        assert pcs._P39_CODEX_CACHE.get("k") is entry, "占用中的条目没被放回池子"
    finally:
        busy_lock.release()
        pcs._P39_CODEX_CACHE.clear()


def test_idle_session_is_closed():
    """反过来: 拿得到锁的空闲 session 要真的关掉, 否则池子永远不回收。"""
    import threading

    free_lock = threading.Lock()
    closed = []

    class _FakeSession:
        def close(self):
            closed.append(True)

    entry = {"lock": free_lock, "session": _FakeSession(), "last_used": 0.0}
    pcs._P39_CODEX_CACHE.clear()
    try:
        pcs._p39_close_cache_entries([("k", entry)], "test")
        assert closed == [True], "空闲 session 没被关掉"
        assert "k" not in pcs._P39_CODEX_CACHE
    finally:
        pcs._P39_CODEX_CACHE.clear()
