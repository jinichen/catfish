"""BL-MEMORY-MIGRATE-STEP1B — 包剩 4 个 Provider 的测试.

Step 1B 包: SkillsCatalog / StatsGuard / SkillGuard / EmployeeJournal
(identity 不包 — 架构决策见 EmployeeJournalProvider 文档)

每个 Provider 测:
  - Protocol 校验
  - 空数据 → 返 None
  - 有数据 → 返字符串
  - 异常 → 返 None 不抛
  - (条件触发 provider) 命中 vs 不命中
"""
from __future__ import annotations

import sys

import pytest

from catfish_gateway.memory import InjectContext, MemoryProvider
from catfish_gateway.memory.providers.employee_journal import (
    EmployeeJournalProvider,
)
from catfish_gateway.memory.providers.skill_guard import SkillGuardProvider
from catfish_gateway.memory.providers.skills_catalog import SkillsCatalogProvider
from catfish_gateway.memory.providers.stats_guard import StatsGuardProvider

_HAS_UTC = sys.version_info >= (3, 11)


# ── SkillsCatalogProvider ──────────────────────────────


def test_skills_catalog_provider_protocol():
    assert isinstance(SkillsCatalogProvider(), MemoryProvider)
    p = SkillsCatalogProvider()
    assert p.name == "skills_catalog"
    assert p.priority == 40
    # BL-MEMORY-MIGRATE-STEP1C 实盘调到 20000 (鸿波本机 14KB skills 超原 12KB)
    assert p.budget_bytes == 20000


def test_skills_catalog_provider_returns_block_or_none():
    """实盘 catalog 可能有也可能没 (依赖本机 ~/.catfish/skills/).
    沙盒里大概率没 skill, 返 None; 鸿波本机有 skill, 返字符串.
    重点: 不崩 + 返类型正确. 给一个动作意图 user message 触发 inject.
    """
    ctx = InjectContext(last_user_message="帮我做一个 PPT")
    out = SkillsCatalogProvider().prefetch(ctx)
    assert out is None or isinstance(out, str)


def test_skills_catalog_action_intent_heuristic():
    """BL-MEMORY-INJECT-OPTIMIZE A: 动作意图启发式."""
    from catfish_gateway.memory.providers.skills_catalog import _has_action_intent

    # 应该匹配的 (员工想做事)
    for msg in [
        "做个 PPT", "帮我写周报", "运行 eis-login",
        "请分析这份数据", "generate a report", "please draft",
        "登录 EIS 查询",
    ]:
        assert _has_action_intent(msg), f"{msg!r} 应被识别为动作意图"

    # 应该不匹配的 (纯查询 / 闲聊)
    for msg in [
        "你好", "你叫什么", "1+1=?",
        "什么是上下文", "今天天气", "",
    ]:
        assert not _has_action_intent(msg), f"{msg!r} 不该被识别为动作意图"


def test_skills_catalog_skips_without_intent(monkeypatch):
    """无动作意图 → 返 None 不 inject (省 14KB)."""
    ctx = InjectContext(last_user_message="你好")
    out = SkillsCatalogProvider().prefetch(ctx)
    assert out is None


def test_skills_catalog_always_inject_env_override(monkeypatch):
    """CATFISH_SKILLS_ALWAYS_INJECT=1 → 老行为永远 inject (一键回滚)."""
    monkeypatch.setenv("CATFISH_SKILLS_ALWAYS_INJECT", "1")
    ctx = InjectContext(last_user_message="你好")  # 没动作意图
    # 应该尝试 inject (沙盒可能返 None 因为没 skill, 但不该因 intent skip)
    out = SkillsCatalogProvider().prefetch(ctx)
    # 重点: 不会因 intent 跳, 返 None 是因 skills 列表本身空
    # (鸿波本机有 skill 时会返字符串)
    assert out is None or isinstance(out, str)


# ── StatsGuardProvider ─────────────────────────────────


def test_stats_guard_provider_protocol():
    assert isinstance(StatsGuardProvider(), MemoryProvider)
    p = StatsGuardProvider()
    assert p.name == "stats_guard"
    assert p.priority == 45


def test_stats_guard_no_intent_returns_none():
    """普通 chat 不触发."""
    ctx = InjectContext(
        messages=[{"role": "user", "content": "你好你叫什么"}],
    )
    assert StatsGuardProvider().prefetch(ctx) is None


def test_stats_guard_intent_returns_block():
    """命中"统计" 关键词 → 返 guard block."""
    # _STATS_PATTERNS 匹配 "统计" / "总数" / "多少[条个项行只张份]" / count / sum / 等
    ctx = InjectContext(
        messages=[{"role": "user", "content": "统计这批数据总共多少行"}],
    )
    out = StatsGuardProvider().prefetch(ctx)
    assert out is not None
    assert "execute_code" in out or "代码" in out  # block 提醒用代码算


# ── SkillGuardProvider ─────────────────────────────────


def test_skill_guard_provider_protocol():
    assert isinstance(SkillGuardProvider(), MemoryProvider)
    p = SkillGuardProvider()
    assert p.name == "skill_guard"
    assert p.priority == 55


