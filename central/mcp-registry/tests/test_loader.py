"""ManifestRegistry loader 单测 (BL-D3 Phase 1)."""
from __future__ import annotations

from pathlib import Path

from catfish_mcp_registry.loader import ManifestRegistry


def test_load_all_4_manifests(registry: ManifestRegistry):
    """load_all 应该扫到 4 个 manifest (jira/gitlab/filesystem/time)."""
    assert registry.count == 4
    ids = sorted(m.id for m in registry.list_all())
    assert ids == ["filesystem", "gitlab", "jira", "time"]


def test_get_by_id(registry: ManifestRegistry):
    jira = registry.get("jira")
    assert jira is not None
    assert jira.name == "Jira"
    assert jira.auth_type == "oauth2"
    assert "engineering" in jira.allowed_dept


def test_get_unknown_returns_none(registry: ManifestRegistry):
    assert registry.get("nonexistent") is None


def test_list_for_dept_engineering(registry: ManifestRegistry):
    """engineering 部门可见所有 4 个 (jira/gitlab 限工程, filesystem/time 全员)."""
    matched = registry.list_for_dept("engineering")
    ids = sorted(m.id for m in matched)
    assert ids == ["filesystem", "gitlab", "jira", "time"]


def test_list_for_dept_finance_only_open(registry: ManifestRegistry):
    """finance 部门只见 allowed_dept 空 (= 全员) 的 (filesystem / time).

    jira/gitlab allowed_dept 是工程类, finance 看不见.
    """
    matched = registry.list_for_dept("finance")
    ids = sorted(m.id for m in matched)
    assert ids == ["filesystem", "time"]


def test_list_for_dept_none_only_open(registry: ManifestRegistry):
    """没传 dept (None) → 只看全员开放的, 防 dev 漏 dept 时露管控连接器."""
    matched = registry.list_for_dept(None)
    ids = sorted(m.id for m in matched)
    assert ids == ["filesystem", "time"]


def test_list_for_dept_empty_string(registry: ManifestRegistry):
    """空字符串 dept 等价 None — 跟上面一致."""
    matched = registry.list_for_dept("")
    ids = sorted(m.id for m in matched)
    assert ids == ["filesystem", "time"]


def test_load_missing_dir(tmp_path: Path):
    """manifests dir 不存在 → 不崩, count=0."""
    r = ManifestRegistry(tmp_path / "nonexistent")
    count = r.load_all()
    assert count == 0
    assert r.count == 0


def test_load_empty_dir(tmp_path: Path):
    """空目录 → count=0."""
    r = ManifestRegistry(tmp_path)
    count = r.load_all()
    assert count == 0


def test_load_invalid_yaml_skipped(tmp_path: Path):
    """非法 yaml 文件 → 跳过, 其他正常加载."""
    bad = tmp_path / "bad.yaml"
    bad.write_text("not valid: yaml: : :", encoding="utf-8")
    good = tmp_path / "good.yaml"
    good.write_text(
        """
id: good
name: Good
version: "1.0"
mcp_command:
  type: uvx
  package: mcp-server-good
""",
        encoding="utf-8",
    )
    r = ManifestRegistry(tmp_path)
    count = r.load_all()
    assert count == 1
    assert r.get("good") is not None
    assert r.get("bad") is None


def test_load_duplicate_ids_first_wins(tmp_path: Path):
    """两份 yaml 同 id → 第一个 (字母序在前) 生效, 后面跳过."""
    a = tmp_path / "a.yaml"
    b = tmp_path / "b.yaml"
    template = """
id: dup
name: {n}
version: "1.0"
mcp_command:
  type: uvx
  package: mcp-server-dup
"""
    a.write_text(template.format(n="From A"), encoding="utf-8")
    b.write_text(template.format(n="From B"), encoding="utf-8")
    r = ManifestRegistry(tmp_path)
    count = r.load_all()
    assert count == 1
    assert r.get("dup").name == "From A"
