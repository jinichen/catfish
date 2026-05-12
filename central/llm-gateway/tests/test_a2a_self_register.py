"""测试 BL-FED2.2 (5/12) — gateway self_register 读 expertise.yaml confirmed tag.

主要保护的不变量:
1. yaml 不存在 → 返 [], 不抛异常
2. 解析坏 yaml → 返 [], 不阻塞 register
3. 只返 status=confirmed 的 tag (pending/rejected 跳过)
4. 不带 evidence_count / aliases / source 等元数据
5. tag 字符串自动 dedup + 限上限 (50 个)
"""
from __future__ import annotations

from pathlib import Path

import pytest

from catfish_gateway.a2a_self_register import _load_confirmed_expertise


@pytest.fixture(autouse=True)
def isolated_catfish_home(tmp_path: Path, monkeypatch):
    """每个测试独立 ~/.catfish/."""
    fake_home = tmp_path / "fake_catfish"
    fake_home.mkdir()
    monkeypatch.setenv("CATFISH_HOME", str(fake_home))
    yield fake_home


def _write_yaml(home: Path, content: str) -> None:
    p = home / "expertise.yaml"
    p.write_text(content, encoding="utf-8")


def test_no_yaml_returns_empty(isolated_catfish_home: Path):
    """文件不存在 → 返 [], 不抛."""
    assert _load_confirmed_expertise() == []


def test_only_confirmed_returned(isolated_catfish_home: Path):
    yaml_text = """extracted_at: 2026-05-12T10:00:00
auto_review_pending: true
tags:
  - tag: 资质管理
    confidence: 0.9
    evidence_count: 20
    status: confirmed
  - tag: 外勤报销
    confidence: 0.8
    evidence_count: 10
    status: pending
  - tag: 已废弃
    confidence: 0.5
    evidence_count: 1
    status: rejected
"""
    _write_yaml(isolated_catfish_home, yaml_text)
    tags = _load_confirmed_expertise()
    assert tags == ["资质管理"]


def test_multiple_confirmed(isolated_catfish_home: Path):
    yaml_text = """tags:
  - tag: 资质管理
    status: confirmed
  - tag: 外勤报销
    status: confirmed
  - tag: 合同审查
    status: confirmed
"""
    _write_yaml(isolated_catfish_home, yaml_text)
    tags = _load_confirmed_expertise()
    assert set(tags) == {"资质管理", "外勤报销", "合同审查"}
    assert len(tags) == 3


def test_no_confirmed_returns_empty(isolated_catfish_home: Path):
    yaml_text = """tags:
  - tag: a
    status: pending
  - tag: b
    status: rejected
"""
    _write_yaml(isolated_catfish_home, yaml_text)
    assert _load_confirmed_expertise() == []


def test_dedup(isolated_catfish_home: Path):
    """重复 tag 自动 dedup (理论上 expertise.py 已 dedup, 这里是双保险)."""
    yaml_text = """tags:
  - tag: 资质管理
    status: confirmed
  - tag: 资质管理
    status: confirmed
"""
    _write_yaml(isolated_catfish_home, yaml_text)
    assert _load_confirmed_expertise() == ["资质管理"]


def test_garbage_yaml_returns_empty(isolated_catfish_home: Path):
    """完全坏 yaml → 不抛, 返 []."""
    _write_yaml(isolated_catfish_home, "this is not yaml at all {{{ broken")
    # 我们的手写 parser 容忍非 yaml, 返 []
    result = _load_confirmed_expertise()
    assert result == []


def test_quoted_tag_string(isolated_catfish_home: Path):
    """tag 带引号也要剥."""
    yaml_text = """tags:
  - tag: "EIS Login"
    status: confirmed
  - tag: '财务报销'
    status: confirmed
"""
    _write_yaml(isolated_catfish_home, yaml_text)
    tags = _load_confirmed_expertise()
    assert "EIS Login" in tags
    assert "财务报销" in tags


def test_limit_50_tags(isolated_catfish_home: Path):
    """超过 50 个 confirmed tag 截断 (防异常 yaml 把 register payload 撑爆)."""
    lines = ["tags:"]
    for i in range(60):
        lines.append(f"  - tag: tag_{i}")
        lines.append("    status: confirmed")
    _write_yaml(isolated_catfish_home, "\n".join(lines))
    tags = _load_confirmed_expertise()
    assert len(tags) == 50


def test_no_evidence_or_aliases_in_output(isolated_catfish_home: Path):
    """**隐私铁律** — 输出**只**含 tag 字符串, 不带 evidence/aliases/source."""
    yaml_text = """tags:
  - tag: 资质管理
    confidence: 0.9
    evidence_count: 20
    aliases:
      - 资质
      - 资格管理
    status: confirmed
    last_reviewed_at: 2026-05-12T10:00:00
"""
    _write_yaml(isolated_catfish_home, yaml_text)
    tags = _load_confirmed_expertise()
    # 必须是 list[str], 不是 list[dict]
    for t in tags:
        assert isinstance(t, str)
    assert tags == ["资质管理"]


def test_long_tag_filtered(isolated_catfish_home: Path):
    """异常长 tag (>50 字符) 跳, 防 yaml 被注异常数据."""
    long_tag = "x" * 100
    yaml_text = f"""tags:
  - tag: {long_tag}
    status: confirmed
  - tag: 正常
    status: confirmed
"""
    _write_yaml(isolated_catfish_home, yaml_text)
    tags = _load_confirmed_expertise()
    assert long_tag not in tags
    assert "正常" in tags


def test_empty_yaml(isolated_catfish_home: Path):
    _write_yaml(isolated_catfish_home, "")
    assert _load_confirmed_expertise() == []


def test_yaml_no_tags_section(isolated_catfish_home: Path):
    """yaml 只有 metadata 没 tags 段."""
    _write_yaml(
        isolated_catfish_home,
        "extracted_at: 2026-05-12T10:00:00\nauto_review_pending: false\n",
    )
    assert _load_confirmed_expertise() == []