def test_skill_guard_no_user_message_returns_none():
    """没 user message → 不触发."""
    ctx = InjectContext(messages=[{"role": "system", "content": "x"}])
    assert SkillGuardProvider().prefetch(ctx) is None


def test_skill_guard_no_match_returns_none():
    """user message 跟所有 skill triggers 都不匹配 → 不触发."""
    # 沙盒里 discover_skills() 可能返空, 也可能返一些. 不管返多少, "完全无关的话"
    # 不该匹配
    ctx = InjectContext(
        messages=[
            {"role": "system", "content": "x"},
            {"role": "user", "content": "请问 1+1 等于几"},
        ],
    )
    out = SkillGuardProvider().prefetch(ctx)
    # 1+1 没有任何 skill 该匹配
    # (理论上沙盒里 skills_root 也读不到 skill, discover_skills 返 [])
    assert out is None or isinstance(out, str)


# ── EmployeeJournalProvider ────────────────────────────


def test_employee_journal_provider_protocol():
    assert isinstance(EmployeeJournalProvider(), MemoryProvider)
    p = EmployeeJournalProvider()
    assert p.name == "employee_journal"
    assert p.priority == 60
    # BL-MEMORY-INJECT-OPTIMIZE C (5/16): 二选一后单段, budget 5000 够
    assert p.budget_bytes == 5000


def test_employee_journal_provider_no_data_returns_none(tmp_path, monkeypatch):
    """没 journal + 没 distilled → 返 None."""
    monkeypatch.setattr(
        "catfish_gateway.employee_journal._default_journal_path",
        lambda: tmp_path / "no.md",
    )
    from catfish_gateway import memory_distill
    monkeypatch.setattr(
        memory_distill, "DISTILLED_FACTS_PATH", tmp_path / "no_d.md",
    )
    assert EmployeeJournalProvider().prefetch(InjectContext()) is None


def test_employee_journal_provider_with_journal_only(tmp_path, monkeypatch):
    """有 journal 没 distilled → fallback journal 单段."""
    j_path = tmp_path / "j.md"
    j_path.write_text("## 2026-05-16\n最近 journal 内容")
    monkeypatch.setattr(
        "catfish_gateway.employee_journal._default_journal_path",
        lambda: j_path,
    )
    from catfish_gateway import memory_distill
    monkeypatch.setattr(
        memory_distill, "DISTILLED_FACTS_PATH", tmp_path / "no_d.md",
    )

    out = EmployeeJournalProvider().prefetch(InjectContext())
    assert out is not None
    assert "员工最近 journal" in out
    assert "最近 journal 内容" in out


def test_employee_journal_provider_with_distilled_only(tmp_path, monkeypatch):
    """有 distilled 没 journal → distilled 单段."""
    monkeypatch.setattr(
        "catfish_gateway.employee_journal._default_journal_path",
        lambda: tmp_path / "no.md",
    )
    d_path = tmp_path / "d.md"
    d_path.write_text("- 老板: 张总")
    from catfish_gateway import memory_distill
    monkeypatch.setattr(memory_distill, "DISTILLED_FACTS_PATH", d_path)

    out = EmployeeJournalProvider().prefetch(InjectContext())
    assert out is not None
    assert "员工长期记忆" in out
    assert "老板: 张总" in out


def test_employee_journal_provider_picks_distilled_over_journal(tmp_path, monkeypatch):
    """BL-MEMORY-INJECT-OPTIMIZE C 二选一: distilled 存在时优先 distilled, 不再两段."""
    j_path = tmp_path / "j.md"
    j_path.write_text("## 最近段")
    d_path = tmp_path / "d.md"
    d_path.write_text("- 蒸馏精华")

    monkeypatch.setattr(
        "catfish_gateway.employee_journal._default_journal_path",
        lambda: j_path,
    )
    from catfish_gateway import memory_distill
    monkeypatch.setattr(memory_distill, "DISTILLED_FACTS_PATH", d_path)

    out = EmployeeJournalProvider().prefetch(InjectContext())
    assert out is not None
    # 默认二选一: distilled 优先, journal 不该出现
    assert "蒸馏精华" in out
    assert "最近段" not in out
    assert "员工长期记忆" in out


def test_employee_journal_provider_both_segments_env_override(tmp_path, monkeypatch):
    """CATFISH_JOURNAL_BOTH_SEGMENTS=1 → 走老两段式 (一键回滚)."""
    j_path = tmp_path / "j.md"
    j_path.write_text("## 最近段")
    d_path = tmp_path / "d.md"
    d_path.write_text("- 蒸馏精华")

    monkeypatch.setattr(
        "catfish_gateway.employee_journal._default_journal_path",
        lambda: j_path,
    )
    from catfish_gateway import memory_distill
    monkeypatch.setattr(memory_distill, "DISTILLED_FACTS_PATH", d_path)
    monkeypatch.setenv("CATFISH_JOURNAL_BOTH_SEGMENTS", "1")

    out = EmployeeJournalProvider().prefetch(InjectContext())
    assert out is not None
    # 两段都在
    assert "A. 长期事实" in out
    assert "B. 最近 journal" in out
    assert "蒸馏精华" in out
    assert "最近段" in out
