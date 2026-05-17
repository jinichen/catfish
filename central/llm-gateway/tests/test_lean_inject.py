"""CATFISH_LEAN_INJECT v3 测试 (BL-MEMORY-MIGRATE-STEP1C 5/16 之后).

# 演化

  - v1 (BL-MM9-FREEZE 5/12): 每个 inject_X(body['messages']) 调用前必须有
    `if not _lean:` gate.
  - v2 (BL-LEAN-SESSION 5/13): + X-Catfish-Teaching-Mode header 兼容
  - **v3 (BL-MEMORY-MIGRATE-STEP1C 5/16)**: 8 个 inject_X 合并进 Registry, 单点
    `_registry.inject_unified(ctx, msgs, enabled)`. lean mode 通过 **enabled set
    缩减** 实现 — `if _lean: enabled = {'skills_catalog'}`, 其它 provider 不跑.
    老的 `if not _lean: inject_X(...)` 每一个 per-call gate 都没了.

# 这个 test 覆盖什么 (v3)

  - env CATFISH_LEAN_INJECT + X-Catfish-Teaching-Mode header 总开关仍在
  - lean=True 时 enabled set 只含 skills_catalog (其它 provider 不跑)
  - 老 inject_X 函数仍在源代码 import (向后兼容 + 直接被 SkillGuardProvider 等
    provider 内部调用), 但**不再** inline 直接调
  - retry-hint / self_critique 还是受 `not _lean` gate (不在 Registry, 独立调)
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


# ─── Registry-based lean gating (v3 5/16) ────────────────────────────


def test_lean_branch_enables_only_skills_catalog(app_src):
    """BL-MEMORY-MIGRATE-STEP1C: lean=True 分支 enabled 必须只含 skills_catalog.

    其它 provider (session_facts / stats_guard / skill_guard / session_history /
    employee_journal / feedback / session_meta / hermes_memory) 都不该出现在
    lean enabled set 里 — 它们是教学场景污染源.
    """
    # 找 `if _lean:` 分支
    m = re.search(
        r"if\s+_lean\s*:\s*\n((?:[ \t]+#[^\n]*\n)*)([ \t]+enabled\s*=\s*\{[^}]+\})",
        app_src,
    )
    assert m is not None, (
        "找不到 `if _lean: ... enabled = {...}` 分支. "
        "BL-MEMORY-MIGRATE-STEP1C 用 enabled set 表达 lean gating, 该结构必须存在."
    )
    lean_enabled_line = m.group(2)
    assert "skills_catalog" in lean_enabled_line, (
        f"lean 分支必须保留 skills_catalog (LLM 必须看到能调哪些 skill). 实际:\n"
        f"{lean_enabled_line}"
    )
    # 其它 provider 都不该出现
    forbidden_in_lean = [
        "session_facts",
        "stats_guard",
        "skill_guard",
        "session_history",
        "employee_journal",
        "feedback",
        "session_meta",
        "hermes_memory",
    ]
    for name in forbidden_in_lean:
        assert name not in lean_enabled_line, (
            f"lean enabled set 不该含 {name!r}: 教学/复用场景会被污染. "
            f"实际: {lean_enabled_line}"
        )


def test_non_lean_branch_enables_all(app_src):
    """else 分支 (非 lean) enabled = None — 全 provider 都跑."""
    m = re.search(
        r"else\s*:\s*\n(?:[ \t]+#[^\n]*\n)*[ \t]+enabled\s*=\s*None",
        app_src,
    )
    assert m is not None, (
        "非 lean 分支必须 enabled = None (Registry 用 None 表示全跑)"
    )


def test_registry_inject_invoked(app_src):
    """body[messages] 必须经过 _registry.inject_unified() (或 inject_subset 回退)."""
    has_unified = "_registry.inject_unified(" in app_src
    has_subset = "_registry.inject_subset(" in app_src
    assert has_unified or has_subset, (
        "BL-MEMORY-MIGRATE-STEP1C: body['messages'] 必须经过 Registry 注入. "
        "找不到 _registry.inject_unified 或 inject_subset 调用."
    )


# ─── 仍 inline 的 hint 系列受 _lean gate ────────────────────────────


INLINE_LEAN_GATED = [
    "tool_retry_hint.inject_tool_retry_hint",
    "self_critique.inject_completion_critique_hint",
    "inject_compound_plan_execute",
]


@pytest.mark.parametrize("inject_name", INLINE_LEAN_GATED)
def test_inline_hint_inject_is_lean_gated(app_src, inject_name):
    """retry-hint / self_critique / compound_plan 不在 Registry, 在 app.py inline.
    它们的调用必须仍受 `if not _lean` gate.
    """
    lines = app_src.split("\n")
    found_call_line = None
    for i, line in enumerate(lines):
        if inject_name in line and "(" in line:
            # 跳过 import 行
            if line.strip().startswith("from ") or line.strip().startswith("import "):
                continue
            found_call_line = i
            break
    assert found_call_line is not None, f"{inject_name} 在 app.py 里找不到调用"

    # 往上 20 行找 `if not _lean` / `and not _lean`
    window = lines[max(0, found_call_line - 20):found_call_line + 1]
    window_text = "\n".join(window)
    has_gate = (
        "if not _lean" in window_text
        or "and not _lean" in window_text
    )
    assert has_gate, (
        f"{inject_name} 调用前 20 行没找到 `not _lean` gate.\n上下文:\n{window_text}"
    )


# ─── lean=1 时**仍保留**的核心 inject (非 Registry, inline 仍在) ─────


LEAN_PRESERVED_INLINE_INJECTS = [
    "inject_session_goal",  # /goal Ralph loop, 教学也要锁目标
]


@pytest.mark.parametrize("inject_name", LEAN_PRESERVED_INLINE_INJECTS)
def test_inline_preserved_inject_no_lean_gate(app_src, inject_name):
    """核心 inline inject (session_goal) 不该被 _lean gate."""
    lines = app_src.split("\n")
    found_call_line = None
    for i, line in enumerate(lines):
        if inject_name in line and "(" in line and "body[" in line:
            found_call_line = i
            break
    assert found_call_line is not None, f"{inject_name} 找不到"

    # 往上找最近的 if. 不应该是 `if not _lean`
    for j in range(found_call_line - 1, max(0, found_call_line - 8), -1):
        stripped = lines[j].strip()
        if stripped.startswith("if ") or stripped.startswith("elif "):
            assert "_lean" not in stripped, (
                f"{inject_name} 被 _lean gate 包了: '{stripped}'. "
                "这是核心 inject 必须保留."
            )
            break


# ─── 老 inject_X 函数仍 import (向后兼容 + 给 provider 内部调) ────────


LEGACY_INJECT_IMPORTS = [
    "inject_employee_journal",
    "inject_feedback",
    "inject_session_history",
    "inject_skill_guard",
    "inject_skills_catalog",
    "inject_stats_guard",
]


@pytest.mark.parametrize("import_name", LEGACY_INJECT_IMPORTS)
def test_legacy_inject_funcs_still_imported(app_src, import_name):
    """老 inject_X 函数仍要在 app.py 顶部 import — 因为 providers/*.py 内部仍调
    它们做实际注入工作 (BL-MEMORY-MIGRATE-STEP1B 包了一层 MemoryProvider 接口,
    底层实现没动). 删了 import 会撞 NameError.
    """
    pattern = rf"from\s+\.[\w_]+\s+import\s+.*{re.escape(import_name)}"
    assert re.search(pattern, app_src), (
        f"老 inject_X 函数 {import_name} 不再 import — provider 内部会撞 NameError"
    )


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
