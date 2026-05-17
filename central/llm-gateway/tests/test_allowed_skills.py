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


# ── skills_loader namespace 推导 (单元测) ──────────────────────


def test_qualified_name_catfish():
    """catfish 自家 → 'catfish:<skill_path>'."""
    from catfish_gateway.skills_loader import SkillMeta
    from pathlib import Path  # noqa: PLC0415

    s = SkillMeta(
        skill_path="department/weekly-report",
        name="weekly-report",
        description="周报",
        skill_md_path=Path("/tmp/x/SKILL.md"),
        script_py_path=None,
        namespace="catfish",
    )
    assert s.qualified_name() == "catfish:department/weekly-report"


def test_qualified_name_hermes_github():
    """hermes github 的 namespace 自带完整 owner/repo, qualified_name 直接返."""
    from catfish_gateway.skills_loader import SkillMeta
    from pathlib import Path  # noqa: PLC0415

    s = SkillMeta(
        skill_path="frontend-slides",
        name="frontend-slides",
        description="HTML slides",
        skill_md_path=Path("/tmp/x/SKILL.md"),
        script_py_path=None,
        namespace="hermes:github:zarazhangrui/frontend-slides",
    )
    assert s.qualified_name() == "hermes:github:zarazhangrui/frontend-slides"


def test_qualified_name_hermes_local():
    """hermes:local:<name>  完整 namespace 已带 name."""
    from catfish_gateway.skills_loader import SkillMeta
    from pathlib import Path  # noqa: PLC0415

    s = SkillMeta(
        skill_path="my-skill",
        name="my-skill",
        description="员工自写",
        skill_md_path=Path("/tmp/x/SKILL.md"),
        script_py_path=None,
        namespace="hermes:local:my-skill",
    )
    assert s.qualified_name() == "hermes:local:my-skill"


def test_qualified_name_hermes_bundled():
    """hermes:bundled — namespace 没编 name, qualified_name 拼 skill_path."""
    from catfish_gateway.skills_loader import SkillMeta
    from pathlib import Path  # noqa: PLC0415

    s = SkillMeta(
        skill_path="read-file-skill",
        name="read-file-skill",
        description="hermes 自带",
        skill_md_path=Path("/tmp/x/SKILL.md"),
        script_py_path=None,
        namespace="hermes:bundled",
    )
    assert s.qualified_name() == "hermes:bundled:read-file-skill"


# ── namespace 推导 (filesystem) ────────────────────────────────


def test_infer_hermes_namespace_github(tmp_path):
    """有 .git/config 含 github.com → hermes:github:<owner>/<repo>."""
    from catfish_gateway.skills_loader import _infer_hermes_skill_namespace

    skill_dir = tmp_path / "frontend-slides"
    skill_dir.mkdir()
    git_dir = skill_dir / ".git"
    git_dir.mkdir()
    (git_dir / "config").write_text(
        "[remote \"origin\"]\n"
        "\turl = https://github.com/zarazhangrui/frontend-slides.git\n",
        encoding="utf-8",
    )

    ns = _infer_hermes_skill_namespace(skill_dir)
    assert ns == "hermes:github:zarazhangrui/frontend-slides"


def test_infer_hermes_namespace_github_ssh(tmp_path):
    """SSH remote URL 也能识别."""
    from catfish_gateway.skills_loader import _infer_hermes_skill_namespace

    skill_dir = tmp_path / "myskill"
    skill_dir.mkdir()
    (skill_dir / ".git").mkdir()
    (skill_dir / ".git" / "config").write_text(
        "[remote \"origin\"]\n"
        "\turl = git@github.com:NousResearch/hermes-agent.git\n",
        encoding="utf-8",
    )
    ns = _infer_hermes_skill_namespace(skill_dir)
    assert ns == "hermes:github:NousResearch/hermes-agent"


def test_infer_hermes_namespace_local(tmp_path):
    """没 .git → hermes:local:<name>."""
    from catfish_gateway.skills_loader import _infer_hermes_skill_namespace

    skill_dir = tmp_path / "my-self-written"
    skill_dir.mkdir()

    ns = _infer_hermes_skill_namespace(skill_dir)
    assert ns == "hermes:local:my-self-written"


def test_infer_hermes_namespace_hf(tmp_path):
    """有 .hf-skill 标记 → hermes:hf:<owner>/<name>."""
    from catfish_gateway.skills_loader import _infer_hermes_skill_namespace

    skill_dir = tmp_path / "video-gen"
    skill_dir.mkdir()
    (skill_dir / ".hf-skill").write_text("NousResearch/video-gen", encoding="utf-8")

    ns = _infer_hermes_skill_namespace(skill_dir)
    assert ns == "hermes:hf:NousResearch/video-gen"
