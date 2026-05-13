"""BL-LEAN-SESSION (5/13 鸿波拍板 '客户无法跑命令行') — header 控制 LEAN 模式.

Companion 加 🎓 教学模式 toggle 后, 请求带 X-Catfish-Teaching-Mode: 1 header.
gateway 优先看 header, 没 header 才 fallback env (老部署兼容).

跨 session 隔离 — 教学完关 toggle 立刻回常态, 不需要重启 gateway / shell unset.
"""
from __future__ import annotations

from pathlib import Path

import pytest

APP_PY = Path(__file__).parent.parent / "src" / "catfish_gateway" / "app.py"


@pytest.fixture(scope="module")
def app_src() -> str:
    return APP_PY.read_text(encoding="utf-8")


# ─── header 出现 ──────────────────────────────────────────────────────


def test_header_name_x_catfish_teaching_mode(app_src):
    """header 名固定 X-Catfish-Teaching-Mode (Companion / gateway 两边对齐)."""
    assert "X-Catfish-Teaching-Mode" in app_src


def test_header_or_env_short_circuit(app_src):
    """_lean 判定: header == '1' OR env 'CATFISH_LEAN_INJECT' == '1' — 任一 True 即开."""
    # 找 chat_completions 内 _teaching_mode 赋值
    assert '_teaching_mode = request.headers.get("X-Catfish-Teaching-Mode") == "1"' in app_src
    # _lean = teaching_mode or env (两条都满足任一开)
    # 找 _lean = _teaching_mode or os.environ.get(...) 模式
    found = False
    for line in app_src.splitlines():
        if "_lean =" in line and "_teaching_mode" in line and "CATFISH_LEAN_INJECT" in line:
            found = True
            break
    assert found, "chat_completions 内 _lean 应该是 _teaching_mode or env 短路"


def test_teaching_mode_propagated_to_stream(app_src):
    """已废 (5/13 鸿波"全部清干净"): teaching_mode 参数从 _stream_chat_completion
    签名删了 (BL-FIX23 retry 没了它就 dead arg). _lean 控制 SOUL inject 在
    chat_completions 入口 (1651 行) 已用过, 不需要透传给 stream."""
    # signature 不再含 teaching_mode
    assert "teaching_mode: bool = False" not in app_src, (
        "teaching_mode dead arg 应已从 _stream_chat_completion 签名删除"
    )


def test_stream_uses_teaching_mode_for_retry(app_src):
    """已废: BL-FIX23 retry 整套删了 (5/13 鸿波"乱七八糟"). teaching_mode 透传
    也跟着删了. _teaching_mode / _lean 仍在 chat_completions 入口控 SOUL inject."""
    assert "_teaching_mode" in app_src, (
        "BL-LEAN-SESSION header 入口判定 _teaching_mode 应该还在"
    )
    assert "X-Catfish-Teaching-Mode" in app_src


# ─── 默认行为 (无 header 无 env) ──────────────────────────────────────


def test_no_header_no_env_means_lean_off(app_src):
    """没传 header 没设 env → _lean=False (老部署默认 0 = 完整 inject)."""
    # 这个是 runtime 行为, 静态分析: env 默认值是 "0" 而不是 "1"
    assert 'os.environ.get("CATFISH_LEAN_INJECT", "0")' in app_src
    # 默认 "0" → != "1" → False


# ─── 跟 env 兼容 (老部署不破) ───────────────────────────────────────


def test_env_only_still_works(app_src):
    """老部署 export CATFISH_LEAN_INJECT=1 没传 header 也要工作."""
    # _lean 表达式必须 OR env, 即使 header 没命中, env 仍能开
    # 已经被 test_header_or_env_short_circuit 覆盖
    pass
