"""测试 Plan D · a2a_allow — ALLOW.md 解析 + 隐私拦截 (五一 sprint Day 4).

覆盖:
- 默认 DENY (没匹配到 allow 规则)
- deny 优先 (allow 命中也拒)
- allow 段关键词命中
- allow_to 限制 (跨员工属性)
- allow_purpose 限制 (purpose 不匹配跳过)
- 多段 allow + 多关键词
- 空 ALLOW.md 全拒
"""

from __future__ import annotations

import pytest

from catfish_gateway.a2a_allow import (
    AllowConfig,
    check_allow,
    parse_allow_md,
)


SAMPLE_ALLOW_MD = """\
# 我允许其他鲶鱼问我什么

## 工作公开 (任何同事鲶鱼可问)
- 项目 X 进展
- 周报
- ISO27001

## 部门内同事 (限研发部)
allow_to: department=研发部
- 工作时段
- 工作偏好

## 限定 purpose=周报
allow_purpose: 周报
- 上周做了什么
- 本周计划

## 显式拒绝
deny:
- 薪资
- 个人财务
- 客户隐私
"""


@pytest.fixture
def config() -> AllowConfig:
    return parse_allow_md(SAMPLE_ALLOW_MD)


# ── 解析 ────────────────────────────────────────────────────────


def test_parse_basic_allow(config: AllowConfig) -> None:
    assert len(config.allow_sections) == 3
    assert config.allow_sections[0].title == "工作公开 (任何同事鲶鱼可问)"
    assert "项目 X 进展" in config.allow_sections[0].rules
    assert "周报" in config.allow_sections[0].rules
    assert "ISO27001" in config.allow_sections[0].rules


def test_parse_allow_to_constraint(config: AllowConfig) -> None:
    section = config.allow_sections[1]
    assert section.allow_to == {"department": "研发部"}
    assert "工作时段" in section.rules


def test_parse_allow_purpose_constraint(config: AllowConfig) -> None:
    section = config.allow_sections[2]
    assert section.allow_purpose == "周报"
    assert "上周做了什么" in section.rules


def test_parse_deny(config: AllowConfig) -> None:
    assert "薪资" in config.deny_section.rules
    assert "个人财务" in config.deny_section.rules
    assert "客户隐私" in config.deny_section.rules


def test_parse_empty() -> None:
    config = parse_allow_md("")
    assert len(config.allow_sections) == 0
    assert len(config.deny_section.rules) == 0


# ── 匹配 ────────────────────────────────────────────────────────


def test_default_deny(config: AllowConfig) -> None:
    """没匹配到 allow → 默认拒."""
    allowed, reason = check_allow("今天天气怎么样", config)
    assert allowed is False
    assert "default DENY" in reason


def test_allow_match_keyword(config: AllowConfig) -> None:
    """问 '项目 X 进展' 命中工作公开段."""
    allowed, reason = check_allow("项目 X 进展怎么样?", config)
    assert allowed is True
    assert "项目 X 进展" in reason


def test_allow_match_substring(config: AllowConfig) -> None:
    """子串匹配 — 'ISO27001 审核情况' 命中 'ISO27001' 关键词."""
    allowed, reason = check_allow("ISO27001 审核情况如何", config)
    assert allowed is True


def test_deny_overrides_allow(config: AllowConfig) -> None:
    """deny 优先 — '薪资' 命中 deny 即使 question 有 '工作' 关键词不影响."""
    allowed, reason = check_allow("我们公司薪资水平", config)
    assert allowed is False
    assert "matched deny" in reason
    assert "薪资" in reason


def test_allow_to_constraint_pass(config: AllowConfig) -> None:
    """allow_to: department=研发部 — 同部门员工 + 命中关键词 → 允许."""
    allowed, reason = check_allow(
        "你的工作时段一般是什么", config,
        from_attrs={"department": "研发部"},
    )
    assert allowed is True
    assert "工作时段" in reason


def test_allow_to_constraint_fail(config: AllowConfig) -> None:
    """allow_to: department=研发部 — 不同部门 (销售部) 即使命中关键词也拒."""
    allowed, reason = check_allow(
        "你的工作时段一般是什么", config,
        from_attrs={"department": "销售部"},
    )
    # 因为 "工作时段" 只在限研发部那段, 销售部跳过这段, 默认拒
    assert allowed is False


def test_allow_purpose_match(config: AllowConfig) -> None:
    """purpose=周报 命中 + 关键词 '本周计划' 匹配."""
    allowed, reason = check_allow(
        "本周计划是什么", config,
        purpose="周报",
    )
    assert allowed is True


def test_allow_purpose_mismatch(config: AllowConfig) -> None:
    """purpose=咨询 不匹配 allow_purpose=周报, 即使关键词命中也拒."""
    allowed, _ = check_allow(
        "本周计划是什么", config,
        purpose="咨询",
    )
    assert allowed is False


def test_case_insensitive_match(config: AllowConfig) -> None:
    """关键词匹配大小写不敏感."""
    config2 = parse_allow_md("## 公开\n- IsO27001\ndeny:\n")
    allowed, _ = check_allow("iso27001 审核", config2)
    assert allowed is True


def test_empty_question_default_deny(config: AllowConfig) -> None:
    """空 question 不命中任何关键词 → 默认拒."""
    allowed, _ = check_allow("", config)
    assert allowed is False
