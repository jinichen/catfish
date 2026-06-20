"""P3.5.43 — skill_sync 单测."""
from __future__ import annotations

from pathlib import Path

import pytest

from catfish_tool_bridge.recmode import skill_sync
from catfish_tool_bridge.recmode.skill_format import SkillManifest, render_skill_md, render_script_py


def _make_recmode_skill_dir(root: Path, slug: str, namespace: str = "personal",
                             author: str = "鲶鱼 RecMode") -> Path:
    """造一个 RecMode-生成形态的 skill 目录."""
    manifest = SkillManifest(
        name=slug,
        namespace=namespace,
        kind="procedural",
        description=f"test skill {slug}",
        triggers=["t1", "t2", "t3"],
        author=author,
    )
    d = root / namespace / slug
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(render_skill_md(manifest), encoding="utf-8")
    (d / "script.py").write_text(render_script_py(manifest), encoding="utf-8")
    return d


def _make_handwritten_skill_dir(root: Path, slug: str,
                                 author: str = "鸿波") -> Path:
    """造一个员工手写形态的 skill 目录 (author 不含 RecMode)."""
    d = root / slug
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        f"""---
name: {slug}
version: "1.0.0"
kind: procedural
deprecated: false
namespace: personal
author: {author}
description: hand-written test skill
triggers:
  - h1
  - h2
  - h3
---

# {slug}
""",
        encoding="utf-8",
    )
    return d


def test_is_recmode_generated_true(tmp_path: Path):
    src = _make_recmode_skill_dir(tmp_path / "src", "test-skill")
    assert skill_sync._is_recmode_generated(src / "SKILL.md") is True


def test_is_recmode_generated_false_for_handwritten(tmp_path: Path):
    d = _make_handwritten_skill_dir(tmp_path / "hermes", "weekly-report")
    assert skill_sync._is_recmode_generated(d / "SKILL.md") is False


def test_is_recmode_generated_false_when_missing(tmp_path: Path):
    assert skill_sync._is_recmode_generated(tmp_path / "no-such-file.md") is False


def test_sync_to_hermes_copies_files(tmp_path: Path):
    src = _make_recmode_skill_dir(tmp_path / "catfish", "my-new-skill")
    hermes = tmp_path / "hermes"
    target = skill_sync.sync_to_hermes(src, slug="my-new-skill", hermes_root=hermes)
    assert target == hermes / "my-new-skill"
    assert (target / "SKILL.md").is_file()
    assert (target / "script.py").is_file()


def test_sync_to_hermes_raises_collision_on_handwritten(tmp_path: Path):
    """hermes 仓库内已有同 slug 但是手写 (非 RecMode) → 拒绝覆盖."""
    src = _make_recmode_skill_dir(tmp_path / "catfish", "weekly-report")
    hermes = tmp_path / "hermes"
    _make_handwritten_skill_dir(hermes, "weekly-report")
    with pytest.raises(skill_sync.SkillSlugCollision) as exc_info:
        skill_sync.sync_to_hermes(src, slug="weekly-report", hermes_root=hermes)
    assert "不是 RecMode 生成" in str(exc_info.value)


def test_sync_to_hermes_overwrites_recmode_iteration(tmp_path: Path):
    """同 slug 但 hermes 内的也是 RecMode 生成的 → 安全覆盖 (员工重录的迭代)."""
    src_v1 = _make_recmode_skill_dir(tmp_path / "catfish_v1", "my-skill")
    hermes = tmp_path / "hermes"
    skill_sync.sync_to_hermes(src_v1, slug="my-skill", hermes_root=hermes)

    # v2: 重录 → 同 slug
    src_v2 = _make_recmode_skill_dir(tmp_path / "catfish_v2", "my-skill")
    # 区分: v2 SKILL.md content 改个标记
    (src_v2 / "SKILL.md").write_text(
        (src_v2 / "SKILL.md").read_text(encoding="utf-8") + "\nV2-MARKER\n",
        encoding="utf-8",
    )
    target = skill_sync.sync_to_hermes(src_v2, slug="my-skill", hermes_root=hermes)
    # v2 内容应该覆盖了 v1
    assert "V2-MARKER" in (target / "SKILL.md").read_text(encoding="utf-8")


def test_sync_to_hermes_raises_when_src_missing(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        skill_sync.sync_to_hermes(tmp_path / "no-such-dir", slug="x",
                                   hermes_root=tmp_path / "hermes")


def test_sync_to_hermes_raises_when_src_skill_md_missing(tmp_path: Path):
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(FileNotFoundError) as exc_info:
        skill_sync.sync_to_hermes(empty, slug="x", hermes_root=tmp_path / "hermes")
    assert "SKILL.md" in str(exc_info.value)


def test_unsync_from_hermes_recmode_succeeds(tmp_path: Path):
    src = _make_recmode_skill_dir(tmp_path / "catfish", "to-remove")
    hermes = tmp_path / "hermes"
    skill_sync.sync_to_hermes(src, slug="to-remove", hermes_root=hermes)
    assert (hermes / "to-remove").is_dir()
    removed = skill_sync.unsync_from_hermes("to-remove", hermes_root=hermes)
    assert removed is True
    assert not (hermes / "to-remove").exists()


def test_unsync_from_hermes_handwritten_refused(tmp_path: Path):
    """不能删手写 skill."""
    hermes = tmp_path / "hermes"
    _make_handwritten_skill_dir(hermes, "precious-handwritten")
    removed = skill_sync.unsync_from_hermes("precious-handwritten", hermes_root=hermes)
    assert removed is False
    assert (hermes / "precious-handwritten").is_dir()  # 还在


def test_unsync_from_hermes_missing_returns_false(tmp_path: Path):
    removed = skill_sync.unsync_from_hermes("never-existed", hermes_root=tmp_path / "hermes")
    assert removed is False
