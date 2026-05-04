"""session_meta 测试 — BL-E16 关系建立 (五一 sprint 5/3 晚).

覆盖:
  - tick 第一次: 建文件 + today_count=1
  - tick 同天: today_count + 1
  - tick 跨天: today_count reset 1
  - build_meta_block 没文件: 返空 (不报错, 不污染 prompt)
  - build_meta_block 同天 N 次: "今天第 N 次找我"
  - build_meta_block 跨天: "距上次 N 天 N 小时前"
  - _humanize_delta: 各时长格式
  - 损坏 json 文件: 返空 + 自动重建 (不抛)
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
    assert "last_chat_at" in data


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
    fake_now = datetime.fromisoformat(yesterday_data["last_chat_at"]) + timedelta(days=1)
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


# ─── build_meta_block ───


def test_build_meta_no_file_returns_empty(tmp_meta: Path) -> None:
    """没文件 (新装) → 空字符串, 不污染 prompt"""
    assert session_meta.build_meta_block() == ""


def test_build_meta_same_day_says_today_count(tmp_meta: Path) -> None:
    session_meta.tick()
    session_meta.tick()
    block = session_meta.build_meta_block()
    assert "Session Meta" in block
    assert "今天第 2 次" in block
    assert "距上次找我" in block
    assert "刚刚前" in block  # 测试中 tick 间隔几乎 0 → "刚刚"


def test_build_meta_cross_day_says_days_ago(
    tmp_meta: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """昨天 tick 过, 今天读 → 显示 '1 天 X 小时前'"""
    fake_yesterday = datetime(2026, 5, 2, 10, 0, tzinfo=timezone.utc).astimezone()
    monkeypatch.setattr(session_meta, "_now", lambda: fake_yesterday)
    session_meta.tick()
    # 切到 1 天 4 小时后
    fake_today = fake_yesterday + timedelta(days=1, hours=4)
    monkeypatch.setattr(session_meta, "_now", lambda: fake_today)
    block = session_meta.build_meta_block()
    assert "1 天 4 小时前" in block
    # today_date 不一致 → today_count 这行不该出 (避免误报昨天的次数当今天)
    assert "今天第" not in block


def test_build_meta_today_first_time(
    tmp_meta: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """今天没 tick 过 (新启动那一刻) → 不返今天计数"""
    # 没文件
    block = session_meta.build_meta_block()
    assert block == ""


# ─── _humanize_delta ───


@pytest.mark.parametrize("secs,expected", [
    (10, "刚刚"),
    (59, "刚刚"),
    (60, "1 分钟"),
    (3599, "59 分钟"),
    (3600, "1 小时"),
    (3 * 3600 + 30 * 60, "3 小时 30 分"),
    (86_400, "1 天"),
    (86_400 + 3 * 3600, "1 天 3 小时"),
    (3 * 86_400 + 12 * 3600, "3 天 12 小时"),
])
def test_humanize_delta(secs: int, expected: str) -> None:
    assert session_meta._humanize_delta(timedelta(seconds=secs)) == expected
