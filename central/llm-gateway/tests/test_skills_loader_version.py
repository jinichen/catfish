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


# ── BL-SKILLS-TIER1-SHRINK (5/25 鸿波) ──────────────────────────


def test_format_block_caps_description_at_80_chars() -> None:
    """单 skill desc 超 SKILL_DESC_CAP (80) 被截 + 加 … (Tier 1 摘要)."""
    from catfish_gateway.skills_loader import SKILL_DESC_CAP
    long_desc = "这是一个" + ("非常长的描述" * 30)  # 远超 80
    assert len(long_desc) > SKILL_DESC_CAP
    skills = [
        SkillMeta(
            skill_path="x/long", name="long",
            description=long_desc,
            skill_md_path=Path("/tmp/x"),
            script_py_path=None,
        ),
    ]
    block = format_skills_block(skills)
    # 完整 desc 不该在 Tier 1 出现 (走 _help)
    assert long_desc not in block
    # 截断标记
    assert "…" in block
    # skill_path 仍可见
    assert "x/long" in block


def test_format_block_single_line_per_skill() -> None:
    """5/25 Anthropic Progressive Disclosure: 单 skill ≤ 1 行 (Tier 1).

    防回退到老的 5 行/skill 格式 (描述展开 + _help 提示重复 + 空行).
    """
    skills = [
        SkillMeta(
            skill_path=f"ns/skill{i}",
            name=f"skill{i}",
            description=f"短描述 {i}",
            skill_md_path=Path(f"/tmp/{i}"),
            script_py_path=None,
        )
        for i in range(5)
    ]
    block = format_skills_block(skills)
    # 每个 skill 1 行 = 5 个 skill 占 5 行
    skill_lines = [ln for ln in block.split("\n") if ln.startswith("- `ns/skill")]
    assert len(skill_lines) == 5, (
        f"应该 5 行 skill (单 skill 1 行), 实际 {len(skill_lines)} 行: {skill_lines}"
    )


def test_format_block_help_hint_in_header_not_per_skill() -> None:
    """`_help: True` 提示在 header 出现一次, 不再每 skill 重复打."""
    skills = [
        SkillMeta(
            skill_path=f"ns/skill{i}",
            name=f"skill{i}",
            description=f"d{i}",
            skill_md_path=Path(f"/tmp/{i}"),
            script_py_path=None,
        )
        for i in range(10)
    ]
    block = format_skills_block(skills)
    # 期望: _help: True 出现次数 << skill 数 (header 提一两次, 不会每 skill 一次)
    n_help = block.count("_help")
    n_help_true = block.count("'_help': True")
    assert n_help_true <= 3, (
        f"_help: True 应该 header 集中提 (≤3 次), 实际 {n_help_true} 次 → 还在每 skill 重复打"
    )
    assert n_help >= 1, "header 必须仍提到 _help"


def test_format_block_total_size_scales_3x_compression() -> None:
    """100 skill 总长度应在 14K chars 内 (相比老格式 ~35K, 至少 2.5x 压缩).

    Anthropic Tier 1 推荐 30-60 tokens / skill. 我们 110 chars / skill ≈ 30 tokens
    (Chinese mixed) — 命中 Anthropic 下限. 加 header ~1500 chars 总 ~12.6K chars
    (~3K tokens). 老格式 100 skill 约 25-35K chars (~6-8K tokens).
    """
    skills = [
        SkillMeta(
            skill_path=f"ns/skill-{i:03d}",
            name=f"skill-{i:03d}",
            description=(
                "Generate beautiful magazine-style PowerPoint slides with "
                "professional layouts and design templates for executives."
            ),  # 100 chars (典型 desc 长度, 超 SKILL_DESC_CAP=80 → 被截)
            skill_md_path=Path(f"/tmp/{i}"),
            script_py_path=None,
        )
        for i in range(100)
    ]
    block = format_skills_block(skills)
    assert len(block) < 14_000, (
        f"100 skill 总长 {len(block)} chars 超出预期 3x 压缩目标 (<14K). "
        f"BL-SKILLS-TIER1-SHRINK 没真起作用?"
    )
    # 单 skill 平均 chars 检查 (扣 header 后)
    skill_lines = [ln for ln in block.split("\n") if ln.startswith("- `ns/skill")]
    assert len(skill_lines) == 100
    avg_per_skill = sum(len(ln) for ln in skill_lines) / 100
    assert avg_per_skill < 130, (
        f"单 skill 平均 {avg_per_skill:.0f} chars 超出 130 上限 (Anthropic Tier 1)"
    )


# ── BL-SKILLS-TIER1-FOLD (5/25 鸿波) ── Phase 2 namespace 分组


def _mk(skill_path: str, namespace: str = "catfish", **kw) -> SkillMeta:
    """造测试用 SkillMeta. 默认 namespace=catfish."""
    return SkillMeta(
        skill_path=skill_path,
        name=skill_path.split("/")[-1],
        description=kw.pop("description", f"desc for {skill_path}"),
        skill_md_path=Path(f"/tmp/{skill_path}"),
        script_py_path=None,
        namespace=namespace,
        **kw,
    )


