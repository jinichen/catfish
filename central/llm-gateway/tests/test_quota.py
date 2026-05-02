"""Quota 配额限流测试 (五一 sprint 5/3, BL-D9).

覆盖:
- yaml 加载 (默认 / overrides / 缺文件)
- record_usage / sum_tokens_*_since (sqlite store)
- check_quota 4 个维度
- 滑动窗口边界 (1 分钟前的事件不该计入 1 分钟内 quota)
- estimate_tokens
- friendly_quota_message
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from catfish_gateway import quota


@pytest.fixture(autouse=True)
def isolated_db_and_config(tmp_path: Path, monkeypatch) -> None:
    """每个测试用独立 sqlite + 独立 quotas.yaml."""
    monkeypatch.setenv("CATFISH_QUOTA_DB", str(tmp_path / "quota.db"))
    monkeypatch.setenv("CATFISH_QUOTAS_PATH", str(tmp_path / "quotas.yaml"))


def _write_quotas_yaml(tmp_path: Path, content: str) -> None:
    (tmp_path / "quotas.yaml").write_text(content, encoding="utf-8")


# ── yaml 加载 ────────────────────────────────────────────────


def test_load_default_when_no_yaml(tmp_path: Path) -> None:
    config = quota.load_quota_config()
    assert config.default_user.tokens_per_minute == 100_000
    assert config.default_user.tokens_per_day == 1_000_000
    assert config.user_overrides == {}
    assert config.model_quotas == {}
    assert config.department_quotas == {}


def test_load_with_defaults(tmp_path: Path) -> None:
    _write_quotas_yaml(tmp_path, """
defaults:
  per_user:
    tokens_per_minute: 50000
    tokens_per_day: 500000
  per_model:
    "catfish-public-qwen-flash":
      tokens_per_day: 5000000
  per_department:
    "研发部":
      tokens_per_day: 30000000
""")
    config = quota.load_quota_config()
    assert config.default_user.tokens_per_minute == 50_000
    assert config.model_quotas["catfish-public-qwen-flash"].tokens_per_day == 5_000_000
    assert config.department_quotas["研发部"].tokens_per_day == 30_000_000


def test_user_override_higher_than_default(tmp_path: Path) -> None:
    _write_quotas_yaml(tmp_path, """
defaults:
  per_user:
    tokens_per_minute: 100000
overrides:
  users:
    "ceo@ffcs.cn":
      tokens_per_minute: 1000000
""")
    config = quota.load_quota_config()
    assert config.per_user_for("ceo@ffcs.cn").tokens_per_minute == 1_000_000
    # 普通员工走默认
    assert config.per_user_for("alice@ffcs.cn").tokens_per_minute == 100_000


def test_department_override(tmp_path: Path) -> None:
    _write_quotas_yaml(tmp_path, """
defaults:
  per_department:
    "研发部":
      tokens_per_day: 50000000
overrides:
  departments:
    "研发部":
      tokens_per_day: 80000000
""")
    config = quota.load_quota_config()
    assert config.per_department_for("研发部").tokens_per_day == 80_000_000


# ── record_usage / sum_*_since ──────────────────────────────


def test_record_and_sum_user(tmp_path: Path) -> None:
    quota.record_usage("alice@x.com", "研发部", "qwen", 1000, 2000)
    quota.record_usage("alice@x.com", "研发部", "qwen", 500, 500)
    quota.record_usage("bob@x.com", "研发部", "qwen", 100, 100)

    cutoff = int(time.time() * 1000) - 60_000
    assert quota.sum_tokens_user_since("alice@x.com", cutoff) == 4000  # 1000+2000+500+500
    assert quota.sum_tokens_user_since("bob@x.com", cutoff) == 200


def test_sliding_window_old_events_excluded(tmp_path: Path) -> None:
    """近 1 分钟 quota 不应该包含 1 分 1 秒前的事件."""
    # 直接写一个 65 秒前的 event
    import sqlite3
    db_path = tmp_path / "quota.db"
    quota.record_usage("alice@x.com", "研发部", "qwen", 1000, 0)  # 触发 schema 创建

    old_ts = int((time.time() - 65) * 1000)  # 65 秒前
    conn = sqlite3.connect(str(db_path))
    with conn:
        conn.execute(
            "INSERT INTO quota_events VALUES (?, ?, ?, ?, ?, ?)",
            (old_ts, "alice@x.com", "研发部", "qwen", 99999, 0),
        )
    conn.close()

    cutoff_60s = int(time.time() * 1000) - 60_000
    used = quota.sum_tokens_user_since("alice@x.com", cutoff_60s)
    # 应该只算最近 1 分钟内的, 65 秒前的 99999 不算
    assert used == 1000  # 只有最初 record 的


def test_sum_model_since(tmp_path: Path) -> None:
    quota.record_usage("alice@x.com", "研发部", "qwen-flash", 1000, 0)
    quota.record_usage("bob@x.com", "研发部", "qwen-flash", 2000, 0)
    quota.record_usage("alice@x.com", "研发部", "gemini", 500, 0)

    cutoff = 0  # 全部
    assert quota.sum_tokens_model_since("qwen-flash", cutoff) == 3000
    assert quota.sum_tokens_model_since("gemini", cutoff) == 500


def test_sum_dept_since(tmp_path: Path) -> None:
    quota.record_usage("alice@x.com", "研发部", "qwen", 1000, 0)
    quota.record_usage("bob@x.com", "销售部", "qwen", 2000, 0)

    cutoff = 0
    assert quota.sum_tokens_dept_since("研发部", cutoff) == 1000
    assert quota.sum_tokens_dept_since("销售部", cutoff) == 2000


# ── check_quota 4 维度 ──────────────────────────────────────


def test_check_quota_allowed_when_empty(tmp_path: Path) -> None:
    qc = quota.check_quota("alice@x.com", "研发部", "qwen", 5000)
    assert qc.allowed
    assert qc.dimension == ""


def test_check_quota_per_user_minute_blocked(tmp_path: Path) -> None:
    _write_quotas_yaml(tmp_path, """
