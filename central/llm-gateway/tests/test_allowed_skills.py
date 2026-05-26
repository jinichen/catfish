"""BL-RBAC-DAY5 (5/17) — User.can_use_skill + skills_loader namespace + glob.

跟 test_allowed_models / test_allowed_tools 套路, 但 skill 是 glob 匹配:
  - 空 list → 全允许
  - ["catfish:*"] → 只 catfish 自家
  - ["hermes:github:zarazhangrui/*"] → 只这个 GitHub user 的
  - sysadmin 绕过
  - 多 pattern OR 关系
"""
from __future__ import annotations

import pytest  # noqa: F401

from catfish_gateway.auth.base import User


# ── User.can_use_skill 单元测 ──────────────────────────────────


def test_empty_allowed_skills_means_all_allowed():
    """空 list → 全允许 (开放默认)."""
    u = User(sub="alice@x", effective_allowed_skills=[])
    assert u.can_use_skill("catfish:department/weekly-report") is True
    assert u.can_use_skill("hermes:github:zarazhangrui/frontend-slides") is True
    assert u.can_use_skill("hermes:local:anything") is True


def test_none_allowed_skills_means_all_allowed():
    """None (没传) → __post_init__ 转空 list → 全允许."""
    u = User(sub="alice@x")
    assert u.effective_allowed_skills == []
    assert u.can_use_skill("catfish:any") is True


def test_only_catfish_namespace():
    """`catfish:*` glob — 只允许 catfish 自家 skill."""
    u = User(sub="sales@x", effective_allowed_skills=["catfish:*"])
    assert u.can_use_skill("catfish:department/weekly-report") is True
    assert u.can_use_skill("catfish:legal/contract") is True
    assert u.can_use_skill("hermes:github:foo/bar") is False
    assert u.can_use_skill("hermes:bundled:something") is False


def test_catfish_plus_hermes_bundled():
    """两个 pattern OR — catfish + hermes 自带."""
    u = User(
        sub="legal@x",
        effective_allowed_skills=["catfish:*", "hermes:bundled:*"],
    )
    assert u.can_use_skill("catfish:legal/contract") is True
    assert u.can_use_skill("hermes:bundled:read-file") is True
    assert u.can_use_skill("hermes:github:foo/bar") is False
    assert u.can_use_skill("hermes:local:my-skill") is False


def test_specific_github_org():
    """`hermes:github:zarazhangrui/*` — 只这个 GitHub 用户的."""
    u = User(
        sub="dev@x",
        effective_allowed_skills=["hermes:github:zarazhangrui/*"],
    )
    assert u.can_use_skill("hermes:github:zarazhangrui/frontend-slides") is True
    assert u.can_use_skill("hermes:github:zarazhangrui/other-skill") is True
    # 别的 org 不行
    assert u.can_use_skill("hermes:github:malicious-user/evil") is False
    # catfish 自家也不行 (没列)
    assert u.can_use_skill("catfish:weekly-report") is False


def test_sysadmin_bypasses_skill_rbac():
    """sysadmin 即使 effective_allowed_skills 收紧也全允许."""
    u = User(
        sub="admin@x",
        role="sysadmin",
        effective_allowed_skills=["catfish:*"],  # 只允许 catfish
    )
    # sysadmin 任何 skill 都能用, 不论 namespace
    assert u.can_use_skill("hermes:github:foo/bar") is True
    assert u.can_use_skill("hermes:hf:some-org/some-skill") is True


def test_admin_does_not_bypass_skill_rbac():
    """admin (非 sysadmin) 仍受 RBAC 限制."""
    u = User(
        sub="admin@x",
        role="admin",
        effective_allowed_skills=["catfish:*"],
    )
    assert u.can_use_skill("catfish:any") is True
    # admin 受 dept allowlist 限制
    assert u.can_use_skill("hermes:github:foo/bar") is False


def test_empty_skill_name_rejected():
    """空 name 在白名单收紧下拒绝."""
    u = User(sub="x@y", effective_allowed_skills=["catfish:*"])
    assert u.can_use_skill("") is False
    assert u.can_use_skill(None) is False  # type: ignore[arg-type]


# ── skills_loader (5/26 砍) ──────────────────────────────────
#
# 5/26 audit: gateway 不该扫 catfish 源码 skills/ — 那是去看员工 / 员工组织
# 内部的 skill, 走中央代码读员工本机违反边界. 整个 skills_loader 砍, 在
# hermes 进程由 catfish-memory plugin 通过 SkillProvider 接管.
#
# 这一段 (老的 SkillMeta.qualified_name + _infer_hermes_skill_namespace 单测)
# 不再有 module 可测, 改成"验 skills_loader 真是 stub" 防回归.


def test_skills_loader_module_is_stubbed_in_central():
    """5/26 兑现校验: 中央 skills_loader 必须是 fail-loud stub.

    防回归: SkillMeta 不能复活, _infer_hermes_skill_namespace 不能复活.
    """
    from catfish_gateway import skills_loader

    with pytest.raises(RuntimeError, match="DEPRECATED 5/26"):
        skills_loader.SkillMeta

    with pytest.raises(RuntimeError, match="DEPRECATED 5/26"):
        skills_loader._infer_hermes_skill_namespace

    with pytest.raises(RuntimeError, match="DEPRECATED 5/26"):
        skills_loader.load_skills
