"""catfish_skill_backup 单测 —— 备份员工 skill 到 versions/ 目录。

跟截图无关, 只是历史上跟它们挤在同一个文件里。安全相关的一条: symlink 要拒
(防备份操作被引到目录外)。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from catfish_tool_bridge import catfish_tools


def test_skill_backup_in_native_tools_list() -> None:
    """catfish_skill_backup 必须出现在 CATFISH_NATIVE_TOOLS"""
    names = [t["name"] for t in catfish_tools.CATFISH_NATIVE_TOOLS]
    assert "catfish_skill_backup" in names


def test_skill_backup_required_fields() -> None:
    """skill_name 和 reason 都必填"""
    tool = next(
        t for t in catfish_tools.CATFISH_NATIVE_TOOLS
        if t["name"] == "catfish_skill_backup"
    )
    required = set(tool["input_schema"]["required"])
    assert required == {"skill_name", "reason"}


def test_skill_backup_missing_skill_name_returns_error() -> None:
    """没传 skill_name → error"""
    result = catfish_tools.skill_backup({"reason": "test"})
    assert result["type"] == "error"
    assert "skill_name" in result["error"]


def test_skill_backup_missing_reason_returns_error() -> None:
    """没传 reason → error"""
    result = catfish_tools.skill_backup({"skill_name": "ns/foo"})
    assert result["type"] == "error"
    assert "reason" in result["error"]


def test_skill_backup_invalid_format_returns_error() -> None:
    """skill_name 没含 / → error"""
    result = catfish_tools.skill_backup(
        {"skill_name": "no_slash", "reason": "x"}
    )
    assert result["type"] == "error"
    assert "格式错" in result["error"] or "/" in result["error"]


def test_skill_backup_skill_not_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """skill 不存在 → error"""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("USERPROFILE", raising=False)
    result = catfish_tools.skill_backup(
        {"skill_name": "productivity/no-such-skill", "reason": "test"}
    )
    assert result["type"] == "error"
    assert "找不到" in result["error"]


def test_skill_backup_success_creates_versions_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """skill 存在 → backup 到 .versions/<ts>.md, 返回 ok"""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("USERPROFILE", raising=False)

    # 建一个 fake skill
    skill_dir = tmp_path / ".hermes" / "skills" / "productivity" / "test-skill"
    skill_dir.mkdir(parents=True)
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text(
        "---\nname: test-skill\ndescription: test\n---\n# Body\n步骤 1: foo\n"
    )

    result = catfish_tools.skill_backup({
        "skill_name": "productivity/test-skill",
        "reason": "员工要求改",
    })
    assert result["type"] == "ok"
    assert result["skill_name"] == "productivity/test-skill"
    assert result["version_count"] == 1
    backup_path = Path(result["backup_path"])
    assert backup_path.exists()
    assert backup_path.parent.name == ".versions"
    # backup 内容跟原 SKILL.md 一致
    assert backup_path.read_text() == skill_md.read_text()


def test_skill_backup_symlink_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """catfish-* skill 通过 install.sh 软链, LLM 不能改 — 软链一律 deny"""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("USERPROFILE", raising=False)

    # 建 catfish 源代码 skill
    src_dir = tmp_path / "catfish-src" / "catfish-email"
    src_dir.mkdir(parents=True)
    (src_dir / "SKILL.md").write_text("---\nname: catfish-email\n---\nbody")

    # 软链到 ~/.hermes/skills/productivity/catfish-email (模拟 install.sh)
    skills_root = tmp_path / ".hermes" / "skills" / "productivity"
    skills_root.mkdir(parents=True)
    (skills_root / "catfish-email").symlink_to(src_dir)

    result = catfish_tools.skill_backup({
        "skill_name": "productivity/catfish-email",
        "reason": "尝试改 catfish skill",
    })
    assert result["type"] == "error"
    assert "软链" in result["error"]
    assert "catfish 自家" in result["error"]


def test_skill_backup_increments_version_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """连续 backup 同一个 skill, version_count 应递增"""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("USERPROFILE", raising=False)

    skill_dir = tmp_path / ".hermes" / "skills" / "ns" / "skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("v1")

    r1 = catfish_tools.skill_backup({"skill_name": "ns/skill", "reason": "first"})
    assert r1["version_count"] == 1

    # 等 1.1s 让 unix-ts 真变 (秒级精度)
    import time as _time
    _time.sleep(1.1)

    (skill_dir / "SKILL.md").write_text("v2")
    r2 = catfish_tools.skill_backup({"skill_name": "ns/skill", "reason": "second"})
    assert r2["version_count"] == 2


def test_dispatch_skill_backup_routes_correctly() -> None:
    """dispatch_native('catfish_skill_backup', ...) 应该路由到 skill_backup"""
    # missing skill_name → 走 skill_backup, 立即 error
    result = catfish_tools.dispatch_native("catfish_skill_backup", {})
    assert result["type"] == "error"
    assert "skill_name" in result["error"]


# ── 关于这次拆分 (8/13) ──────────────────────────────────────
#
# 原 tests/test_screenshot.py 1850 行 / 103 个 test, 一个文件装了六件不相干的事。
# 按**夹具依赖**切, 不按行号切: 先用 AST 算出每个 test 引用了哪些模块级 helper,
# 确认两簇 (_FakePage/_patch_connect 与 _FakeBrowserPage/_patch_browser_connect)
# 没有任何 test 同时用到, 才敢让它们各自独立成文件。
#
# 顺带修了 10 处 pyflakes B 类: List / Dict 在注解里用了但从没 import。
# 有 `from __future__ import annotations` 所以运行时不炸 —— 属于"看不见的债",
# 拆文件时每个文件重算 import, 正好清掉。
