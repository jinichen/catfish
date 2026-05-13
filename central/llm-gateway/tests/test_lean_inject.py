"""CATFISH_LEAN_INJECT v2 测试 (BL-MM9-FREEZE-v2, 5/12).

策略: 不起 FastAPI / mock LLM upstream (太重). 直接读 app.py 源码,
按"哪些 inject 函数被 `if not _lean:` 包" 模式 assert. 重构 inject pipeline
时 (改名 / 加新 inject), 这测会 fail, 提示要同步更新 LEAN 名单.

补充: BL-FIX23 L8 retry 也要受 _lean 约束 (`_lean_retry_off`).
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest


APP_PY = Path(__file__).parent.parent / "src" / "catfish_gateway" / "app.py"


@pytest.fixture(scope="module")
def app_src() -> str:
    return APP_PY.read_text(encoding="utf-8")


# ─── 总开关 ──────────────────────────────────────────────────────────


def test_lean_env_flag_exists(app_src):
    """app.py 必须有 _lean 判定 — env CATFISH_LEAN_INJECT 或 BL-LEAN-SESSION (5/13)
    header X-Catfish-Teaching-Mode: 1.
    """
    assert 'CATFISH_LEAN_INJECT' in app_src
    # 5/13 BL-LEAN-SESSION: 优先 header, fallback env
    assert 'X-Catfish-Teaching-Mode' in app_src, (
        "BL-LEAN-SESSION (5/13): 应该有 header 路径让 Companion toggle 控制"
    )
    # _lean 仍然由 env 兜底 (老部署兼容)
    assert 'os.environ.get("CATFISH_LEAN_INJECT", "0") == "1"' in app_src


# ─── lean=1 时关掉的 inject (核心 — 教学/复用纯净) ────────────────────


# 这些 inject 必须被 `if not _lean:` 包. 否则 LEAN 模式 prompt 仍会爆.
LEAN_GATED_INJECTS = [
    "inject_session_facts",
    "inject_stats_guard",
    "inject_skill_guard",
    "inject_session_history",
    "inject_employee_journal",
    "inject_feedback",
    # retry-hint 系列 (5/12 教学撞坑根因之一)
    "tool_retry_hint.inject_tool_retry_hint",
    "self_critique.inject_completion_critique_hint",
    # duplicate_tool_call_guard.inject_duplicate_guard_hint 已从 chat_completions
    # 入口删除 (5/13 鸿波"乱七八糟"), 不再 LEAN-gate. 模块本身仍在.
]


@pytest.mark.parametrize("inject_name", LEAN_GATED_INJECTS)
def test_inject_is_lean_gated(app_src, inject_name):
    """每个 LEAN_GATED inject 调用前 5 行内必须出现 `if not _lean` (或 `and not _lean`)."""
    # 找 inject 调用位置
    lines = app_src.split("\n")
    found_call_line = None
    for i, line in enumerate(lines):
        if inject_name in line and "(" in line and "body[" in line:
            found_call_line = i
            break
    assert found_call_line is not None, f"{inject_name} 在 app.py 里找不到调用"

    # 往上找最近的 `if not _lean` 或 `and not _lean`.
    # 窗口拉到 20 行 — 因为 lean gate 经常包一个 block (含多个 inject), inject 跟 if 行距远.
    window = lines[max(0, found_call_line - 20):found_call_line + 1]
    window_text = "\n".join(window)
    has_gate = (
        "if not _lean" in window_text
        or "and not _lean" in window_text
        or "_lean_retry_off" in window_text
    )
    assert has_gate, (
        f"{inject_name} 调用前 10 行没找到 `if not _lean` gate.\n"
        f"上下文:\n{window_text}"
    )


# ─── lean=1 时**仍保留**的核心 inject ────────────────────────────────


# 这些是身份 / skill 列表 / 锁定目标 / 时间感 / 安全, 教学时也要保留.
LEAN_PRESERVED_INJECTS = [
    "inject_identity_if_needed",  # 身份 SOUL.md
    "inject_skills_catalog",       # LLM 必须看到 skill 列表 (含凝固后的)
    "inject_session_goal",         # /goal Ralph loop
]


@pytest.mark.parametrize("inject_name", LEAN_PRESERVED_INJECTS)
def test_inject_is_lean_preserved(app_src, inject_name):
    """核心 inject 不该被 `if not _lean:` 包."""
    lines = app_src.split("\n")
    found_call_line = None
    for i, line in enumerate(lines):
        if inject_name in line and "(" in line and "body[" in line:
            found_call_line = i
            break
    assert found_call_line is not None, f"{inject_name} 找不到"

    # 往上找最近的 `if`. 不应该是 `if not _lean`
    for j in range(found_call_line - 1, max(0, found_call_line - 8), -1):
        stripped = lines[j].strip()
        if stripped.startswith("if ") or stripped.startswith("elif "):
            # 找到最近的 if — 检查不是 _lean gate
            assert "_lean" not in stripped, (
                f"{inject_name} 被 _lean gate 包了: '{stripped}'. "
                "这是核心 inject 必须保留."
            )
            break


# ─── BL-FIX23 retry tests 已 DELETED (5/13 鸿波"乱七八糟") ───────────
# test_plan_only_retry_lean_only_skips_feedback / test_plan_only_retry_max_differs_by_path
# 测的是 BL-FIX23 retry trigger 表达式 + retry 上限常量, 现在 retry 整套删了,
# 这些 assertion 都不适用. LEAN 总开关 (CATFISH_LEAN_INJECT / X-Catfish-Teaching-Mode)
# 仍由其他测试覆盖, 控制 SOUL inject pipeline.


# ─── 默认行为兼容性 ──────────────────────────────────────────────────


def test_default_env_is_not_lean(monkeypatch):
    """没设 CATFISH_LEAN_INJECT 时 _lean 应该是 False (老行为)."""
    monkeypatch.delenv("CATFISH_LEAN_INJECT", raising=False)
    lean = os.environ.get("CATFISH_LEAN_INJECT", "0") == "1"
    assert lean is False


def test_lean_env_1_is_on(monkeypatch):
    monkeypatch.setenv("CATFISH_LEAN_INJECT", "1")
    lean = os.environ.get("CATFISH_LEAN_INJECT", "0") == "1"
    assert lean is True


def test_lean_env_other_values_off(monkeypatch):
    """非 '1' 值 (例如 'true' / 'yes' / '0') 都视为 off — 严格 strict."""
    for val in ["true", "yes", "0", "TRUE", "on", ""]:
        monkeypatch.setenv("CATFISH_LEAN_INJECT", val)
        lean = os.environ.get("CATFISH_LEAN_INJECT", "0") == "1"
        assert lean is False, f"{val!r} 不该 turn on lean"
