"""pytest conftest — 让测试能直接 `import catfish_memory` (绕过 __init__.py
的 relative import, 单测不走 hermes plugin discovery).

C3 (6/6 鸿波 CI matrix audit): 老 conftest 只 sys.path insert, 但 catfish_memory.py
有 `from .catfish_memory_helpers import ...` relative import, 走 sys.path insert
路径 catfish_memory 被当 top-level module 加载, relative import 失败 (no parent
package). 修法: 把 plugin 整目录注册为 package — 用 importlib spec 创建一个
parent package alias, 让 relative import 找到 parent.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_PLUGIN_DIR = Path(__file__).parent.parent

# C3 修法: 把 plugin 整目录注册为一个 fake package `_catfish_memory_pkg`,
# 让 catfish_memory.py / catfish_memory_helpers.py 都作为它的 submodule 加载.
# 这样 catfish_memory.py 内的 `from .catfish_memory_helpers import ...` work
# (parent package = _catfish_memory_pkg, 真存在).
#
# 但 tests/test_*.py 直接 `import catfish_memory` 也要 work — 加 sys.path alias
# 让 `catfish_memory` 名也指向同一 module.
_PKG_NAME = "_catfish_memory_pkg"
if _PKG_NAME not in sys.modules:
    # 创 fake package (用 plugin dir 作 __path__)
    _spec = importlib.util.spec_from_file_location(
        _PKG_NAME,
        location=str(_PLUGIN_DIR / "__init__.py"),
        submodule_search_locations=[str(_PLUGIN_DIR)],
    )
    if _spec and _spec.loader:
        _pkg = importlib.util.module_from_spec(_spec)
        sys.modules[_PKG_NAME] = _pkg
        # 真不 exec __init__.py (它 import hermes runtime, CI 装不上), 留空 namespace
        # _spec.loader.exec_module(_pkg)  # 故意不调

# 加 plugin 目录到 sys.path, 让 `import catfish_memory` 也能找到 (但走 fake package).
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

# 把 catfish_memory.py 作为 _catfish_memory_pkg.catfish_memory 加载, 再 alias
# 成 top-level `catfish_memory`. 这样 relative import `from .catfish_memory_helpers`
# 能找到 _catfish_memory_pkg.catfish_memory_helpers.
import importlib  # noqa: E402

if "catfish_memory" not in sys.modules:
    _mem_spec = importlib.util.spec_from_file_location(
        f"{_PKG_NAME}.catfish_memory",
        str(_PLUGIN_DIR / "catfish_memory.py"),
    )
    if _mem_spec and _mem_spec.loader:
        _mem_mod = importlib.util.module_from_spec(_mem_spec)
        sys.modules[f"{_PKG_NAME}.catfish_memory"] = _mem_mod
        sys.modules["catfish_memory"] = _mem_mod  # top-level alias for tests
        # 先把 helpers 也加载好 (relative import 时 Python 会 lookup parent.helpers)
        _hlp_spec = importlib.util.spec_from_file_location(
            f"{_PKG_NAME}.catfish_memory_helpers",
            str(_PLUGIN_DIR / "catfish_memory_helpers.py"),
        )
        if _hlp_spec and _hlp_spec.loader:
            _hlp_mod = importlib.util.module_from_spec(_hlp_spec)
            sys.modules[f"{_PKG_NAME}.catfish_memory_helpers"] = _hlp_mod
            _hlp_spec.loader.exec_module(_hlp_mod)
        # 现在再 exec catfish_memory.py — relative import 能找到 parent.helpers
        _mem_spec.loader.exec_module(_mem_mod)


# P3.5.29 Phase 7.1 (6/17 鸿波): hermes-memory _get_summarize_model 加 role_resolver
# 真second tier** 改后, 老 yaml/env 测 真break** — 真测真 LIVE gateway 真
# fetch 真roles.yaml summarize: catfish-public-gemini-pro** → winning over yaml/env.
#
# fix path D: autouse fixture reset role_resolver cache + monkeypatch fetch 返 None.
# 所有 test 真默认 mock role_resolver 0 干扰, 测真yaml/env path
# 真保留**. 真单独测 role_resolver path 需要 真显式 monkeypatch.undo 或 测真直**.
#
# 为啥 autouse: 6 个 sync_turn / on_session_end 测真 hit _get_summarize_model,
# 真单测漏 mock 真LIVE gateway 真 break**. autouse 0 漏.
import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _mock_role_resolver(monkeypatch):
    """默认 mock 真`role_resolver.resolve` 真 None → fallback yaml/env path.

    测真 role_resolver 真positive path 真测显式 monkeypatch.setattr(
    role_resolver_mod, 'resolve', lambda role: ...) override autouse default.
    """
    try:
        import importlib
        _role_resolver_mod = importlib.import_module(f"{_PKG_NAME}.role_resolver")
        # reset cache 防 test 间 stale state
        _role_resolver_mod._reset_cache_for_tests()
        # 默认 mock 真resolve 返 None** → fallback yaml/env
        monkeypatch.setattr(
            _role_resolver_mod,
            "resolve",
            lambda role: None,
        )
    except Exception:
        # role_resolver.py 真没装 / load 失败** — 老测 path, 不需 mock.
        pass
    yield
