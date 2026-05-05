"""chat_completions 的 AST 静态测试 (回归防御).

为什么静态测试:
    chat_completions 调上游 LLM, 跑 unit test 要 mock LiteLLM + 构造 fastapi.Request +
    Mock User, 工程量大. 但 P0 bug "UnboundLocalError: is_internal_call" (5/5 17:13)
    本质是个静态 control-flow 问题: 用变量前必须赋值. AST 检查比 mock-based 测试
    更便宜更稳, 5ms 跑完, 不依赖任何 runtime.

Bug 历史 (5/5 17:13 鸿波报):
    - app.py:1012-1014 早期 is_internal_call 赋值被 (谁? 啥时?) 注释掉
    - app.py:1074 (session_meta tick), 1106 (prompt_security) 用了这个变量
    - app.py:1165 重新赋值
    - 执行流: 1004 (parse body) → 1074 (UnboundLocalError) → 500 → 永远到不了 1165
    - 现象: 所有 POST /v1/chat/completions 全 500, gateway log 刷屏 traceback

修法 (5/5 17:xx):
    取消 line 1012-1014 注释, 让 is_internal_call 在 line 1074 之前一定赋值.
    line 1165 那条冗余赋值留作 defensive (防有人不小心又注释 line 1012).

防回归:
    本测试 ast-walk chat_completions function, 找所有 'is_internal_call' 引用 (Load
    context = read), 验证每一次 read 之前在同一函数 scope 内至少有一次 Store
    (write). 防有人再次注释掉早期赋值.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest


APP_PY = (
    Path(__file__).resolve().parent.parent
    / "src" / "catfish_gateway" / "app.py"
)


def _find_function(tree: ast.AST, name: str) -> ast.AsyncFunctionDef | ast.FunctionDef | None:
    """递归找名为 name 的顶层函数 (含 async)."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)) and node.name == name:
            return node
    return None


def _collect_var_events(
    func_node: ast.AsyncFunctionDef | ast.FunctionDef, var_name: str,
) -> list[tuple[int, str]]:
    """List all (line_no, "store" | "load") events for var_name in func body.

    Walks AST in document order. Only counts simple Name nodes (not attributes).
    Useful approximation for detecting "use before store" in straight-line code.
    """
    events: list[tuple[int, str]] = []
    for node in ast.walk(func_node):
        if isinstance(node, ast.Name) and node.id == var_name:
            if isinstance(node.ctx, ast.Store):
                events.append((node.lineno, "store"))
            elif isinstance(node.ctx, ast.Load):
                events.append((node.lineno, "load"))
    # AST walk 顺序 = document 顺序 (大致). sort by line for safety.
    events.sort(key=lambda e: e[0])
    return events


def test_is_internal_call_assigned_before_first_use():
    """5/5 17:13 P0 回归防御: is_internal_call 用前必须赋值.

    具体 bug: line 1012-1014 早期赋值被注释, 但 line 1074 (session_meta tick) 用了
    这变量, line 1165 才重新赋值, chat_completions 全 500.
    """
    source = APP_PY.read_text(encoding="utf-8")
    tree = ast.parse(source)

    func = _find_function(tree, "chat_completions")
    assert func is not None, "chat_completions 函数找不到 (重命名了?)"

    events = _collect_var_events(func, "is_internal_call")

    # 至少要有一次 store 和至少一次 load
    stores = [line for line, kind in events if kind == "store"]
    loads = [line for line, kind in events if kind == "load"]

    assert stores, (
        "is_internal_call 在 chat_completions 里没有任何赋值 — 可能整段被删 / 注释了. "
        "P0 防御点失效, 立刻找 line 1012 附近赋值是不是被注释掉了."
    )
    assert loads, (
        "is_internal_call 在 chat_completions 里没有任何引用 — 函数已重构, "
        "本测试可能要更新, 但要确认变量名没改."
    )

    # 第一次 load 必须在第一次 store 之后 (或同一行)
    first_store = stores[0]
    first_load = loads[0]
    assert first_load >= first_store, (
        f"chat_completions: is_internal_call 在 line {first_load} 被引用, "
        f"但首次赋值在 line {first_store} (晚于引用) → UnboundLocalError. "
        f"5/5 17:13 鸿波报的 P0 又回来了, 立刻检查 line ~1012 早期赋值是不是被注释了."
    )


def test_is_internal_call_has_early_assignment():
    """额外严格: 早期赋值应在函数前 1/3 范围内 (尽量挨着 body parse).

    防 future change 把早期赋值挪到很后面再不小心又有人在前面用它.
    """
    source = APP_PY.read_text(encoding="utf-8")
    tree = ast.parse(source)

    func = _find_function(tree, "chat_completions")
    assert func is not None

    func_start = func.lineno
    func_end = func.end_lineno or (func_start + 500)
    func_len = func_end - func_start

    events = _collect_var_events(func, "is_internal_call")
    stores = [line for line, kind in events if kind == "store"]
    assert stores, "is_internal_call 没赋值 (test_is_internal_call_assigned_before_first_use 应该已抓)"

    first_store_offset = stores[0] - func_start
    # 函数前 1/3 内必须有早期赋值
    assert first_store_offset < func_len / 3, (
        f"chat_completions: is_internal_call 首次赋值在函数 line +{first_store_offset} "
        f"(函数总长 {func_len}). 应在前 1/3 内 (尽量靠近 body parse). "
        f"如果挪后了, 检查中间有没有 'if not is_internal_call' 类似引用."
    )
