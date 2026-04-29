"""gateway skills_inject + skills_loader 测试.

覆盖:
  - 扫描真实 catfish/skills/ 目录, 找到 leadership-briefing
  - frontmatter yaml 解析正确
  - inject_skills_catalog 把 block 拼到最后一条 system message 末尾
  - 没有 system message 时不动
  - 缓存 fingerprint 防止重复扫
  - 多次注入幂等 (block 已存在不重复)
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from catfish_gateway.skills_inject import (  # noqa: E402
    inject_skills_catalog,
    reset_cache,
)
from catfish_gateway.skills_loader import (  # noqa: E402
    discover_skills,
    format_skills_block,
)


# ── skills_loader ──────────────────────────────────────────────


def test_discover_finds_leadership_briefing():
    skills = discover_skills()
    paths = [s.skill_path for s in skills]
    assert "department/leadership-briefing" in paths, (
        f"应该找到 leadership-briefing, 实际 paths={paths}"
    )


def test_skill_meta_has_required_fields():
    skills = discover_skills()
    s = next(s for s in skills if s.skill_path == "department/leadership-briefing")
    assert s.name == "leadership-briefing"
    assert "汇报" in s.description
    assert s.skill_md_path.exists()
    assert s.script_py_path is not None
    assert s.script_py_path.exists()


def test_format_block_includes_skill_name():
    skills = discover_skills()
    block = format_skills_block(skills)
    assert "department/leadership-briefing" in block
    assert "catfish_run_skill" in block
    assert "_help" in block  # 提示 LLM 不知道 schema 时怎么查


def test_format_block_empty_when_no_skills():
    assert format_skills_block([]) == ""


# ── inject_skills_catalog ──────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_cache_each_test():
    reset_cache()


def test_inject_into_last_system_message():
    msgs = [
        {"role": "system", "content": "原始 system prompt"},
        {"role": "user", "content": "写个汇报"},
    ]
    out = inject_skills_catalog(msgs)
    assert out[0]["content"].startswith("原始 system prompt")
    assert "department/leadership-briefing" in out[0]["content"]
    # user message 不动
    assert out[1] == {"role": "user", "content": "写个汇报"}


def test_inject_targets_last_system_when_multiple():
    msgs = [
        {"role": "system", "content": "第一段 system"},
        {"role": "user", "content": "u1"},
        {"role": "system", "content": "第二段 system"},
        {"role": "user", "content": "u2"},
    ]
    out = inject_skills_catalog(msgs)
    # 第一段不动
    assert out[0]["content"] == "第一段 system"
    # 第二段被追加
    assert out[2]["content"].startswith("第二段 system")
    assert "leadership-briefing" in out[2]["content"]


def test_inject_no_system_message_noop():
    """没 system message 不主动加 — inject_identity 应已在前面跑过."""
    msgs = [{"role": "user", "content": "hi"}]
    out = inject_skills_catalog(msgs)
    assert out == msgs


def test_inject_idempotent():
    """同样的 block 已经在 system 里, 第二次注入不重复."""
    msgs = [
        {"role": "system", "content": "原始"},
        {"role": "user", "content": "u"},
    ]
    once = inject_skills_catalog(msgs)
    twice = inject_skills_catalog(once)
    # 长度不应翻倍
    assert once[0]["content"] == twice[0]["content"]


def test_inject_does_not_mutate_input():
    msgs = [{"role": "system", "content": "原始"}]
    original_content = msgs[0]["content"]
    inject_skills_catalog(msgs)
    assert msgs[0]["content"] == original_content, "不应改动 caller 的 messages"


def test_inject_handles_multimodal_content_gracefully():
    """system content 是 list 时跳过, 不抛."""
    msgs = [
        {"role": "system", "content": [{"type": "text", "text": "..."}]},
        {"role": "user", "content": "u"},
    ]
    out = inject_skills_catalog(msgs)
    # 不抛, 原样返回 (logger debug 一条)
    assert out == msgs or out[0]["content"] == msgs[0]["content"]


def test_inject_empty_messages():
    assert inject_skills_catalog([]) == []
