"""session_meta 测试 — BL-E16 关系建立 + 5/26 字段漂移修.

# 5/26 字段漂移修 (BL-E16 hidden bug)

老 tick 写 `last_chat_at`, 但 catfish-memory plugin `_render_session_meta`
(`edge/hermes-plugins/catfish-memory/catfish_memory.py`) 读的是 `last_chat_iso`.
字段错位导致 plugin 端"🕒 时间感"长期渲染空. 5/26 改 gateway tick 写
`last_chat_iso`, 跟 plugin 对齐.

# 5/26 同批砍 build_meta_block + _humanize_delta

老代码里 gateway 自己拼一个 "Session Meta" 段, 但 0 真 caller — 真正注入
"🕒 时间感" 段的是 catfish-memory hermes plugin (它读 tick() 写的 json).
gateway 的 build_meta_block 是死代码, 直接砍.

# 覆盖

  - tick 第一次: 建文件 + today_count=1 + last_chat_iso 字段
  - tick 同天: today_count + 1
  - tick 跨天: today_count reset 1
  - tick 不再写老的 last_chat_at 字段 (防字段漂移回归)
  - 损坏 json 文件: 返空 + 自动重建 (不抛)
  - 防回归: build_meta_block + _humanize_delta 不能复活
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from catfish_gateway import session_meta


@pytest.fixture
def tmp_meta(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """临时 meta 文件路径"""
    p = tmp_path / "session_meta.json"
    monkeypatch.setattr(session_meta, "meta_path", lambda: p)
    return p


# ─── tick ───


def test_tick_first_time_creates_file(tmp_meta: Path) -> None:
    session_meta.tick()
    assert tmp_meta.exists()
    data = json.loads(tmp_meta.read_text())
    assert data["today_count"] == 1
    assert "today_date" in data
    assert "last_chat_iso" in data, "5/26 字段对齐: 应写 last_chat_iso (跟 plugin 同源)"


def test_tick_no_longer_writes_old_field_name(tmp_meta: Path) -> None:
    """5/26 字段漂移修防回归: 老字段 last_chat_at 不能再写 (plugin 读 last_chat_iso)."""
    session_meta.tick()
    data = json.loads(tmp_meta.read_text())
    assert "last_chat_at" not in data, (
        "5/26 audit 改了 tick 写 last_chat_iso, last_chat_at 是老 bug 字段名. "
        "若复活意味着 plugin '🕒 时间感' 段又渲染空了 (BL-E16 hidden bug 回归)."
    )


def test_tick_same_day_increments(tmp_meta: Path) -> None:
    session_meta.tick()
    session_meta.tick()
    session_meta.tick()
    data = json.loads(tmp_meta.read_text())
    assert data["today_count"] == 3


def test_tick_new_day_resets(tmp_meta: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # 第一天 tick 3 次
    session_meta.tick()
    session_meta.tick()
    session_meta.tick()
    # 改"今天" 为下一天
    yesterday_data = json.loads(tmp_meta.read_text())
    fake_now = datetime.fromisoformat(yesterday_data["last_chat_iso"]) + timedelta(days=1)
    monkeypatch.setattr(session_meta, "_now", lambda: fake_now)
    session_meta.tick()
    data = json.loads(tmp_meta.read_text())
    assert data["today_count"] == 1


def test_tick_corrupted_json_silent_recover(
    tmp_meta: Path,
) -> None:
    """文件被外部破坏 → tick 不抛, 重建"""
    tmp_meta.parent.mkdir(parents=True, exist_ok=True)
    tmp_meta.write_text("not json {{{", encoding="utf-8")
    session_meta.tick()  # 不该抛
    data = json.loads(tmp_meta.read_text())
    assert data["today_count"] == 1
    assert "last_chat_iso" in data


# ─── 砍掉的 API 防回归 ───────────────────────────────────────


def test_build_meta_block_is_removed():
    """5/26 砍: build_meta_block 是死代码 (0 真 caller), plugin 自己渲染 '🕒 时间感'."""
    assert not hasattr(session_meta, "build_meta_block"), (
        "build_meta_block 5/26 砍 (死代码). 真渲染在 catfish-memory plugin "
        "_render_session_meta. 若复活意味着双重渲染 risk."
    )


def test_humanize_delta_is_removed():
    """5/26 砍: _humanize_delta 是 build_meta_block 的 helper, 同批砍."""
    assert not hasattr(session_meta, "_humanize_delta"), (
        "_humanize_delta 5/26 砍 (build_meta_block 的 helper, 一起死). "
        "若需要 humanize 时长, plugin 端自己实现 (gateway 不渲染)."
    )
