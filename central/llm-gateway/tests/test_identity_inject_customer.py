"""5/13 SOUL 分层 P1 — gateway 按 CATFISH_CUSTOMER 拼客户特定 SOUL.

铁律:
- CATFISH_CUSTOMER=ffcs (默认) → 注入 ~/.hermes/SOUL_FFCS.md
- 客户文件不存在 → 静默跳过, 只注 CORE SOUL
- 改 env → 切到 SOUL_BYD.md / 等 (大写文件名约定)
- 跟 BL-FED2 / BL-MM 注入流程不冲突 (拼在 CORE 后, system 唯一一条 message)
"""
from __future__ import annotations

from pathlib import Path

import pytest

from catfish_gateway import identity_inject


@pytest.fixture
def fake_hermes_home(tmp_path: Path, monkeypatch):
    home = tmp_path / "fake_hermes"
    home.mkdir()
    monkeypatch.setattr(identity_inject, "_hermes_home", lambda: home)
    # 清掉模块级 cache (上轮测试可能污染)
    identity_inject._cache._store.clear()
    yield home


def test_customer_soul_default_ffcs(fake_hermes_home: Path, monkeypatch):
    """没设 env → 默认 ffcs, 读 SOUL_FFCS.md."""
    monkeypatch.delenv("CATFISH_CUSTOMER", raising=False)
    (fake_hermes_home / "SOUL.md").write_text("# CORE\n通用铁律", encoding="utf-8")
    (fake_hermes_home / "SOUL_FFCS.md").write_text("# FFCS\n.ffcs.cn → http", encoding="utf-8")

    content = identity_inject.build_identity_content()
    assert "通用铁律" in content
    assert ".ffcs.cn → http" in content
    assert "Identity (SOUL_FFCS — 客户业务环境)" in content


def test_customer_soul_env_overrides(fake_hermes_home: Path, monkeypatch):
    """CATFISH_CUSTOMER=byd → 读 SOUL_BYD.md, 不读 SOUL_FFCS.md."""
    monkeypatch.setenv("CATFISH_CUSTOMER", "byd")
    (fake_hermes_home / "SOUL.md").write_text("# CORE", encoding="utf-8")
    (fake_hermes_home / "SOUL_FFCS.md").write_text("FFCS 内容不该被注", encoding="utf-8")
    (fake_hermes_home / "SOUL_BYD.md").write_text("# BYD\n.bytedance.net → https", encoding="utf-8")

    content = identity_inject.build_identity_content()
    assert ".bytedance.net" in content
    assert "FFCS 内容不该被注" not in content
    assert "Identity (SOUL_BYD — 客户业务环境)" in content


def test_customer_soul_missing_file_silent(fake_hermes_home: Path, monkeypatch):
    """CATFISH_CUSTOMER=meituan 但 SOUL_MEITUAN.md 不存在 → 静默, 只注 CORE."""
    monkeypatch.setenv("CATFISH_CUSTOMER", "meituan")
    (fake_hermes_home / "SOUL.md").write_text("# CORE\n通用", encoding="utf-8")

    content = identity_inject.build_identity_content()
    assert "通用" in content
    assert "MEITUAN" not in content
    # 不抛异常 — 没文件就 silent skip


def test_customer_soul_empty_string_falls_back_to_ffcs(fake_hermes_home: Path, monkeypatch):
    """env 设成空串 → 当默认 (ffcs) 处理."""
    monkeypatch.setenv("CATFISH_CUSTOMER", "")
    (fake_hermes_home / "SOUL.md").write_text("# CORE", encoding="utf-8")
    (fake_hermes_home / "SOUL_FFCS.md").write_text("FFCS 段", encoding="utf-8")

    content = identity_inject.build_identity_content()
    assert "FFCS 段" in content


def test_customer_soul_after_core(fake_hermes_home: Path, monkeypatch):
    """客户段拼在 CORE SOUL 后面, 不在前面 — LLM 先看通用再看客户."""
    monkeypatch.setenv("CATFISH_CUSTOMER", "ffcs")
    (fake_hermes_home / "SOUL.md").write_text("CORE_MARKER", encoding="utf-8")
    (fake_hermes_home / "SOUL_FFCS.md").write_text("CUSTOMER_MARKER", encoding="utf-8")

    content = identity_inject.build_identity_content()
    core_pos = content.find("CORE_MARKER")
    cust_pos = content.find("CUSTOMER_MARKER")
    assert core_pos >= 0 and cust_pos >= 0
    assert core_pos < cust_pos, "CORE SOUL 应该在客户 SOUL 前"


def test_no_core_no_customer_returns_empty(fake_hermes_home: Path, monkeypatch):
    """两份都没 → 返空串 (维持原行为, 静默跳过 inject)."""
    monkeypatch.delenv("CATFISH_CUSTOMER", raising=False)
    # 两份都不写
    assert identity_inject.build_identity_content() == ""


def test_only_core_no_customer_file_works(fake_hermes_home: Path, monkeypatch):
    """只有 CORE 没客户文件 → 只注 CORE (不退化, 兼容老部署)."""
    monkeypatch.delenv("CATFISH_CUSTOMER", raising=False)
    (fake_hermes_home / "SOUL.md").write_text("# CORE only", encoding="utf-8")

    content = identity_inject.build_identity_content()
    assert "CORE only" in content
    assert "客户业务环境" not in content


def test_case_insensitive_env(fake_hermes_home: Path, monkeypatch):
    """env=FFCS / Ffcs / ffcs 都对应 SOUL_FFCS.md (大写文件名约定)."""
    (fake_hermes_home / "SOUL.md").write_text("CORE", encoding="utf-8")
    (fake_hermes_home / "SOUL_FFCS.md").write_text("FFCS", encoding="utf-8")
    for env_val in ["ffcs", "FFCS", "Ffcs"]:
        monkeypatch.setenv("CATFISH_CUSTOMER", env_val)
        # 清缓存让重读
        identity_inject._cache._store.clear()
        content = identity_inject.build_identity_content()
        assert "FFCS" in content, f"env={env_val} should load SOUL_FFCS"
