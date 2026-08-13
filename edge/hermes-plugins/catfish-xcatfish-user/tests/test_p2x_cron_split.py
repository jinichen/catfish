"""cron 一族拆出 plugin_cron 之后的结构不变式。

拆红线的第二块（第一块是 P39 Codex 会话池）。这个文件钉住几件搬运时最容易
静默搞坏的事，每一条都对应一个已经发生过的事故类型。
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_DIR))

import plugin_cron as pc  # noqa: E402

_CRON_SRC = (_DIR / "plugin_cron.py").read_text(encoding="utf-8")
_PLUGIN_SRC = (_DIR / "plugin.py").read_text(encoding="utf-8")

CRON_SYMBOLS = [
    "_CATFISH_CRON_THREAD_LOCAL",
    "_handle_cron_pause", "_handle_cron_resume", "_handle_cron_delete",
    "_patch_p26_cron_rest_endpoints", "_patch_p21_cron_picker_integration",
    "_patch_p25_cron_env_isolation", "_patch_p27_cron_auto_retry",
]


# ── 搬运完整性 ──────────────────────────────────────────────────

@pytest.mark.parametrize("name", CRON_SYMBOLS)
def test_symbol_moved_here(name):
    assert hasattr(pc, name), f"{name} 没搬过来"


def test_plugin_keeps_only_reexports():
    """plugin.py 里这 8 个名字只能是 re-export，不能有第二份实现。

    两份同名定义时后定义的赢，另一份变死代码，而测试照样绿（调用方拿到的是
    能跑的那份）。这条盯的就是这种静默重复。
    """
    tree = ast.parse(_PLUGIN_SRC)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            assert node.name not in CRON_SYMBOLS, f"plugin.py 里还定义着 {node.name}"
        tgt = None
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            tgt = node.targets[0].id
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            tgt = node.target.id
        if tgt in CRON_SYMBOLS:
            v = node.value
            assert (isinstance(v, ast.Attribute) and isinstance(v.value, ast.Name)
                    and v.value.id == "plugin_cron"), (
                f"{tgt} 在 plugin.py 里不是 re-export 而是 {ast.unparse(v)[:60]}"
            )


# ── ★ cron threadlocal：改错了不报错，只是 execute_code 有时被拦 ──

def test_cron_threadlocal_has_no_readers_outside_p25():
    """`_CATFISH_CRON_THREAD_LOCAL` 只能由 `_patch_p25_cron_env_isolation` 读。

    这个 threadlocal 是 P25 判定"本线程真的在 cron run_job 里"的唯一依据 ——
    hermes 的 `cron/scheduler.py:1558` 把 `HERMES_CRON_SESSION` env set 了不清，
    env 又是进程级跨线程的，整个 daemon 被污染；之后任何 chat / api 调
    execute_code 走 `check_execute_code_guard:1714` 看见 env=1 + cron_mode=deny
    就 BLOCKED（P3.5.104，6/24「execute_code 一直被拦」）。

    搬这块之前手工查过"没有组外读者"，这条把它变成可执行的。要是将来有人在
    别的模块里读它（而那个模块拿到的是另一个对象），后果是 execute_code 的
    放行判定**有时对有时不对** —— 不报错，只是行为随线程飘。
    """
    readers: list[str] = []
    for src, where in ((_CRON_SRC, "plugin_cron"), (_PLUGIN_SRC, "plugin")):
        tree = ast.parse(src)
        for node in tree.body:
            fname = getattr(node, "name", None)
            if fname == "_patch_p25_cron_env_isolation":
                continue                      # 唯一合法的读者
            for sub in ast.walk(node):
                if isinstance(sub, ast.Name) and sub.id == "_CATFISH_CRON_THREAD_LOCAL":
                    if isinstance(sub.ctx, ast.Load):
                        readers.append(f"{where}.{fname or '<模块层>'} L{sub.lineno}")
    # plugin.py 的 re-export 行是 Load，但它是"把同一个对象吐回去"，不是另起一份
    readers = [r for r in readers if "<模块层>" not in r]
    assert not readers, f"P25 之外还有人读这个 threadlocal: {readers}"


def test_plugin_reexports_the_same_threadlocal_object():
    """re-export 必须是**同一个对象**，不能各造一个。

    两个 threading.local() 实例互不可见 —— 一个线程在 A 上打了标记，P25 去 B 上
    查，查不到，于是按"不在 cron 里"处理。同样是不报错的错。
    """
    tree = ast.parse(_PLUGIN_SRC)
    found = False
    for node in tree.body:
        if (isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id == "_CATFISH_CRON_THREAD_LOCAL"):
            found = True
            assert "plugin_cron._CATFISH_CRON_THREAD_LOCAL" in ast.unparse(node.value), (
                "plugin.py 又造了一个 threading.local()，不是引用 plugin_cron 那个"
            )
    assert found, "plugin.py 没 re-export _CATFISH_CRON_THREAD_LOCAL"


# ── 注入契约 ────────────────────────────────────────────────────

def test_wiring_guard_raises_when_not_wired(monkeypatch):
    monkeypatch.setattr(pc, "model_authority", None)
    with pytest.raises(RuntimeError, match="没接上依赖"):
        pc._require_wiring()


def test_wiring_guard_is_actually_called_by_plugin():
    """护栏必须有人调 —— P39 那次差点交了个定义完整但永不执行的护栏。

    而且要在**模块层**调：P21 要到第一个 cron job 触发才执行，那时候报错已经
    在定时任务路径上了，而定时任务失败最不容易被人看见。
    """
    called = any(
        isinstance(n, ast.Expr) and isinstance(n.value, ast.Call)
        and isinstance(n.value.func, ast.Attribute)
        and n.value.func.attr == "_require_wiring"
        and isinstance(n.value.func.value, ast.Name)
        and n.value.func.value.id == "plugin_cron"
        for n in ast.parse(_PLUGIN_SRC).body
    )
    assert called, "plugin.py 没在模块层调 plugin_cron._require_wiring()"


# ── 搬运时不许"顺手"改行为 ──────────────────────────────────────

def test_web_import_stays_inside_the_handlers():
    """三个 REST handler 各自函数内 `from aiohttp import web`，保持原样。

    提到模块层看着更整齐，但那是行为改动（import 时机从"首次调用"变成"模块
    加载"）。plugin_wechat_qr 就是因为模块层缺 import 而挂了五天 —— 反过来，
    把原本在函数内的 import 提到模块层同样是没必要的风险，不该搭在拆文件里。
    """
    tree = ast.parse(_CRON_SRC)
    for node in tree.body:                       # 模块层不许有 aiohttp
        if isinstance(node, ast.ImportFrom):
            assert node.module != "aiohttp", "web 被提到模块层了 —— 那是行为改动"

    for handler in ("_handle_cron_pause", "_handle_cron_resume", "_handle_cron_delete"):
        node = next(n for n in tree.body if getattr(n, "name", None) == handler)
        has = any(
            isinstance(s, ast.ImportFrom) and s.module == "aiohttp"
            and any(a.name == "web" for a in s.names)
            for s in ast.walk(node)
        )
        assert has, f"{handler} 里的 `from aiohttp import web` 丢了"


def test_picker_read_goes_through_model_authority():
    """P21 读 picker 必须走 model_authority —— 全仓只能有一份实现。

    8/13 合过一次：plugin.py 那份和 model_authority 那份并存时，飘了就是
    "有的路径听 picker、有的不听"，跟 8/9 那次会话级 model override 一样，
    现场看不出来。
    """
    assert "model_authority.read_picker_model()" in _CRON_SRC
    assert "_read_catfish_picker_model" not in _CRON_SRC, (
        "又出现了本地的 picker 读法"
    )
