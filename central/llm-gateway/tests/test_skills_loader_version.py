"""测试 skills_loader version / deprecated 字段 (五一 sprint Day 2 BL-L14, L15).

覆盖:
- _parse_skill_md 解析 version / deprecated / deprecated_reason
- 老 SKILL.md 没新字段走默认值
- format_skills_block 显示版本 + deprecation 警告
- discover_skills 返新字段
"""

from __future__ import annotations

from pathlib import Path

import pytest

from catfish_gateway.skills_loader import (
    SkillMeta,
    _parse_skill_md,
    discover_skills,
    format_skills_block,
)


def _write_skill(root: Path, name: str, frontmatter: str) -> Path:
    """造一个 skill 目录, 含 SKILL.md."""
    skill_dir = root / "test-ns" / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\n{frontmatter}\n---\n# {name}\n", encoding="utf-8"
    )
    return skill_dir


# ── 单字段解析 ──────────────────────────────────────────────────


def test_parse_with_version(tmp_path: Path) -> None:
    md = tmp_path / "SKILL.md"
    md.write_text(
        '---\nname: foo\nversion: "2.0.1"\ndescription: test\n---\n',
        encoding="utf-8",
    )
    parsed = _parse_skill_md(md)
    assert parsed is not None
    assert parsed["version"] == "2.0.1"
    assert parsed["deprecated"] is False
    assert parsed["deprecated_reason"] == ""


def test_parse_deprecated(tmp_path: Path) -> None:
    md = tmp_path / "SKILL.md"
    md.write_text(
        '---\nname: foo\nversion: "0.5.0"\ndeprecated: true\n'
        'deprecated_reason: "use bar instead"\ndescription: x\n---\n',
        encoding="utf-8",
    )
    parsed = _parse_skill_md(md)
    assert parsed is not None
    assert parsed["deprecated"] is True
    assert parsed["deprecated_reason"] == "use bar instead"


def test_parse_legacy_skill_md_no_new_fields(tmp_path: Path) -> None:
    """老 SKILL.md 没 version/deprecated → 默认值."""
    md = tmp_path / "SKILL.md"
    md.write_text(
        "---\nname: legacy\ndescription: old\n---\n",
        encoding="utf-8",
    )
    parsed = _parse_skill_md(md)
    assert parsed is not None
    assert parsed["version"] == "0.1.0"
    assert parsed["deprecated"] is False


def test_parse_missing_name_returns_none(tmp_path: Path) -> None:
    md = tmp_path / "SKILL.md"
    md.write_text(
        "---\ndescription: 缺 name\n---\n", encoding="utf-8"
    )
    assert _parse_skill_md(md) is None


# ── format_skills_block 显示 ───────────────────────────────────


def test_format_block_shows_non_default_version() -> None:
    skills = [
        SkillMeta(
            skill_path="x/y", name="foo",
            description="testing",
            skill_md_path=Path("/tmp/x"),
            script_py_path=None,
            version="1.2.3",
        ),
    ]
    block = format_skills_block(skills)
    assert "v1.2.3" in block


def test_format_block_default_version_hidden() -> None:
    skills = [
        SkillMeta(
            skill_path="x/y", name="foo",
            description="testing",
            skill_md_path=Path("/tmp/x"),
            script_py_path=None,
            # version 默认 "0.1.0"
        ),
    ]
    block = format_skills_block(skills)
    assert "v0.1.0" not in block  # 默认不显式 print


def test_format_block_deprecated_warning() -> None:
    skills = [
        SkillMeta(
            skill_path="x/old", name="old-skill",
            description="old",
            skill_md_path=Path("/tmp/x"),
            script_py_path=None,
            deprecated=True,
            deprecated_reason="use new one",
        ),
    ]
    block = format_skills_block(skills)
    assert "DEPRECATED" in block
    assert "use new one" in block


def test_format_block_deprecated_no_reason() -> None:
    """deprecated=true 但没填 reason → 显示通用警告."""
    skills = [
        SkillMeta(
            skill_path="x/old", name="x",
            description="d",
            skill_md_path=Path("/tmp/x"),
            script_py_path=None,
            deprecated=True,
        ),
    ]
    block = format_skills_block(skills)
    assert "DEPRECATED" in block
    assert "已下线" in block


# ── discover_skills (集成) ──────────────────────────────────────


def test_discover_skills_with_version(tmp_path: Path, monkeypatch) -> None:
    """造目录跑 discover_skills, 验 version/deprecated 透传."""
    monkeypatch.setenv("CATFISH_SKILLS_DIR", str(tmp_path))
    _write_skill(
        tmp_path, "alpha",
        'name: alpha\nversion: "1.0.0"\ndescription: alpha skill',
    )
    _write_skill(
        tmp_path, "beta",
        'name: beta\nversion: "0.9.0"\ndeprecated: true\n'
        'deprecated_reason: try gamma\ndescription: beta skill',
    )

    skills = discover_skills()
    by_name = {s.name: s for s in skills}
    assert "alpha" in by_name
    assert by_name["alpha"].version == "1.0.0"
    assert by_name["alpha"].deprecated is False
    assert "beta" in by_name
    assert by_name["beta"].version == "0.9.0"
    assert by_name["beta"].deprecated is True
    assert by_name["beta"].deprecated_reason == "try gamma"
