"""identity_inject 单测 —— 不依赖真 ~/.hermes 文件,monkeypatch HOME。"""
from __future__ import annotations

from pathlib import Path

import pytest

from catfish_gateway import identity_inject


@pytest.fixture(autouse=True)
def _clear_cache():
    """每个用例前清缓存，避免 fixture 之间串。"""
    identity_inject._cache.clear()
    yield
    identity_inject._cache.clear()


@pytest.fixture
def fake_hermes(tmp_path, monkeypatch) -> Path:
    """造一个假的 ~/.hermes 目录,通过 HERMES_HOME env 注入。"""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    return tmp_path


# ─── build_identity_content ───


def test_no_files_returns_empty(fake_hermes):
    """SOUL/USER 都没,返回空字符串。"""
    assert identity_inject.build_identity_content() == ""


def test_only_soul(fake_hermes):
    soul = fake_hermes / "SOUL.md"
    soul.write_text("我是小鲶。", encoding="utf-8")
    content = identity_inject.build_identity_content()
    assert "我是小鲶" in content
    assert content.startswith("# Identity (SOUL)")


def test_soul_plus_user_memory(fake_hermes):
    (fake_hermes / "SOUL.md").write_text("Persona: 小鲶", encoding="utf-8")
    (fake_hermes / "USER.md").write_text("User likes 直接 tone", encoding="utf-8")
    content = identity_inject.build_identity_content()
    assert "Identity" in content
    assert "User Memory" in content
    assert "小鲶" in content
    assert "直接 tone" in content


def test_memories_dir(fake_hermes):
    """~/.hermes/memories/*.md 全部读进 memory 区段。"""
    (fake_hermes / "SOUL.md").write_text("S", encoding="utf-8")
    mem_dir = fake_hermes / "memories"
    mem_dir.mkdir()
    (mem_dir / "preferences.md").write_text("Likes terse replies.", encoding="utf-8")
    (mem_dir / "team.md").write_text("Team uses 飞书 for notifications.", encoding="utf-8")

    content = identity_inject.build_identity_content()
    assert "preferences" in content  # ## preferences
    assert "team" in content
    assert "terse replies" in content
    assert "飞书" in content


# ─── inject_identity_if_needed ───


def test_inject_when_no_system(fake_hermes):
    (fake_hermes / "SOUL.md").write_text("我是小鲶。", encoding="utf-8")
    msgs = [{"role": "user", "content": "你好"}]
    out = identity_inject.inject_identity_if_needed(msgs)
    assert len(out) == 2
    assert out[0]["role"] == "system"
    assert "小鲶" in out[0]["content"]
    assert out[1] == {"role": "user", "content": "你好"}


def test_no_inject_when_system_present(fake_hermes):
    """Hermes 这种已自带 system 的客户端,gateway 不重复注入。"""
    (fake_hermes / "SOUL.md").write_text("我是小鲶。", encoding="utf-8")
    msgs = [
        {"role": "system", "content": "你是 Hermes 自己的系统提示。"},
        {"role": "user", "content": "你好"},
    ]
    out = identity_inject.inject_identity_if_needed(msgs)
    assert out == msgs  # 完全不动


def test_skip_flag_forces_no_inject(fake_hermes):
    (fake_hermes / "SOUL.md").write_text("我是小鲶。", encoding="utf-8")
    msgs = [{"role": "user", "content": "你好"}]
    out = identity_inject.inject_identity_if_needed(msgs, skip=True)
    assert out == msgs


def test_no_inject_when_no_files(fake_hermes):
    """SOUL.md 都没装,messages 原封不动。"""
    msgs = [{"role": "user", "content": "你好"}]
    out = identity_inject.inject_identity_if_needed(msgs)
    assert out == msgs


def test_empty_messages_works(fake_hermes):
    """edge case: client 发空 messages（不合规但不能崩）。"""
    (fake_hermes / "SOUL.md").write_text("S", encoding="utf-8")
    out = identity_inject.inject_identity_if_needed([])
    # 注入一条 system，user 就让客户端自己补
    assert len(out) == 1
    assert out[0]["role"] == "system"


def test_none_messages_works(fake_hermes):
    """edge case: client 完全没发 messages key。"""
    out = identity_inject.inject_identity_if_needed(None)  # type: ignore[arg-type]
    assert out == []


# ─── 缓存行为 ───


def test_cache_picks_up_file_changes(fake_hermes):
    """SOUL.md 改了,下次请求自动用新版（不需重启 gateway）。"""
    soul = fake_hermes / "SOUL.md"
    soul.write_text("V1", encoding="utf-8")
    assert "V1" in identity_inject.build_identity_content()

    # 改文件 - 用户编辑 SOUL.md 的真实场景
    import time
    time.sleep(0.01)  # 让 mtime 真变（同秒写入有时不变）
    soul.write_text("V2", encoding="utf-8")

    content = identity_inject.build_identity_content()
    assert "V2" in content
    assert "V1" not in content


# ─── header_skips_identity ───


def test_header_skip_true():
    headers = {"x-catfish-skip-identity": "true"}
    assert identity_inject.header_skips_identity(headers) is True


def test_header_skip_yes():
    assert identity_inject.header_skips_identity({"x-catfish-skip-identity": "yes"}) is True


def test_header_skip_one():
    assert identity_inject.header_skips_identity({"x-catfish-skip-identity": "1"}) is True


def test_header_skip_false():
    assert identity_inject.header_skips_identity({"x-catfish-skip-identity": "false"}) is False


def test_header_missing():
    assert identity_inject.header_skips_identity({}) is False
