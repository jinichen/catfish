"""skill_register 单测 (5/21 方案 1).

测幂等 + atomic write + 失败 silent 三个核心约束.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from catfish_tool_bridge import skill_register


@pytest.fixture
def patch_paths(tmp_path, monkeypatch):
    """所有路径指向 tmp_path 内, 隔离测试."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    fake_hermes = fake_home / ".hermes"
    fake_hermes.mkdir()
    config_path = fake_hermes / "config.yaml"
    local_skills = fake_home / ".catfish" / "skills"

    monkeypatch.setattr(skill_register, "HERMES_CONFIG_PATH", config_path)
    monkeypatch.setattr(skill_register, "LOCAL_SKILLS_ROOT", local_skills)
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    return {"home": fake_home, "config": config_path, "skills": local_skills}


def test_ensure_local_skills_dir_creates(patch_paths):
    """ensure_local_skills_dir 没目录时 mkdir."""
    assert not patch_paths["skills"].exists()
    out = skill_register.ensure_local_skills_dir()
    assert out.exists()
    assert out == patch_paths["skills"]


def test_register_no_config_yaml_skips(patch_paths):
    """hermes config.yaml 不存在时跳过 (不强造)."""
    assert not patch_paths["config"].exists()
    result = skill_register.ensure_external_dir_registered()
    assert result["ok"] is True
    assert result["action"] == "skipped_no_config"
    assert not patch_paths["config"].exists()  # 没造


def test_register_appends_to_empty(patch_paths):
    """config.yaml 有但没 skills 段 → 追加."""
    patch_paths["config"].write_text(
        "memory:\n  memory_char_limit: 2200\n", encoding="utf-8",
    )
    result = skill_register.ensure_external_dir_registered(patch_paths["skills"])
    assert result["ok"]
    assert result["action"] == "appended"

    with open(patch_paths["config"], encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    assert "skills" in cfg
    assert str(patch_paths["skills"].resolve()) in cfg["skills"]["external_dirs"]
    # 保留老字段
    assert cfg["memory"]["memory_char_limit"] == 2200


def test_register_idempotent(patch_paths):
    """跑两次, 第二次 noop."""
    patch_paths["config"].write_text(
        f"skills:\n  external_dirs:\n    - {patch_paths['skills'].resolve()}\n",
        encoding="utf-8",
    )
    result = skill_register.ensure_external_dir_registered(patch_paths["skills"])
    assert result["ok"]
    assert result["already_present"]
    assert result["action"] == "noop"

    # 不重复加
    with open(patch_paths["config"], encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    assert cfg["skills"]["external_dirs"].count(str(patch_paths["skills"].resolve())) == 1


def test_register_idempotent_via_resolve(patch_paths):
    """existing 是 ~ 展开前格式, 也要识别为同 path."""
    target_resolved = patch_paths["skills"].resolve()
    # 写一个 ~ 风格的 (用绝对路径作 surrogate, resolve 相等即可)
    patch_paths["config"].write_text(
        f"skills:\n  external_dirs:\n    - {target_resolved}\n",
        encoding="utf-8",
    )
    result = skill_register.ensure_external_dir_registered(patch_paths["skills"])
    assert result["already_present"]
    assert result["action"] == "noop"


def test_register_preserves_other_skills_keys(patch_paths):
    """skills 段下的其它字段 (e.g. enabled) 不被覆盖."""
    patch_paths["config"].write_text(
        "skills:\n  enabled: true\n  some_other: value\n",
        encoding="utf-8",
    )
    result = skill_register.ensure_external_dir_registered(patch_paths["skills"])
    assert result["ok"]

    with open(patch_paths["config"], encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    assert cfg["skills"]["enabled"] is True
    assert cfg["skills"]["some_other"] == "value"
    assert str(patch_paths["skills"].resolve()) in cfg["skills"]["external_dirs"]


def test_register_root_not_dict_silent_fail(patch_paths):
    """config.yaml 根是 list / 字符串 → silent log + ok=False, 不动盘."""
    patch_paths["config"].write_text("- just\n- a\n- list\n", encoding="utf-8")
    result = skill_register.ensure_external_dir_registered(patch_paths["skills"])
    assert result["ok"] is False
    assert "not dict" in result["error"].lower()
    # 没破原文件
    raw = patch_paths["config"].read_text(encoding="utf-8")
    assert raw.startswith("- just")


def test_register_corrupt_yaml_silent_fail(patch_paths):
    """yaml 解析失败 → silent log + ok=False, 不挂."""
    patch_paths["config"].write_text(
        "skills: {\n  external_dirs: [unclosed\n",
        encoding="utf-8",
    )
    result = skill_register.ensure_external_dir_registered(patch_paths["skills"])
    assert result["ok"] is False
    assert result["error"]
