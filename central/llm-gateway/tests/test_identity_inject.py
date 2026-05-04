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


# ─── BL-E11 命名权 + 人设 (五一 sprint 5/3) ───


def test_personalization_preamble_default_returns_empty():
    """全默认 (空 / 小鲶 / gentle) → 不加 preamble, 省 token"""
    assert identity_inject.build_personalization_preamble("", "") == ""
    assert identity_inject.build_personalization_preamble("小鲶", "gentle") == ""
    assert identity_inject.build_personalization_preamble("Catfish", "") == ""


def test_personalization_preamble_custom_name_only():
    """员工改名 '老李', 人设默认 → preamble 只含改名段"""
    out = identity_inject.build_personalization_preamble("老李", "")
    assert "员工偏好" in out
    assert "老李" in out
    assert "我是 老李" in out
    assert "我是小鲶" in out  # 提示模型不要再说这句
    # 不该含人设段 (默认 gentle 无 preset)
    assert "直爽" not in out
    assert "毒舌" not in out


def test_personalization_preamble_custom_personality_direct():
    """选 direct 风格, 名字默认"""
    out = identity_inject.build_personalization_preamble("", "direct")
    assert "直爽" in out
    assert "短" in out  # direct preset 提到说话短
    # 不该含改名段
    assert "起的名字" not in out


def test_personalization_preamble_custom_personality_roast():
    """选 roast 毒舌风格"""
    out = identity_inject.build_personalization_preamble("", "roast")
    assert "毒舌" in out
    assert "敏感话题" in out  # 关键边界提示


def test_personalization_preamble_both_name_and_personality():
    """名字 + 人设都改"""
    out = identity_inject.build_personalization_preamble("老李", "roast")
    assert "老李" in out
    assert "毒舌" in out
    # preamble 头一致
    assert out.startswith("# 员工偏好")


def test_personalization_preamble_unknown_personality_fallback():
    """传未知 personality → 静默忽略 (不抛错, 不加 preset 段)"""
    out = identity_inject.build_personalization_preamble("老李", "weirdmood")
    assert "老李" in out
    # 未知 personality 不该泄进 prompt
    assert "weirdmood" not in out


def test_header_agent_prefs_full():
    """header 都给齐"""
    h = {"x-catfish-agent-name": "老李", "x-catfish-agent-personality": "direct"}
    name, pers = identity_inject.header_agent_prefs(h)
    assert name == "老李"
    assert pers == "direct"


def test_header_agent_prefs_partial():
    """只给 name"""
    name, pers = identity_inject.header_agent_prefs({"x-catfish-agent-name": "小赵"})
    assert name == "小赵"
    assert pers == ""


def test_header_agent_prefs_empty():
    """都不给 → 双空"""
    assert identity_inject.header_agent_prefs({}) == ("", "")


def test_header_agent_prefs_unknown_personality_silent_fallback():
    """member 用客户端传了不在白名单的 personality → 静默 fallback gentle"""
    h = {"x-catfish-agent-personality": "tsundere"}
    _, pers = identity_inject.header_agent_prefs(h)
    assert pers == "gentle"  # 不抛错, 不让 weird 值泄到 preamble 函数


def test_inject_identity_with_agent_name(tmp_path, monkeypatch):
    """端到端: 注入 SOUL 时, agent_name 触发 preamble 在前"""
    monkeypatch.setenv("HOME", str(tmp_path))
    soul = tmp_path / ".hermes" / "SOUL.md"
    soul.parent.mkdir(parents=True)
    soul.write_text("# 你是小鲶\n你是 catfish 平台的 AI 副手.", encoding="utf-8")
    identity_inject._cache.clear()  # 清缓存

    out = identity_inject.inject_identity_if_needed(
        [{"role": "user", "content": "你好"}],
        agent_name="老李",
        agent_personality="direct",
    )
    # 第 0 条是 system, 含 preamble + SOUL
    assert out[0]["role"] == "system"
    sys_content = out[0]["content"]
    assert "员工偏好" in sys_content
    assert "老李" in sys_content
    assert "直爽" in sys_content
    # SOUL 在 preamble 之后
    soul_idx = sys_content.find("你是小鲶")
    pream_idx = sys_content.find("员工偏好")
    assert pream_idx < soul_idx
    # user 消息原样保留
    assert out[1]["role"] == "user"


def test_inject_identity_default_no_preamble(tmp_path, monkeypatch):
    """默认参数 → 不加 preamble (省 token)"""
    monkeypatch.setenv("HOME", str(tmp_path))
    soul = tmp_path / ".hermes" / "SOUL.md"
    soul.parent.mkdir(parents=True)
    soul.write_text("SOUL 内容", encoding="utf-8")
    identity_inject._cache.clear()

    out = identity_inject.inject_identity_if_needed([{"role": "user", "content": "hi"}])
    assert "员工偏好" not in out[0]["content"]