defaults:
  per_user:
    tokens_per_minute: 10000
""")
    # 已用 8000
    quota.record_usage("alice@x.com", "研发部", "qwen", 5000, 3000)
    # 再来 5000 → 8000+5000 > 10000 → 拒
    qc = quota.check_quota("alice@x.com", "研发部", "qwen", 5000)
    assert not qc.allowed
    assert qc.dimension == "per_user_minute"
    assert qc.current == 8000
    assert qc.limit == 10000
    assert qc.reset_at > int(time.time())


def test_check_quota_per_model_day_blocked(tmp_path: Path) -> None:
    _write_quotas_yaml(tmp_path, """
defaults:
  per_user:
    tokens_per_minute: 10000000
    tokens_per_day: 100000000
  per_model:
    "qwen-flash":
      tokens_per_day: 1000
""")
    quota.record_usage("alice@x.com", "研发部", "qwen-flash", 800, 0)
    qc = quota.check_quota("alice@x.com", "研发部", "qwen-flash", 500)
    assert not qc.allowed
    assert qc.dimension == "per_model_day"


def test_check_quota_per_dept_day_blocked(tmp_path: Path) -> None:
    _write_quotas_yaml(tmp_path, """
defaults:
  per_user:
    tokens_per_minute: 10000000
    tokens_per_day: 100000000
  per_department:
    "研发部":
      tokens_per_day: 5000
""")
    quota.record_usage("alice@x.com", "研发部", "qwen", 4500, 0)
    qc = quota.check_quota("bob@x.com", "研发部", "qwen", 1000)
    assert not qc.allowed
    assert qc.dimension == "per_dept_day"
    assert qc.current == 4500


def test_check_quota_zero_means_unlimited(tmp_path: Path) -> None:
    """user/model/dept 各维度 tokens_per_*=0 表示不限.

    场景: 内网 LLM (qwen-private) + 内部员工, 全维度不限制 token.
    """
    _write_quotas_yaml(tmp_path, """
defaults:
  per_user:
    tokens_per_minute: 0
    tokens_per_day: 0
  per_model:
    "qwen-private":
      tokens_per_day: 0
""")
    # 写超大量, 但任意维度 = 0 不应触发限流
    quota.record_usage("alice@x.com", "研发部", "qwen-private", 10_000_000, 0)
    qc = quota.check_quota("alice@x.com", "研发部", "qwen-private", 1_000_000)
    assert qc.allowed, f"全维度 0 应该不限, 实际 dimension={qc.dimension}"


def test_user_override_takes_effect(tmp_path: Path) -> None:
    """ceo override 高 quota 不被 default user 限."""
    _write_quotas_yaml(tmp_path, """
defaults:
  per_user:
    tokens_per_minute: 1000
overrides:
  users:
    "ceo@ffcs.cn":
      tokens_per_minute: 1000000
""")
    # ceo 用 5000 (高过默认 1000) 但他是 override 的高 quota
    quota.record_usage("ceo@ffcs.cn", "研发部", "qwen", 5000, 0)
    qc = quota.check_quota("ceo@ffcs.cn", "研发部", "qwen", 5000)
    assert qc.allowed  # 5000+5000 < 1000000


# ── estimate_tokens / friendly_quota_message ────────────────


def test_estimate_tokens_short_text() -> None:
    assert quota.estimate_tokens("") == 1000  # 最低 1000
    assert quota.estimate_tokens("hi") == 1000
    assert quota.estimate_tokens("a" * 10) == 1000  # < 4000 字符


def test_estimate_tokens_long_text() -> None:
    text = "a" * 8000  # 8000 字符 = ~2000 token
    assert quota.estimate_tokens(text) == 2000


def test_friendly_message_per_user_minute() -> None:
    qc = quota.QuotaCheck(
        allowed=False,
        dimension="per_user_minute",
        current=105000,
        limit=100000,
        reset_at=int(time.time()) + 30,
    )
    msg = quota.friendly_quota_message(qc, "alice@x.com", "qwen-flash")
    assert "分钟" in msg
    assert "catfish-private-main" in msg


def test_friendly_message_per_model_day() -> None:
    qc = quota.QuotaCheck(
        allowed=False,
        dimension="per_model_day",
        current=5000000,
        limit=5000000,
        reset_at=int(time.time()) + 86400,
    )
    msg = quota.friendly_quota_message(qc, "alice@x.com", "gemini-pro")
    assert "gemini-pro" in msg
    assert "qwen-flash" in msg or "private-main" in msg