def test_format_block_groups_by_namespace() -> None:
    """混合 namespace → 输出按 namespace 分段, catfish 排首位."""
    skills = [
        _mk("hermes-skill-a", namespace="hermes:bundled"),
        _mk("local-skill", namespace="hermes:local:my-skill"),
        _mk("dept/leadership", namespace="catfish"),
        _mk("owner/repo", namespace="hermes:github:owner/repo"),
    ]
    block = format_skills_block(skills)
    # 4 个 namespace 段都出现 (catfish 必须排第一)
    idx_catfish = block.find("#### 🎯 catfish")
    idx_bundled = block.find("#### 🧰 hermes:bundled")
    idx_github = block.find("#### 🐱 hermes:github")
    idx_local = block.find("#### 📝 hermes:local")
    assert idx_catfish > 0, "catfish 段必须存在"
    assert idx_bundled > idx_catfish, "hermes:bundled 必须排在 catfish 之后"
    assert idx_github > idx_bundled
    assert idx_local > idx_github


def test_format_block_skips_empty_namespaces() -> None:
    """只有 catfish skill → 只渲染 catfish 段, 不打空的 hermes:* 段 header."""
    skills = [_mk(f"x/skill{i}") for i in range(3)]
    block = format_skills_block(skills)
    assert "#### 🎯 catfish (3 个" in block
    # 别的 namespace 段头不该出现 (header 铁律段里可能提到 hermes:local 名字, 不算)
    assert "#### 🧰 hermes:bundled" not in block
    assert "#### 📝 hermes:local" not in block
    assert "#### 🐱 hermes:github" not in block
    assert "#### 🤗 hermes:hf" not in block


def test_format_block_namespace_count_in_header() -> None:
    """每个 namespace 段头显示该 ns 的 skill 数."""
    skills = [
        _mk("a", namespace="catfish"),
        _mk("b", namespace="catfish"),
        _mk("c", namespace="hermes:bundled"),
    ]
    block = format_skills_block(skills)
    assert "catfish (2 个" in block
    assert "hermes:bundled (1 个" in block


def test_namespace_group_key_normalizes_long_namespaces() -> None:
    """hermes:github:owner/repo → 归一到 hermes:github group."""
    from catfish_gateway.skills_loader import _namespace_group_key
    s1 = _mk("a", namespace="hermes:github:zarazhangrui/frontend")
    s2 = _mk("b", namespace="hermes:github:owner2/repo2")
    s3 = _mk("c", namespace="hermes:hf:openai/something")
    s4 = _mk("d", namespace="hermes:local:my-skill")
    s5 = _mk("e", namespace="catfish")
    assert _namespace_group_key(s1) == "hermes:github"
    assert _namespace_group_key(s2) == "hermes:github"
    assert _namespace_group_key(s3) == "hermes:hf"
    assert _namespace_group_key(s4) == "hermes:local"
    assert _namespace_group_key(s5) == "catfish"


def test_format_block_preserves_relative_order_within_namespace() -> None:
    """同 namespace 内的 skill 保 caller 给的顺序 (discover 已排过 qualified_name)."""
    skills = [
        _mk("zzz/late", namespace="catfish"),
        _mk("aaa/early", namespace="catfish"),
        _mk("mmm/middle", namespace="catfish"),
    ]
    block = format_skills_block(skills)
    # 因为我们不再排序, caller 给啥顺序就啥顺序
    catfish_section = block[block.find("#### 🎯"):block.find("####", block.find("#### 🎯") + 1) if block.count("####") > 1 else len(block)]
    idx_zzz = catfish_section.find("zzz/late")
    idx_aaa = catfish_section.find("aaa/early")
    idx_mmm = catfish_section.find("mmm/middle")
    assert idx_zzz < idx_aaa < idx_mmm, (
        f"应保 caller 给的顺序 (zzz → aaa → mmm), 实际 zzz={idx_zzz} aaa={idx_aaa} mmm={idx_mmm}"
    )


def test_format_block_unknown_namespace_fallback() -> None:
    """未知 namespace (理论不该有, 但兜底) → 渲染在末尾 ❓ 段."""
    skills = [
        _mk("a", namespace="catfish"),
        _mk("b", namespace="some-unknown-ns"),
    ]
    block = format_skills_block(skills)
    assert "❓ some-unknown-ns" in block
    # catfish 仍排在前面
    assert block.find("catfish") < block.find("some-unknown-ns")


def test_format_block_deprecated_compact_format() -> None:
    """deprecated skill 也走单行格式, deprecated_reason 替代 desc 位置."""
    skills = [
        SkillMeta(
            skill_path="x/old", name="old-skill",
            description="原描述",
            skill_md_path=Path("/tmp/x"),
            script_py_path=None,
            deprecated=True,
            deprecated_reason="改用 new-skill",
        ),
    ]
    block = format_skills_block(skills)
    assert "DEPRECATED" in block
    # deprecated_reason 在 Tier 1 行内, 不是单独段落
    deprecated_lines = [
        ln for ln in block.split("\n") if "DEPRECATED" in ln
    ]
    assert len(deprecated_lines) == 1, "deprecated 也应单行"
    assert "改用 new-skill" in deprecated_lines[0]


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
