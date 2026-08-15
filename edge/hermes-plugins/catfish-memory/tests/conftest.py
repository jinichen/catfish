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


def _alias_submodules() -> None:
    """把已加载的包子模块 `_catfish_memory_pkg.catfish_memory_X` 也挂到裸名 X。

    见下面 helpers 那段注释。**必须在每次可能加载新子模块之后都调一遍** ——
    8/15 第二趟拆分踩过: 当时只在 exec helpers 之后调, 而 distill / tools /
    expense 是 catfish_memory.py 执行时才加载的, 于是那几个又成了两份,
    19 条测试红 (monkeypatch 打在第二份上, 真正被调的是第一份)。
    """
    for _full in list(sys.modules):
        if _full.startswith(f"{_PKG_NAME}.catfish_memory_"):
            _bare = _full[len(_PKG_NAME) + 1:]
            sys.modules.setdefault(_bare, sys.modules[_full])
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
            # 上面第 56 行给 catfish_memory 加了 top-level alias, 这里**漏了** helpers。
            # 后果: 测试 `from catfish_memory_helpers import ...` 走 sys.path 又
            # exec 了一遍同一个文件, 于是同一份源码有两个 module 对象:
            #   catfish_memory_helpers                     (测试看到的)
            #   _catfish_memory_pkg.catfish_memory_helpers (catfish_memory.py 看到的)
            # 实测 `top is pkg` → False。monkeypatch 打在哪一份上就决定它是否生效,
            # 而阴性断言 (assert not called) 察觉不到打空 —— 见
            # test_employee_authored.test_p19_merges_non_employee_files。
            #
            # 补 alias 让两个名字指同一个对象。必须在 exec_module **之前**注册:
            # helpers 拆出子模块后, 子模块会用绝对 import 回指
            # `from catfish_memory_helpers import ...`, 那一刻这个名字必须已经在
            # sys.modules 里, 否则会触发第二次 exec → 真循环 import。
            sys.modules["catfish_memory_helpers"] = _hlp_mod
            _hlp_spec.loader.exec_module(_hlp_mod)

            # 8/15 拆分后补: helpers 末尾的 re-export 会把 5 个子模块
            # (prompts / fm / wiki / llm / merge) 作为**包子模块**加载, 名字是
            # `_catfish_memory_pkg.catfish_memory_xxx`。而测试里写
            # `import catfish_memory_merge` 走的是 sys.path, 那个名字不在
            # sys.modules 里 → 又 exec 一份, 于是同一份源码两个 module 对象。
            #
            # 这正是今早给 helpers 修过的那个坑 (见上面那段注释), 拆分把它复制了
            # 5 遍。实测症状: `H._call_merge_llm is M._call_merge_llm` → False,
            # 也就是 monkeypatch 打在哪一份上决定它是否生效, 而阴性断言察觉不到。
            #
            # 统一 alias: 凡是已经以包子模块身份加载的 catfish_memory_*,
            # 都把裸名指到同一个对象。放在 exec 之后 —— 那时 re-export 已经把
            # 它们全加载好了; 而 pytest 收集测试在 conftest 之后, 所以测试里
            # 的 `import catfish_memory_merge` 拿到的就是这一份。
            _alias_submodules()
        # 现在再 exec catfish_memory.py — relative import 能找到 parent.helpers
        _mem_spec.loader.exec_module(_mem_mod)
        _alias_submodules()   # catfish_memory 又带进来一批子模块


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
def _isolate_home(tmp_path_factory, monkeypatch):
    """**每条测试都用一个空的假 HOME** —— 别读开发机的真实家目录。

    # 病 (8/15 鸿波本机跑出 3 条红, 沙箱/CI 全绿)

    两处代码绕过了 `CATFISH_HOME` 这个隔离口子, 直接摸真实家目录:

      1. `catfish_memory_render._render_skills_catalog` 读
         `Path.home() / ".hermes" / "skills"`。鸿波机器上那里有 **17 个真技能**,
         把 5KB 预算 (_BUDGETS["skills_catalog"]) 占满, 测试塞进 fake home 的
         `ppt-magazine` 根本进不了输出 → test_prefetch_skills_catalog 红。

      2. `catfish_memory_helpers._read_hermes_env_key` 读 `~/.hermes/.env`。
         而 `_gateway_dev_token` 的优先级是
         **OPENAI_API_KEY > CATFISH_INTERNAL_DEV_TOKEN > yaml**
         (7/27 BL-PLUGIN-AUTH-FIX 定的, 修 "Dream Engine 9.7 天没跑")。
         `.env` 里有真 OPENAI_API_KEY, 于是测试 setenv 的
         CATFISH_INTERNAL_DEV_TOKEN=x 永远轮不到 →
         test_call_summarize_llm_success (test_helpers / test_on_session_end
         各一条) 断言 `Bearer x` 必红。

    这两条**从 7/27 起就在鸿波机器上红着**, 跟今早查出的
    test_prefetch_employee_journal_distilled (红了 9 天) 是同一个形状:
    改了源码的行为契约, 没改描述那个契约的测试。

    # 为什么之前没人发现

    CI 和沙箱里没有 `~/.hermes/` —— 那两处读到空, 测试就绿。也就是说
    **绿灯来自环境的巧合, 不是代码对**。这类"只在真机红"的测试比普通红灯更坏:
    唯一会跑到它的人 (开发者本人) 会习惯性忽略, 而 CI 永远不报。

    # 修法

    `Path.home()` 和 `os.path.expanduser("~")` 在 POSIX 上都认 HOME 环境变量,
    所以改 HOME 一条就同时堵住两处。用 tmp_path_factory 而不是 tmp_path ——
    后者跟测试自己的 tmp_path 同名会打架 (有些测试往 tmp_path 里塞 .catfish)。

    Windows 上 expanduser 看 USERPROFILE, 一并设上。
    """
    fake_home = tmp_path_factory.mktemp("home")
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("USERPROFILE", str(fake_home))
    yield fake_home


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
