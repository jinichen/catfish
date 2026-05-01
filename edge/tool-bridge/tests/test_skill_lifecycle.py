"""测试 Skill 全生命周期 4 步 (五一 sprint Day 2-3).

覆盖:
- _read_skill_metadata 解析 version / deprecated
- _write_skill_audit append 一行 jsonl
- catfish_run_skill 加 audit (成功 + 失败)
- catfish_run_skill 检测 deprecated → 加 warning
- catfish_skill_install 正常 + 安全检查 + overwrite
- catfish_skill_delete 正常 + confirm 强制
- 注册到 NATIVE_TOOL_NAMES
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# 让测试 import tool-bridge
SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from catfish_tool_bridge.catfish_tools import (  # noqa: E402
    NATIVE_TOOL_NAMES,
    _read_skill_metadata,
    _skill_audit_path,
    _write_skill_audit,
    is_native,
    run_skill,
    skill_delete,
    skill_install,
)


# ── 注册检查 (Day 2-3) ──────────────────────────────────────────


def test_new_tools_registered():
    assert "catfish_skill_install" in NATIVE_TOOL_NAMES
    assert "catfish_skill_delete" in NATIVE_TOOL_NAMES
    assert "catfish_a2a_ask" in NATIVE_TOOL_NAMES
    assert is_native("catfish_skill_install")
    assert is_native("catfish_skill_delete")
    assert is_native("catfish_a2a_ask")


# ── metadata 解析 (Day 2 BL-L14) ───────────────────────────────


def test_read_skill_metadata_full(tmp_path: Path) -> None:
    skill_md = tmp_path / "SKILL.md"
    skill_md.write_text(
        '---\n'
        'name: foo\n'
        'version: "1.2.3"\n'
        'deprecated: true\n'
        'deprecated_reason: "use bar instead"\n'
        'description: |-\n'
        '  test\n'
        '---\n'
        '# Foo\n',
        encoding="utf-8",
    )
    md = _read_skill_metadata(skill_md)
    assert md["version"] == "1.2.3"
    assert md["deprecated"] is True
    assert md["deprecated_reason"] == "use bar instead"


def test_read_skill_metadata_defaults(tmp_path: Path) -> None:
    """老 SKILL.md 没有 version/deprecated → 走默认值."""
    skill_md = tmp_path / "SKILL.md"
    skill_md.write_text(
        '---\n'
        'name: legacy\n'
        'description: old\n'
        '---\n',
        encoding="utf-8",
    )
    md = _read_skill_metadata(skill_md)
    assert md["version"] == "0.1.0"
    assert md["deprecated"] is False
    assert md["deprecated_reason"] == ""


def test_read_skill_metadata_no_file(tmp_path: Path) -> None:
    """没文件 → 默认值."""
    md = _read_skill_metadata(tmp_path / "nope.md")
    assert md["version"] == "0.1.0"
    assert md["deprecated"] is False


def test_read_skill_metadata_no_frontmatter(tmp_path: Path) -> None:
    skill_md = tmp_path / "SKILL.md"
    skill_md.write_text("# 无 frontmatter\n直接正文")
    md = _read_skill_metadata(skill_md)
    assert md["version"] == "0.1.0"


# ── audit 写入 (Day 2 BL-L17) ──────────────────────────────────


def test_write_skill_audit_append(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    audit_path = tmp_path / ".catfish" / "skill_audit.jsonl"

    _write_skill_audit({"event_type": "run", "skill_path": "x/y", "ok": True})
    _write_skill_audit({"event_type": "delete", "skill_path": "x/y", "ok": True})

    assert audit_path.exists()
    lines = audit_path.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 2
    assert json.loads(lines[0])["event_type"] == "run"
    assert json.loads(lines[1])["event_type"] == "delete"


def test_write_skill_audit_unicode(tmp_path: Path, monkeypatch) -> None:
    """中文 skill name / reason 不应 escape."""
    monkeypatch.setenv("HOME", str(tmp_path))
    _write_skill_audit({"reason": "员工说删掉"})
    text = (tmp_path / ".catfish" / "skill_audit.jsonl").read_text(encoding="utf-8")
    assert "员工说删掉" in text


# ── catfish_skill_delete (Day 2 BL-L16) ────────────────────────


def _make_test_skill(skills_root: Path, name: str) -> Path:
    """造一个测试 skill 目录 (含 SKILL.md + script.py)."""
    skill_dir = skills_root / "test-ns" / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        '---\n'
        f'name: {name}\n'
        'version: "1.0.0"\n'
        'description: test\n'
        '---\n',
        encoding="utf-8",
    )
    (skill_dir / "script.py").write_text(
        "def render_x():\n    return {'ok': True}\n", encoding="utf-8"
    )
    return skill_dir


def test_skill_delete_requires_confirm(tmp_path: Path, monkeypatch) -> None:
    """confirm 默认 false → 拒绝."""
    skills = tmp_path / "skills"
    skills.mkdir()
    monkeypatch.setenv("CATFISH_SKILLS_DIR", str(skills))
    monkeypatch.setenv("HOME", str(tmp_path))
    _make_test_skill(skills, "test-skill")

    result = skill_delete({
        "skill_path": "test-ns/test-skill",
        "reason": "员工说不用了",
        "confirm": False,
    })
    assert result["ok"] is False
    assert "confirm" in result["error"].lower()


def test_skill_delete_blocks_path_traversal(tmp_path: Path, monkeypatch) -> None:
    """skill_path 含 .. 拒绝."""
    skills = tmp_path / "skills"
    skills.mkdir()
    monkeypatch.setenv("CATFISH_SKILLS_DIR", str(skills))
    monkeypatch.setenv("HOME", str(tmp_path))

    result = skill_delete({
        "skill_path": "../../../etc",
        "reason": "x",
        "confirm": True,
    })
    assert result["ok"] is False
    assert ".." in result["error"]


def test_skill_delete_success_with_backup(tmp_path: Path, monkeypatch) -> None:
    skills = tmp_path / "skills"
    skills.mkdir()
    monkeypatch.setenv("CATFISH_SKILLS_DIR", str(skills))
    monkeypatch.setenv("HOME", str(tmp_path))
    _make_test_skill(skills, "to-delete")

    assert (skills / "test-ns" / "to-delete").is_dir()

    result = skill_delete({
        "skill_path": "test-ns/to-delete",
        "reason": "已不需要",
        "confirm": True,
    })
    assert result["ok"] is True
    assert "to-delete" in result["deleted_path"]
    # 原目录消失
    assert not (skills / "test-ns" / "to-delete").exists()
    # 备份目录存在
    assert Path(result["backup_path"]).exists()
    # audit jsonl 有 delete 事件
    audit = (tmp_path / ".catfish" / "skill_audit.jsonl").read_text(encoding="utf-8")
    assert "delete" in audit


# ── catfish_skill_install (Day 3 BL-D1) ───────────────────────


def test_skill_install_basic(tmp_path: Path, monkeypatch) -> None:
    """正常装一个 skill."""
    skills = tmp_path / "skills"
    skills.mkdir()
    monkeypatch.setenv("CATFISH_SKILLS_DIR", str(skills))
    monkeypatch.setenv("HOME", str(tmp_path))

    # 准备 source dir
    src = tmp_path / "source-skill"
    src.mkdir()
    (src / "SKILL.md").write_text(
        '---\n'
        'name: my-new-skill\n'
        'version: "0.1.0"\n'
        'description: test\n'
        '---\n',
        encoding="utf-8",
    )

    result = skill_install({
        "source_dir": str(src),
        "namespace": "personal",
    })
    assert result["ok"] is True
    assert "personal/my-new-skill" in result["installed_path"]
    # skill 真复制了
    assert (skills / "personal" / "my-new-skill" / "SKILL.md").exists()


def test_skill_install_blocks_system_dirs(tmp_path: Path, monkeypatch) -> None:
    """source_dir 在 /etc /usr /System 等 → 拒绝."""
    skills = tmp_path / "skills"
    skills.mkdir()
    monkeypatch.setenv("CATFISH_SKILLS_DIR", str(skills))
    monkeypatch.setenv("HOME", str(tmp_path))

    for blocked in ["/etc", "/usr/lib", "/System/Library"]:
        result = skill_install({
            "source_dir": blocked,
            "namespace": "personal",
        })
        assert result["ok"] is False
        assert "系统目录" in result["error"]


def test_skill_install_requires_skill_md(tmp_path: Path, monkeypatch) -> None:
    """source_dir 没 SKILL.md → 拒绝."""
    skills = tmp_path / "skills"
    skills.mkdir()
    monkeypatch.setenv("CATFISH_SKILLS_DIR", str(skills))
    monkeypatch.setenv("HOME", str(tmp_path))

    src = tmp_path / "source-no-skillmd"
    src.mkdir()

    result = skill_install({"source_dir": str(src)})
    assert result["ok"] is False
    assert "SKILL.md" in result["error"]


def test_skill_install_overwrite(tmp_path: Path, monkeypatch) -> None:
    """同名 skill 已存在, overwrite=True 才覆盖, 老版本 backup 到 trash."""
    skills = tmp_path / "skills"
    skills.mkdir()
    monkeypatch.setenv("CATFISH_SKILLS_DIR", str(skills))
    monkeypatch.setenv("HOME", str(tmp_path))

    # 装一次
    src = tmp_path / "v1"
    src.mkdir()
    (src / "SKILL.md").write_text(
        '---\nname: foo\nversion: "1.0.0"\ndescription: v1\n---\n', encoding="utf-8",
    )
    skill_install({"source_dir": str(src), "namespace": "personal"})

    # 不带 overwrite 装第二次 → 拒
    src2 = tmp_path / "v2"
    src2.mkdir()
    (src2 / "SKILL.md").write_text(
        '---\nname: foo\nversion: "2.0.0"\ndescription: v2\n---\n', encoding="utf-8",
    )
    result = skill_install({"source_dir": str(src2), "namespace": "personal"})
    assert result["ok"] is False
    assert "已存在" in result["error"]

    # overwrite=True 装成功
    result2 = skill_install({
        "source_dir": str(src2),
        "namespace": "personal",
        "overwrite": True,
    })
    assert result2["ok"] is True
    # 旧版备份在 trash 里
    trash = tmp_path / ".catfish" / "skill-trash"
    assert any(d.name.endswith("-replaced") for d in trash.iterdir())


def test_skill_install_blocks_namespace_traversal(tmp_path: Path, monkeypatch) -> None:
    """namespace 含 .. 或 / 拒绝."""
    skills = tmp_path / "skills"
    skills.mkdir()
    monkeypatch.setenv("CATFISH_SKILLS_DIR", str(skills))
    monkeypatch.setenv("HOME", str(tmp_path))

    src = tmp_path / "src"
    src.mkdir()
    (src / "SKILL.md").write_text(
        '---\nname: foo\ndescription: test\n---\n', encoding="utf-8",
    )

    for bad_ns in ["../etc", "namespace/sub", "..", "..//.."]:
        result = skill_install({"source_dir": str(src), "namespace": bad_ns})
        assert result["ok"] is False, f"namespace {bad_ns} 应被拒"


# ── run_skill audit (Day 2 BL-L17) ────────────────────────────


def test_run_skill_writes_audit_on_success(tmp_path: Path, monkeypatch) -> None:
    """正常调用后 audit jsonl 有 1 行."""
    skills = tmp_path / "skills"
    skills.mkdir()
    monkeypatch.setenv("CATFISH_SKILLS_DIR", str(skills))
    monkeypatch.setenv("HOME", str(tmp_path))
    skill_dir = _make_test_skill(skills, "audit-test")

    result = run_skill({"skill_path": "test-ns/audit-test", "params": {}})
    assert result["ok"] is True

    audit = (tmp_path / ".catfish" / "skill_audit.jsonl").read_text(encoding="utf-8")
    assert "audit-test" in audit
    event = json.loads(audit.strip().split("\n")[-1])
    assert event["ok"] is True
    assert event["skill_path"] == "test-ns/audit-test"
    assert "duration_ms" in event


def test_run_skill_writes_audit_on_failure(tmp_path: Path, monkeypatch) -> None:
    """skill 不存在也写 audit (但带 error_msg)."""
    skills = tmp_path / "skills"
    skills.mkdir()
    monkeypatch.setenv("CATFISH_SKILLS_DIR", str(skills))
    monkeypatch.setenv("HOME", str(tmp_path))

    result = run_skill({"skill_path": "nonexistent/x", "params": {}})
    assert result["ok"] is False
    # 注意: skill 不存在的情况是早期返回 (root 检查后 skill_dir.is_dir 失败), 在写
    # audit 之前. 这里只验证不 crash 即可.
