"""P18 压缩的 SSE 契约 —— 以**代码**为真值，不以文档为真值。

# 为什么要有这个文件

8/13 准备写 Phase 2（Companion 客户端）时，发现同一份契约有三个互相打架的版本：

    docs/P3.5.18-design.md      漏 aborted / fallback_used
    handler 自己的 docstring     写的是 design 那版，跟自己发的不一致
    实际 send_event 调用          唯一真值

照文档写客户端，完成弹窗会拿到一半 undefined。

更要紧的是查出一个真 bug：`summarize_manual_compression` 的 `compression_state`
**根本没传**，导致 `aborted` / `fallback_used` 恒 False ——
**压缩失败会被报成成功**。hermes 自己 5 个调用点全传，只有 P18 漏了。

所以这个文件钉的是"契约不能再漂"，判据全部从 AST 里取，不看注释。
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_DIR))

_SRC = (_DIR / "plugin_compress.py").read_text(encoding="utf-8")
_HANDLER = next(
    n for n in ast.parse(_SRC).body
    if getattr(n, "name", None) == "_handle_compress_session_stream"
)

#: summarize_manual_compression 的返回键（hermes agent/manual_compression_feedback.py）。
#: 这是**外部依赖**，hermes 升级改了它这里会红 —— 那正是需要人看一眼的时刻。
_SUMMARY_KEYS = {"noop", "aborted", "fallback_used", "headline", "token_line", "note"}


def _send_event_calls() -> list[tuple[str, ast.Call]]:
    out = []
    for c in ast.walk(_HANDLER):
        if (isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
                and c.func.id == "send_event" and c.args):
            ev = c.args[0]
            if isinstance(ev, ast.Constant) and isinstance(ev.value, str):
                out.append((ev.value, c))
    return out


def test_event_names_are_exactly_these_five():
    """事件名集合固定 —— 客户端按名字分发，多一个少一个都是静默失配。"""
    names = {ev for ev, _ in _send_event_calls()}
    assert names == {
        "compress.started", "compress.progress", "compress.warn",
        "compress.completed", "compress.failed",
    }, f"事件名变了: {sorted(names)}"


def test_completed_payload_has_the_four_local_fields():
    """completed 里本函数自己算的 4 个字段。"""
    call = next(c for ev, c in _send_event_calls() if ev == "compress.completed")
    literal = {
        k.value for k in call.args[1].keys
        if isinstance(k, ast.Constant) and isinstance(k.value, str)
    }
    assert literal == {"before_count", "after_count", "before_tokens", "after_tokens"}, (
        f"本地字段变了: {sorted(literal)}"
    )


def test_completed_payload_splats_the_summary():
    """★ 必须有 `**summary` —— 那 6 个字段全靠它带进来。

    我 8/13 第一次分析时用正则只抓字面量键，**漏了 `**` 展开**，于是错误地断定
    "后端跟设计文档对不上"。用 AST 判 `**` 而不是正则，就是为了不再犯。
    """
    call = next(c for ev, c in _send_event_calls() if ev == "compress.completed")
    splats = [k for k in call.args[1].keys if k is None]  # ast: **x 的 key 是 None
    assert len(splats) == 1, "completed 里没有 `**summary` 展开"
    idx = call.args[1].keys.index(None)
    assert ast.unparse(call.args[1].values[idx]) == "summary"


# ── ★ 压缩失败必须能被报出来 ────────────────────────────────────

def test_summarize_call_passes_compression_state():
    """★ 这是那个真 bug。

    `summarize_manual_compression` 里:
        aborted       = compression_state is not None and getattr(...)
        fallback_used = compression_state is not None and getattr(...)

    不传 compression_state → 两个恒 False → headline 永远走
    "Compressed: N → M messages"，**而真实情况可能是摘要 LLM 挂了一条没删
    (aborted)，或者摘要挂了走降级硬删了 N 条 (fallback_used)**。
    员工看到"压缩完成"，上下文可能被砍了或者压根没压。

    hermes 自己 5 个调用点全传，4 处逐字是 getattr(agent, "context_compressor", None)。
    """
    call = next(
        c for c in ast.walk(_HANDLER)
        if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
        and c.func.id == "summarize_manual_compression"
    )
    kw = {k.arg: k.value for k in call.keywords}
    assert "compression_state" in kw, (
        "compression_state 没传 —— aborted / fallback_used 会恒 False，"
        "压缩失败被报成成功"
    )
    src = ast.unparse(kw["compression_state"])
    assert "context_compressor" in src, f"传的不是 context_compressor: {src}"
    assert src.startswith("getattr("), (
        "必须用 getattr 兜底 —— context_compressor 在 AIAgent 上是**条件存在**的，"
        "hermes 全仓访问它都走 getattr/hasattr"
    )


def test_docstring_lists_all_ten_completed_fields():
    """handler 的 docstring 是写客户端的人唯一会读的东西，必须是真值。

    原来它写的是 design doc 那版（漏 aborted / fallback_used / before_tokens /
    after_tokens），跟自己发的对不上。谁照它写客户端，失败态就没有 UI。
    """
    doc = ast.get_docstring(_HANDLER) or ""
    for f in ("before_count", "after_count", "before_tokens", "after_tokens",
              "headline", "token_line", "note", "noop", "aborted", "fallback_used"):
        assert f in doc, f"docstring 漏了 completed 的字段 {f}"


def test_docstring_warns_that_completed_is_not_success():
    """docstring 必须点明 completed ≠ 成功。

    这不是措辞洁癖：三个标志位任一为真都代表压缩没真做成，而 headline 只是
    换了句英文。客户端如果只显示 headline，员工分不出来。
    """
    doc = ast.get_docstring(_HANDLER) or ""
    assert "completed 不等于成功" in doc or "≠ 成功" in doc
    for flag in ("aborted", "fallback_used", "noop"):
        assert flag in doc


def test_failed_events_all_terminate_the_stream():
    """每个 compress.failed 之后必须 write_eof —— 客户端据此关闭进度 UI。

    漏一个的表现是: 弹窗永远转圈, 因为流没关而后面也不会再有事件。
    """
    n_failed = sum(1 for ev, _ in _send_event_calls() if ev == "compress.failed")
    n_eof = sum(
        1 for c in ast.walk(_HANDLER)
        if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
        and c.func.attr == "write_eof"
    )
    assert n_failed >= 5, f"failed 触发点少了 (现 {n_failed})"
    assert n_eof >= n_failed - 1, (
        f"write_eof ({n_eof}) 少于 failed ({n_failed}) —— 可能有分支没关流"
    )


def test_summary_keys_still_match_hermes():
    """跟 hermes 的 summarize_manual_compression 对一遍返回键。

    hermes 升级改了那个函数的返回结构 → 这里红。不红的话，客户端会静默拿到
    undefined —— 而那正是 8/13 差点发生的事。
    """
    # 走 HERMES_ROOT (跟 conftest / test_patches_present 同一套)。
    # 第一版只用 Path.home()，在 HERMES_ROOT 指向别处的环境里直接 skip ——
    # 而 skip 掉的测试等于没有测试，正是今天反复在抓的那种"看起来绿"。
    import os
    root = Path(os.environ.get("HERMES_ROOT") or (Path.home() / ".hermes/hermes-agent"))
    p = root / "agent/manual_compression_feedback.py"
    if not p.exists():
        pytest.skip(f"找不到 hermes-agent 源码 (HERMES_ROOT={root})")
    tree = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
    fn = next(n for n in tree.body
              if getattr(n, "name", None) == "summarize_manual_compression")
    keys: set[str] = set()
    for r in ast.walk(fn):
        if isinstance(r, ast.Return) and isinstance(r.value, ast.Dict):
            keys |= {
                k.value for k in r.value.keys
                if isinstance(k, ast.Constant) and isinstance(k.value, str)
            }
    assert keys == _SUMMARY_KEYS, (
        f"hermes 的 summarize_manual_compression 返回键变了: {sorted(keys)}\n"
        f"客户端和 docstring 都要跟着改"
    )
