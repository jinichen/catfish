"""BL-MEMORY-DEDUPE-COMPRESS (5/17 凌晨, P2 #1+#2 lite 版) — 单元测.

测 catfish_memory_dedupe / catfish_memory_compress 工具行为:
  - 读 hermes USER.md / MEMORY.md (用 monkeypatch HOME 到 tmp)
  - Jaccard 相似度计算正确
  - 阈值控制
  - usage % 触发压缩建议
  - 不真改盘 (这俩工具只返建议, 不写)
"""
from __future__ import annotations

from pathlib import Path

import pytest

from catfish_tool_bridge import catfish_tools


@pytest.fixture
def hermes_home(tmp_path, monkeypatch):
    """造 ~/.hermes/memories/ tmp 结构."""
    monkeypatch.setenv("HOME", str(tmp_path))
    mem_dir = tmp_path / ".hermes" / "memories"
    mem_dir.mkdir(parents=True)
    return mem_dir


def _write_memory_file(mem_dir: Path, target: str, entries: list[str]) -> None:
    """写 hermes 格式 (§ 分隔) memory 文件."""
    filename = "USER.md" if target == "user" else "MEMORY.md"
    content = "\n§\n".join(entries)
    (mem_dir / filename).write_text(content, encoding="utf-8")


# ── catfish_memory_dedupe ─────────────────────────────────


def test_dedupe_no_entries_returns_empty(hermes_home):
    """没 memory 文件 → 空 suggestions."""
    result = catfish_tools.memory_dedupe({"target": "user"})
    assert result["ok"] is True
    assert result["suggestions_count"] == 0
    assert result["suggestions"] == []


def test_dedupe_finds_similar_entries(hermes_home):
    """高 Jaccard 相似 → 提议合并. 真实场景: 员工在两个 session 写"几乎一样"."""
    _write_memory_file(hermes_home, "user", [
        "员工在做 EIS 资质项目, 部门是 FFCS 数字鲶鱼小组",
        "员工在做 EIS 资质项目, 部门是 FFCS 数字鲶鱼小组, 角色技术负责",  # 几乎包含上面
        "员工儿子陈淡孜在韩国留学",  # 完全不相关
    ])
    # threshold 0.5 — 几乎一字不差的副本应该过
    result = catfish_tools.memory_dedupe({"target": "user", "threshold": 0.5})
    assert result["ok"] is True
    assert result["suggestions_count"] >= 1, f"应有重复 entry 建议, 实际: {result}"
    # 至少找到 EIS 那对
    found_eis = any(
        "EIS" in s["suggested_keep"] or "EIS" in s["suggested_remove"]
        for s in result["suggestions"]
    )
    assert found_eis, f"没找到 EIS 重复 entry: {result['suggestions']}"


def test_dedupe_high_threshold_filters_out(hermes_home):
    """阈值 0.9 几乎没 entry 通过."""
    _write_memory_file(hermes_home, "user", [
        "鸿波的老婆叫小芳",
        "鸿波妻子小芳",
    ])
    result = catfish_tools.memory_dedupe({"target": "user", "threshold": 0.95})
    assert result["ok"] is True
    # 阈值过高 → 0 suggestion (entries 不完全相同)
    assert result["suggestions_count"] == 0


def test_dedupe_scans_both_targets_by_default(hermes_home):
    """target=both → 扫 USER + MEMORY 两文件."""
    _write_memory_file(hermes_home, "user", ["U1", "U1 重复"])
    _write_memory_file(hermes_home, "memory", ["M1", "M1 重复"])
    result = catfish_tools.memory_dedupe({"threshold": 0.2})
    assert "user" in result["scanned_targets"]
    assert "memory" in result["scanned_targets"]


def test_dedupe_does_not_write_disk(hermes_home):
    """关键 PRIVACY-PRINCIPLES: dedupe 只返建议, 不真删盘文件."""
    _write_memory_file(hermes_home, "user", ["A", "A 重复"])
    user_file = hermes_home / "USER.md"
    content_before = user_file.read_text()

    catfish_tools.memory_dedupe({"target": "user", "threshold": 0.2})

    content_after = user_file.read_text()
    assert content_before == content_after, "dedupe 不应该改盘"


# ── catfish_memory_compress ─────────────────────────────────


def test_compress_invalid_target(hermes_home):
    """target 不是 user/memory → 返 error."""
    result = catfish_tools.memory_compress({"target": "invalid"})
    assert result["ok"] is False
    assert "error" in result


def test_compress_under_threshold_no_action(hermes_home):
    """chars < 80% limit → action_needed=False."""
    _write_memory_file(hermes_home, "user", ["短 entry"])  # 远低于 1375
    result = catfish_tools.memory_compress({"target": "user"})
    assert result["ok"] is True
    assert result["action_needed"] is False
    assert result["usage_pct"] < 80


def test_compress_over_threshold_suggests(hermes_home):
    """chars > 80% limit → 返压缩建议."""
    # USER limit 1375, 写 1200 chars (87%)
    long_entry = "x" * 600
    _write_memory_file(hermes_home, "user", [
        long_entry,
        long_entry,  # 2 条 600 chars = 1200 总
    ])
    result = catfish_tools.memory_compress({"target": "user", "oldest_n": 2})
    assert result["ok"] is True
    assert result["action_needed"] is True
    assert result["usage_pct"] >= 80
    assert result["oldest_n"] == 2
    assert len(result["oldest_entries"]) == 2
    assert "next_step_for_llm" in result


def test_compress_does_not_write_disk(hermes_home):
    """关键: compress 只返建议, 不真改盘."""
    long_entry = "x" * 600
    _write_memory_file(hermes_home, "user", [long_entry, long_entry])
    user_file = hermes_home / "USER.md"
    content_before = user_file.read_text()

    catfish_tools.memory_compress({"target": "user"})

    content_after = user_file.read_text()
    assert content_before == content_after, "compress 不应该改盘"


# ── 工具 schema 注册 ─────────────────────────────────


def test_dedupe_and_compress_in_native_tools():
    """工具 schema 出现在 CATFISH_NATIVE_TOOLS, is_native 识别."""
    names = {t["name"] for t in catfish_tools.CATFISH_NATIVE_TOOLS}
    assert "catfish_memory_dedupe" in names
    assert "catfish_memory_compress" in names
    assert catfish_tools.is_native("catfish_memory_dedupe")
    assert catfish_tools.is_native("catfish_memory_compress")
