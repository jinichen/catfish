"""Skills Hub storage 层单测 (五一 sprint 5/2 收尾).

覆盖:
- publish_skill 正常 + duplicate version + 路径越界
- get_skill latest / 指定 version
- list_skills (按 namespace 过滤)
- download_file 路径检查
- delete_skill_version + 自动清空 skill 目录
- audit jsonl
"""
from __future__ import annotations

from pathlib import Path

import pytest

from catfish_skills_hub import storage


@pytest.fixture(autouse=True)
def isolated_hub(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setenv("CATFISH_HUB_ROOT", str(tmp_path / "hub"))
    return tmp_path


def _skill_md(name: str, version: str, description: str = "") -> bytes:
    return (
        f'---\n'
        f'name: {name}\n'
        f'version: "{version}"\n'
        f'description: {description}\n'
        f'---\n# {name}\n'
    ).encode()


# ── publish_skill ──────────────────────────────────────────────


def test_publish_skill_basic() -> None:
    files = {
        "SKILL.md": _skill_md("hello-world", "1.0.0", "test"),
        "script.py": b"def render():\n    return {}\n",
    }
    result = storage.publish_skill("personal", files, published_by="alice@x.com")
    assert result["ok"] is True
    assert result["namespace"] == "personal"
    assert result["name"] == "hello-world"
    assert result["version"] == "1.0.0"
    assert result["files_count"] == 2


def test_publish_skill_missing_skill_md() -> None:
    result = storage.publish_skill("personal", {"x.py": b"x"}, published_by="a@x.com")
    assert result["ok"] is False
    assert "SKILL.md" in result["error"]


def test_publish_skill_no_name_in_frontmatter() -> None:
    files = {"SKILL.md": b"---\nversion: 1.0.0\n---\n"}
    result = storage.publish_skill("personal", files, published_by="a@x.com")
    assert result["ok"] is False
    assert "name" in result["error"].lower()


def test_publish_skill_duplicate_version() -> None:
    files = {"SKILL.md": _skill_md("foo", "1.0.0")}
    storage.publish_skill("personal", files, published_by="a@x.com")
    # 再发同版本 → 拒
    result = storage.publish_skill("personal", files, published_by="a@x.com")
    assert result["ok"] is False
    assert "已发布" in result["error"]


def test_publish_skill_multiple_versions() -> None:
    """同 skill 多版本应该都能发布."""
    storage.publish_skill("personal", {"SKILL.md": _skill_md("multi", "1.0.0")}, published_by="a@x.com")
    r2 = storage.publish_skill(
        "personal", {"SKILL.md": _skill_md("multi", "1.1.0")}, published_by="a@x.com",
    )
    r3 = storage.publish_skill(
        "personal", {"SKILL.md": _skill_md("multi", "2.0.0")}, published_by="a@x.com",
    )
    assert r2["ok"] is True
    assert r3["ok"] is True


def test_publish_skill_blocks_path_traversal() -> None:
    """file 名含 .. → 拒."""
    files = {
        "SKILL.md": _skill_md("evil", "1.0.0"),
        "../../../etc/passwd": b"x",
    }
    result = storage.publish_skill("personal", files, published_by="a@x.com")
    assert result["ok"] is False


# ── get_skill / list_skills ────────────────────────────────────


def test_get_skill_latest_picks_highest_version() -> None:
    storage.publish_skill("personal", {"SKILL.md": _skill_md("v", "1.0.0")}, published_by="a@x.com")
    storage.publish_skill("personal", {"SKILL.md": _skill_md("v", "2.0.0")}, published_by="a@x.com")
    storage.publish_skill("personal", {"SKILL.md": _skill_md("v", "1.5.0")}, published_by="a@x.com")
    info = storage.get_skill("personal", "v", version="latest")
    assert info is not None
    assert info["version"] == "2.0.0"


def test_get_skill_specific_version() -> None:
    storage.publish_skill("personal", {"SKILL.md": _skill_md("x", "1.0.0")}, published_by="a@x.com")
    storage.publish_skill("personal", {"SKILL.md": _skill_md("x", "2.0.0")}, published_by="a@x.com")
    info = storage.get_skill("personal", "x", version="1.0.0")
    assert info["version"] == "1.0.0"


def test_get_skill_not_found() -> None:
    assert storage.get_skill("personal", "nope") is None


def test_list_skills_with_namespace_filter() -> None:
    storage.publish_skill("personal", {"SKILL.md": _skill_md("p1", "1.0.0")}, published_by="a@x.com")
    storage.publish_skill("personal", {"SKILL.md": _skill_md("p2", "1.0.0")}, published_by="a@x.com")
    storage.publish_skill("dept", {"SKILL.md": _skill_md("d1", "1.0.0")}, published_by="a@x.com")

    all_skills = storage.list_skills()
    assert len(all_skills) == 3

    personal_only = storage.list_skills(namespace_filter="personal")
    assert len(personal_only) == 2


# ── download_file ──────────────────────────────────────────────


def test_download_file_works() -> None:
    files = {
        "SKILL.md": _skill_md("dl", "1.0.0"),
        "script.py": b"print('hi')",
    }
    storage.publish_skill("personal", files, published_by="a@x.com")
    content = storage.download_file("personal", "dl", "1.0.0", "script.py")
    assert content == b"print('hi')"


def test_download_file_blocks_traversal() -> None:
    storage.publish_skill("personal", {"SKILL.md": _skill_md("dl", "1.0.0")}, published_by="a@x.com")
    with pytest.raises(ValueError):
        storage.download_file("personal", "dl", "1.0.0", "../../../etc/passwd")


def test_download_file_not_found() -> None:
    storage.publish_skill("personal", {"SKILL.md": _skill_md("dl", "1.0.0")}, published_by="a@x.com")
    assert storage.download_file("personal", "dl", "1.0.0", "missing.py") is None


# ── delete_skill_version ───────────────────────────────────────


def test_delete_skill_version_works() -> None:
    storage.publish_skill("personal", {"SKILL.md": _skill_md("d", "1.0.0")}, published_by="a@x.com")
    result = storage.delete_skill_version("personal", "d", "1.0.0", deleted_by="admin@x.com")
    assert result["ok"] is True
    assert storage.get_skill("personal", "d", "1.0.0") is None


def test_delete_skill_version_keeps_other_versions() -> None:
    storage.publish_skill("personal", {"SKILL.md": _skill_md("k", "1.0.0")}, published_by="a@x.com")
    storage.publish_skill("personal", {"SKILL.md": _skill_md("k", "2.0.0")}, published_by="a@x.com")
    storage.delete_skill_version("personal", "k", "1.0.0", deleted_by="admin@x.com")
    # 2.0.0 还在
    assert storage.get_skill("personal", "k", "2.0.0") is not None


def test_delete_skill_version_not_found() -> None:
    result = storage.delete_skill_version("personal", "nope", "1.0.0", deleted_by="admin@x.com")
    assert result["ok"] is False


# ── audit ──────────────────────────────────────────────────────


def test_audit_records_publish() -> None:
    storage.publish_skill(
        "personal", {"SKILL.md": _skill_md("a", "1.0.0")}, published_by="alice@x.com",
    )
    audit = storage.read_audit()
    assert len(audit) == 1
    assert audit[0]["event"] == "publish"
    assert audit[0]["published_by"] == "alice@x.com"


def test_audit_records_delete() -> None:
    storage.publish_skill("personal", {"SKILL.md": _skill_md("a", "1.0.0")}, published_by="a@x.com")
    storage.delete_skill_version(
        "personal", "a", "1.0.0", deleted_by="admin@x.com", reason="测试删除",
    )
    audit = storage.read_audit()
    assert len(audit) == 2
    # 倒序: delete 在前
    assert audit[0]["event"] == "delete_version"
    assert audit[0]["reason"] == "测试删除"


# ── validate_seg ──────────────────────────────────────────────


def test_validate_namespace_blocks_dots() -> None:
    files = {"SKILL.md": _skill_md("x", "1.0.0")}
    with pytest.raises(ValueError):
        storage._validate_seg("..", "namespace")


def test_validate_version_allows_semver() -> None:
    storage._validate_seg("1.2.3", "version")
    storage._validate_seg("1.0.0-rc1", "version")
    storage._validate_seg("v0.1.0+beta", "version")


def test_validate_name_blocks_special_chars() -> None:
    with pytest.raises(ValueError):
        storage._validate_seg("foo/bar", "name")
    with pytest.raises(ValueError):
        storage._validate_seg("foo bar", "name")
