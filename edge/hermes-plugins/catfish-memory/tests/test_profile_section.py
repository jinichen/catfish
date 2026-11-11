"""11/12: 每轮带上员工确认过的偏好画像 (catfish_memory_profile)。"""
from __future__ import annotations

import json
from pathlib import Path

from catfish_memory_profile import render_profile


def _write(home: Path, data) -> None:
    (home / "user_profile.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def test_只带确认过的值_待确认的不带(tmp_path):
    _write(tmp_path, {
        "writing_style.tone": {"value": "直接", "proposed_value": "委婉"},
        "writing_style.length_pref": {"value": None, "proposed_value": "长"},
        "personality.deference": {"value": "casual"},
    })
    out = render_profile(tmp_path)
    assert "- 语气: 直接" in out
    assert "委婉" not in out and "长" not in out.split("\n\n")[1]
    assert "- 称呼和口吻: 随意" in out  # 英文取值翻成中文
    assert "catfish_user_profile_propose" in out


def test_没有画像也教模型怎么记(tmp_path):
    out = render_profile(tmp_path)
    assert "还没有确认过的偏好" in out and "catfish_user_profile_propose" in out


def test_文件坏了不抛(tmp_path):
    (tmp_path / "user_profile.json").write_text("{坏", encoding="utf-8")
    assert "还没有确认过的偏好" in render_profile(tmp_path)


def test_不认识的字段和红线字段不带(tmp_path):
    _write(tmp_path, {"personal.health": {"value": "x"}, "foo.bar": {"value": "y"}})
    out = render_profile(tmp_path)
    assert "x" not in out.split("\n\n")[1] and "foo" not in out
