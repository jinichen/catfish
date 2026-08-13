"""收尾那行 "installed ✓" 必须说实话。

# 病历

原来是:

    logger.info("catfish-xcatfish-user plugin installed ✓ (15 patches applied)")

`15` 是**写死的字符串**，而 `_apply_patches` 实际挂 33 个 patch 入口。

这不是数字不好看。那行是判断"插件装好了没"用的**唯一**信号——8/13 验 P39 拆分
时用的就是它。而它：

  · patch 从 15 涨到 33 的整个过程一动不动
  · 19 个包在 try 里的可选 patch **全部失败**时，照样打 `✓ (15 patches applied)`

一个不会变的状态指示灯。跟同期查出来的几件是同一类：`|| echo` 吞掉 cargo test
退出码、`MCP_CATFISH_PREFIX` 单双下划线写错三个月没匹配过、`_require_wiring`
定义了没人调、`make_box_file` 夹具压根不存在。

所以这个文件盯的不是"计数对不对"，是"**这行日志会不会再变成常量**"。
"""
from __future__ import annotations

import ast
import importlib.util
import logging
import sys
import tempfile
import textwrap
from pathlib import Path

import pytest

_DIR = Path(__file__).resolve().parent.parent
_PLUGIN_SRC = (_DIR / "plugin.py").read_text(encoding="utf-8")
_TREE = ast.parse(_PLUGIN_SRC)


def _seg(name: str) -> str:
    node = next((n for n in _TREE.body if getattr(n, "name", None) == name), None)
    assert node is not None, f"plugin.py 里找不到 {name}"
    return ast.get_source_segment(_PLUGIN_SRC, node)


# ── 不许再出现写死的数字 ────────────────────────────────────────

def test_summary_line_has_no_hardcoded_count():
    """收尾日志里不能再出现 "N patches applied" 这种写死的数字。"""
    import re
    # 注释和 docstring 里允许提到旧文案(在解释病因)，只查真正的 logger 调用
    for node in ast.walk(_TREE):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if not (isinstance(f, ast.Attribute) and f.attr in ("info", "warning")):
            continue
        if not node.args or not isinstance(node.args[0], ast.Constant):
            continue
        msg = node.args[0].value
        if not isinstance(msg, str) or "installed" not in msg:
            continue
        assert not re.search(r"\(\s*\d+\s*个?\s*patch", msg), (
            f"收尾日志又写死了数字: {msg!r}"
        )


def test_counter_reads_the_source_instead_of_hardcoding():
    """计数器自己也不能写死 —— 那只是把同一个 bug 换个数字再犯一遍。"""
    seg = _seg("_count_patch_entry_points")
    assert "inspect" in seg and "ast" in seg, "应该从源码数"
    import re
    assert not re.search(r"return\s+\d+", seg), "计数器里出现了写死的 return"


def test_install_actually_calls_the_counter():
    """★ 收尾日志必须**真的调**计数器, 不能自己填个数。

    这条是补出来的: 第一版测试跑变异检验时, 把 `_n = _count_patch_entry_points()`
    改成 `_n = 33`, **7 条全绿**。

    原因跟 `_require_wiring` 那个死护栏一模一样 —— 测了"这个函数写得对", 没测
    "这个函数会被用到"。计数器再正确, 调用方绕过它就等于没有。
    """
    install = next(n for n in _TREE.body if getattr(n, "name", None) == "install")
    called = any(
        isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
        and c.func.id == "_count_patch_entry_points"
        for c in ast.walk(install)
    )
    assert called, "install() 没调 _count_patch_entry_points() —— 数字又是编的了"

    # 而且不能是"调了但不用" —— 结果得喂进那行日志
    seg = ast.get_source_segment(_PLUGIN_SRC, install)
    assert "_attempted" in seg and "installed" in seg


# ── 真实数量 ────────────────────────────────────────────────────

