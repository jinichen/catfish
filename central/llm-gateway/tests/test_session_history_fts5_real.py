"""STUB — DEPRECATED 5/26 (inject_session_history FTS5 实测同被砍).

同 test_session_history_fts5.py — gateway 端 inject_session_history 是 stub, 老
3 层 FTS5 降级测试无需保留, 真 memory 注入由 catfish-memory hermes plugin 做.
"""
from __future__ import annotations

import pytest


def test_inject_session_history_fts5_module_is_stubbed_in_central():
    """5/26 兑现校验: 中央 inject_session_history 的 FTS5 helpers 也必须是 stub."""
    from catfish_gateway import inject_session_history as ish

    with pytest.raises(RuntimeError, match="DEPRECATED 5/26"):
        ish._fts5_sanitize

    with pytest.raises(RuntimeError, match="DEPRECATED 5/26"):
        ish._which_fts5_table

    with pytest.raises(RuntimeError, match="DEPRECATED 5/26"):
        ish._query_via_fts5
