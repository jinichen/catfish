"""10/29 缓存命中 token: 记进 quota_events (tokens_cache_read), 审计 by_model 拆输入 / 缓存命中 / 输出。

从 test_quota.py 拆出来 (那个文件已到 800 行红线)。
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from catfish_gateway import quota


@pytest.fixture(autouse=True)
def isolated_db_and_config(tmp_path: Path, monkeypatch) -> None:
    """每个测试用独立 sqlite + 独立 quotas.yaml (同 test_quota.py)."""
    monkeypatch.setenv("CATFISH_QUOTA_DB", str(tmp_path / "quota.db"))
    monkeypatch.setenv("CATFISH_QUOTAS_PATH", str(tmp_path / "quotas.yaml"))



def test_record_usage_stores_cache_read_clamped(tmp_path: Path) -> None:
    import sqlite3

    quota.record_usage("alice@x.com", "研发部", "qwen", 1000, 50, tokens_cache_read=800)
    # 上游怪数 cached > prompt：夹到 tokens_in，计费不出负数
    quota.record_usage("alice@x.com", "研发部", "qwen", 100, 10, tokens_cache_read=999)
    # 老调用方不传：默认 0
    quota.record_usage("alice@x.com", "研发部", "qwen", 300, 30)
    conn = sqlite3.connect(str(tmp_path / "quota.db"))
    rows = conn.execute("SELECT tokens_in, tokens_cache_read FROM quota_events ORDER BY rowid").fetchall()
    conn.close()
    assert rows == [(1000, 800), (100, 100), (300, 0)]


def test_old_sqlite_db_gets_cache_column(tmp_path: Path) -> None:
    """老测试库 (6 列) 打开时自动补 tokens_cache_read 列。"""
    import sqlite3

    db = tmp_path / "quota.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE quota_events (ts INTEGER NOT NULL, user_email TEXT NOT NULL, department TEXT NOT NULL, "
        "model TEXT NOT NULL, tokens_in INTEGER NOT NULL, tokens_out INTEGER NOT NULL)"
    )
    conn.commit()
    conn.close()
    quota.record_usage("bob@x.com", "研发部", "qwen", 10, 1, tokens_cache_read=4)
    conn = sqlite3.connect(str(db))
    assert conn.execute("SELECT tokens_cache_read FROM quota_events").fetchall() == [(4,)]
    conn.close()


def test_audit_by_model_splits_in_out_cache() -> None:
    quota.record_usage("alice@x.com", "研发部", "qwen", 1000, 100, tokens_cache_read=600)
    quota.record_usage("bob@x.com", "研发部", "qwen", 500, 50, tokens_cache_read=0)
    quota.record_usage("bob@x.com", "研发部", "deepseek", 200, 20, tokens_cache_read=150)
    cutoff = int(time.time() * 1000) - 60_000

    for summary in (
        quota.audit_summary_global_since(cutoff),
        quota.audit_summary_dept_since("研发部", cutoff),
    ):
        rows = {m["model"]: m for m in summary["by_model"]}
        assert rows["qwen"] == {
            "model": "qwen", "count": 2, "total_tokens": 1650,
            "tokens_in": 1500, "tokens_out": 150, "tokens_cache_read": 600,
        }
        assert rows["deepseek"]["tokens_cache_read"] == 150
        assert rows["deepseek"]["total_tokens"] == 220

    mine = {m["model"]: m for m in quota.audit_summary_user_since("bob@x.com", cutoff)["by_model"]}
    assert mine["qwen"]["tokens_in"] == 500 and mine["qwen"]["tokens_cache_read"] == 0
    assert mine["deepseek"]["tokens_out"] == 20
