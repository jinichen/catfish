"""这个 plugin 有**三种**加载方式, 三种的 import 语义互不相同。

    ① hermes 真运行时
       ~/.hermes/hermes-agent/plugins/memory/__init__.py 用
       spec_from_file_location(..., submodule_search_locations=[provider_dir])
       加载本目录的 __init__.py。于是它**是**一个真包:
         · 相对 import  `from .catfish_memory_helpers import ...`  → work
         · 绝对 sibling `from catfish_memory_helpers import ...`   → **失败**
           (plugin 目录不在 sys.path)

    ② wiki_health.py 独立脚本
       edge/companion-app/src-tauri/src/services/distill_scheduler.rs:102 直接
       `python3 wiki_health.py`。它自己 sys.path.insert(plugin_dir) 然后走
       **绝对** import。这条路径下 helpers 没有父包, 相对 import 会炸。

    ③ pytest
       tests/conftest.py 又造了第三套 (fake package + top-level alias)。

# 为什么要有这个文件

5/28 鸿波查出来的 P0: catfish_memory.py 当时用的是绝对 sibling import,
在 ① 下静默失败 (loader 只 logger.debug), register() 跟着没被定义, hermes 报
"loaded but no provider instance found"。**plugin 5/19 装好, 9 天一次都没工作过。**

那 9 天之所以没人发现, 正是因为测试跑的是 ③, 而 ③ 把 ① 会踩的坑填平了。
换句话说: 当时全绿的测试, 对这个 bug 一个字都没说。

所以这里不用 conftest 的环境 —— 每种加载方式起一个**干净子进程**, 复刻它真实的
import 上下文。拆 catfish_memory_helpers.py 时, 这是唯一能拦住"又改成绝对
import"的东西。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parent.parent


def _run(code: str) -> subprocess.CompletedProcess:
    """干净子进程跑一段代码 —— 不继承 conftest 造的 sys.modules。"""
    return subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, timeout=120,
        cwd=str(PLUGIN_DIR.parent),      # 故意不在 plugin 目录里跑
    )


def test_hermes_package_mode_loads_provider() -> None:
    """① hermes 真加载方式: 包模式 + 相对 import。

    这条红 = plugin 在员工机器上根本不会注册 (而其它测试照样全绿)。

    注意它**抓不到** 5/28 那个"绝对 sibling import"的写法: 实测把
    catfish_memory.py 的 `from .catfish_memory_helpers` 改回绝对, 这条仍绿。
    因为本目录 __init__.py 有三段 fallback, 第 3 段会临时
    sys.path.insert(plugin_dir) 再 spec_from_file_location, 正好把绝对写法救回来。
    两种写法都能**加载**, 所以这条只保证"能加载", 不保证 import 风格。

    真正被那三段 fallback 掩盖的是**运行时**的 sibling import ——
    第 3 段在 finally 里又把目录 remove 了, 第 1 段压根没插过。
    见下面 test_runtime_sibling_import_in_package_mode。
    """
    code = f'''
import importlib.util, sys
from pathlib import Path
d = Path({str(PLUGIN_DIR)!r})
assert str(d) not in sys.path, "前提: plugin 目录不在 sys.path (hermes 就是这样)"

# 完全复刻 hermes 的 plugins/memory/__init__.py
spec = importlib.util.spec_from_file_location(
    "_probe_catfish_memory_pkg", str(d / "__init__.py"),
    submodule_search_locations=[str(d)],
)
mod = importlib.util.module_from_spec(spec)
sys.modules["_probe_catfish_memory_pkg"] = mod
spec.loader.exec_module(mod)

assert hasattr(mod, "register"), "register() 没被定义 —— hermes 会报 no provider instance found"
assert mod.CatfishMemoryProvider is not None, "CatfishMemoryProvider 是 None, 顶部 import 挂了"

# helpers 的符号必须真能通过 provider 拿到 (不是只 import 成功就算)
p = mod.CatfishMemoryProvider()
assert hasattr(p, "prefetch")
print("HERMES_MODE_OK")
'''
    r = _run(code)
    assert "HERMES_MODE_OK" in r.stdout, (
        f"hermes 包模式加载失败 —— 这正是 5/28 那个 9 天没工作的形状\\n"
        f"stdout={r.stdout}\\nstderr={r.stderr}"
    )


def test_standalone_script_mode_imports_helpers() -> None:
    """② wiki_health.py 的加载方式: sys.path.insert + 绝对 import, 无父包。

    这条红 = distill_scheduler.rs 每次跑体检都会炸, 而 hermes 那边毫无察觉。
    """
    needed = [
        "_canon_subtype", "_normalize_slug_for_dedup",
        "_parse_frontmatter_lists", "_read_title_of", "_rel_item_name",
    ]
    code = f'''
import sys
from pathlib import Path
d = Path({str(PLUGIN_DIR)!r})
sys.path.insert(0, str(d))          # wiki_health.py:38 就是这么干的
import catfish_memory_helpers as H  # 无父包, 相对 import 在这里会炸
missing = [n for n in {needed!r} if not hasattr(H, n)]
assert not missing, f"wiki_health.py 要的符号取不到: {{missing}}"
print("STANDALONE_MODE_OK")
'''
    r = _run(code)
    assert "STANDALONE_MODE_OK" in r.stdout, (
        f"独立脚本模式加载失败 —— wiki_health.py 会炸\\n"
        f"stdout={r.stdout}\\nstderr={r.stderr}"
    )


def test_runtime_sibling_import_in_package_mode() -> None:
    """①下**运行时**(import 完之后) 的 sibling import 必须还能用。

    这是 __init__.py 三段 fallback 盖不住的洞:
      · 第 1 段 (包模式, 正常路径) 全程不碰 sys.path
      · 第 3 段 插了 plugin_dir, 但在 finally 里 remove 掉了
    所以 import 期能用的绝对 sibling import, 到了函数体里执行就 ModuleNotFoundError。

    _check_dangling_related 正是这种: 函数体内 `from wiki_resolve import ...`。
    它由 _write_wiki_files:2460 调, 外面那层只 `except OSError` 接不住,
    再外面是 _spawn_summarize_thread 起的 fire-and-forget daemon 线程 ——
    抛出来既拦不住写盘失败, 也没人看得见。

    今天没出事只因为 ~/.catfish/memory_plugin.yaml 里 `wiki.auto_ingest: false`,
    进程内这条路没被走到。把那一位翻成 true 就会踩上。
    """
    code = f'''
import importlib.util, sys, tempfile
from pathlib import Path
d = Path({str(PLUGIN_DIR)!r})
assert str(d) not in sys.path

spec = importlib.util.spec_from_file_location(
    "_pk", str(d / "__init__.py"), submodule_search_locations=[str(d)],
)
mod = importlib.util.module_from_spec(spec)
sys.modules["_pk"] = mod
spec.loader.exec_module(mod)
assert str(d) not in sys.path, "前提: 加载完 plugin 目录不在 sys.path 上"

H = sys.modules["_pk.catfish_memory_helpers"]
content = '---\\ntype: entity\\nrelated: ["[[不存在的东西]]"]\\n---\\n\\n# x\\n正文\\n'
out = H._check_dangling_related(Path(tempfile.mkdtemp()), "wiki/entities/x.md", content)
assert out == ["不存在的东西"], f"断链没被识别出来: {{out}}"
print("RUNTIME_SIBLING_OK")
'''
    r = _run(code)
    assert "RUNTIME_SIBLING_OK" in r.stdout, (
        f"包模式下运行时 sibling import 失败 —— auto_ingest 一开 wiki 就写不进去\\n"
        f"stdout={r.stdout}\\nstderr={r.stderr[-2000:]}"
    )


def test_wiki_health_actually_runs() -> None:
    """②的端到端: 真把 wiki_health.py 跑起来。

    上面那条只验 import; 这条验它整个脚本能跑完 —— 它 import 的是
    helpers **和** wiki_resolve 两个 sibling。
    """
    r = subprocess.run(
        [sys.executable, str(PLUGIN_DIR / "wiki_health.py")],
        capture_output=True, text=True, timeout=120, cwd=str(PLUGIN_DIR.parent),
    )
    # 没有 wiki 数据时它可以报"没数据", 但不该是 ImportError/AttributeError
    bad = ("ImportError", "ModuleNotFoundError", "AttributeError", "NameError")
    hit = [b for b in bad if b in r.stderr]
    assert not hit, f"wiki_health.py 起不来: {hit}\\nstderr={r.stderr[-1500:]}"


def test_没有模块被加载两遍() -> None:
    """同一份源码不许有两个 module 对象。

    # 为什么单列一条

    8/15 早上修过一次: conftest 给 catfish_memory 加了 top-level alias 却漏了
    helpers, 于是 `H._call_merge_llm is 包里那份` → False, monkeypatch 打在
    哪一份上决定它是否生效, 而阴性断言 (assert not called) 完全察觉不到。

    当天下午拆 helpers 时**又把这个坑复制了 5 遍** —— 新的 prompts / fm / wiki /
    llm / merge 都是包子模块, 而测试写 `import catfish_memory_merge` 走 sys.path
    另加载一份。

    它之所以两次都躲过测试, 是因为**双份是自洽的**: 测试 patch 第二份、又调
    第二份, 一路对得上, 全绿。要抓它只能直接问"有没有两份", 而不是问
    "某个行为对不对"。实测: 摘掉 conftest 的 alias, 其余 7 条照绿。
    """
    import importlib
    import sys

    # **必须自己主动 import**, 不能只扫 sys.modules 现状。
    # 第一版就是只扫现状 —— 而本文件自己不 import 这些子模块, 于是摘掉
    # conftest 的 alias 做变异时它照样绿 (双份压根没在这一轮里产生)。
    # 判据依赖了别的测试文件的执行顺序, 又窄了一次。
    # **清单从文件系统推导**, 不写死。
    # 第二版还是写死的 6 个 (helpers 那批), 于是第二趟拆出来的 distill /
    # tools / expense 双份时它照样绿 —— 判据比真事窄, 同一个文件里栽第三次。
    expected = sorted(p.stem for p in PLUGIN_DIR.glob("catfish_memory*.py"))
    assert len(expected) >= 6, f"只发现 {expected} —— glob 判据是不是失效了"

    dupes = []
    for name in expected:
        bare = importlib.import_module(name)        # 走 sys.path / alias
        twin = sys.modules.get(f"_catfish_memory_pkg.{name}")
        if twin is not None and twin is not bare:
            dupes.append(name)
    assert not dupes, (
        f"这些模块有两个 module 对象: {dupes} —— monkeypatch 会静默打空, "
        "去 tests/conftest.py 看 alias 那段"
    )