def _entry_points_in_apply_patches() -> int:
    node = next(n for n in _TREE.body if getattr(n, "name", None) == "_apply_patches")
    return sum(
        1 for c in ast.walk(node)
        if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
        and (c.func.id == "_try_patch" or c.func.id.startswith("_patch_"))
    )


def test_entry_point_count_is_well_above_the_old_lie():
    """真实数量必须远大于 15 —— 这条是那个谎的墓碑。

    数字会随着加 patch 增长, 所以不写死期望值, 只钉"比当年那个 15 多得多"。
    """
    n = _entry_points_in_apply_patches()
    assert n > 25, f"只数出 {n} 个入口, 跟预期(30+)差太远, 计数逻辑可能坏了"


def test_try_patch_does_not_swallow_the_failure_silently():
    """可选 patch 失败必须同时做两件事: 打 error + 把名字记下来。

    只打 error 的话, 收尾那行还是不知道有没有失败 —— 又回到"常量指示灯"。
    """
    seg = _seg("_try_patch")
    assert "logger.error" in seg, "失败要打 error"
    assert "_PATCH_FAILURES.append" in seg, "失败要记名字, 否则收尾日志看不见"
    assert "return False" in seg and "return True" in seg


# ── 端到端: 用跟 hermes 同款的加载方式跑一遍 ────────────────────

@pytest.fixture
def harness(tmp_path):
    """把 _try_patch / _count_patch_entry_points 搬进一个**真文件**再加载。

    必须是真文件: `_count_patch_entry_points` 靠 `inspect.getsource`, 而 exec 出来的
    函数没有源码 —— 第一版 harness 就是这么翻的车 (返 None)。
    用 spec_from_file_location 加载, 跟 hermes 装 plugin 是同一条路。
    """
    src = textwrap.dedent('''
        import logging
        from typing import Optional
        logger = logging.getLogger("harness")
        _PATCH_FAILURES: list = []
    ''') + _seg("_try_patch") + "\n\n" + _seg("_count_patch_entry_points") + textwrap.dedent('''

        def _patch_ok_1(): pass
        def _patch_ok_2(): pass
        def _patch_bad_1(): raise RuntimeError("模拟失败 1")
        def _patch_bad_2(): raise RuntimeError("模拟失败 2")

        def _apply_patches():
            _PATCH_FAILURES.clear()
            _patch_ok_1()
            _patch_ok_2()
            _try_patch(_patch_bad_1, "bad1: %s")
            _try_patch(_patch_bad_2, "bad2: %s")
    ''')
    p = tmp_path / "harness.py"
    p.write_text(src, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("catfish_test_harness", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_counter_works_under_spec_from_file_location(harness):
    """hermes 用 spec_from_file_location 装 plugin, inspect.getsource 在那下面要能用。"""
    assert harness._count_patch_entry_points() == 4, "2 裸调 + 2 个 _try_patch"


def test_failures_are_collected_with_names(harness, caplog):
    with caplog.at_level(logging.ERROR):
        harness._apply_patches()
    assert harness._PATCH_FAILURES == ["_patch_bad_1", "_patch_bad_2"]
    # 各自的原始文案要照样出现 (这次改动不许动那 19 条文案)
    assert "bad1: 模拟失败 1" in caplog.text
    assert "bad2: 模拟失败 2" in caplog.text


def test_counter_returns_none_instead_of_lying_when_source_missing():
    """读不到源码时返 None, 由调用方说"未知", 不编一个数字。

    真会发生: 打包成 pyc 分发时 inspect.getsource 拿不到源。那时候宁可显示
    "未知数量的 patch 入口", 也不能显示一个编出来的数 —— 编出来的数就是原来那个
    15 的翻版。
    """
    ns: dict = {"Optional": __import__("typing").Optional}
    exec(_seg("_count_patch_entry_points"), ns)  # noqa: S102  exec 出来的函数没有源码
    ns["_apply_patches"] = lambda: None
    assert ns["_count_patch_entry_points"]() is None
