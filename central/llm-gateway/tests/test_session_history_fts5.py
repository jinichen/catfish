"""STUB — DEPRECATED 5/26 (inject_session_history 真死代码 + plugin 接管).

# 原测试搬哪了

无搬迁. 老 FTS5 相关性召回 (LIKE-based) 是 gateway 端死代码 (app.py 只 import
不调). 真 memory 注入由 catfish-memory hermes plugin 在 hermes 进程做.

# 防回归: 这个 stub 测试验中央 inject_session_history 真是 stub 没复活.
"""
from __future__ import annotations

import pytest


def test_inject_session_history_module_is_stubbed_in_central():
    """5/26 兑现校验: 中央 inject_session_history 必须是 fail-loud stub."""
    from catfish_gateway import inject_session_history as ish

    with pytest.raises(RuntimeError, match="DEPRECATED 5/26"):
        ish.inject_session_history

    with pytest.raises(RuntimeError, match="DEPRECATED 5/26"):
        ish._extract_query_tokens

    with pytest.raises(RuntimeError, match="DEPRECATED 5/26"):
        ish.get_relevant_sessions
