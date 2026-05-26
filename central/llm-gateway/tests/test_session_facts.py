"""STUB — DEPRECATED 5/26 (session_facts 真死代码 + catfish-memory plugin 接管).

# 原测试搬哪了

无搬迁. 替代方案是 edge/hermes-plugins/catfish-memory/ 接管 facts 注入 (在
hermes 进程, 不在 gateway). 那里有 plugin 自己的测试.

# 为什么砍

5/26 grep: gateway/app.py 只 `from .session_facts import inject_session_facts`
但全文 0 处调用 — 真死代码. 5/16 catfish_remember 黑名单后 session_facts
inject 失去数据源.

# 防回归: 这个 stub 测试验中央 session_facts 真是 stub 没复活.
"""
from __future__ import annotations

import pytest


def test_session_facts_module_is_stubbed_in_central():
    """5/26 兑现校验: 中央 session_facts 必须是 fail-loud stub."""
    from catfish_gateway import session_facts

    with pytest.raises(RuntimeError, match="DEPRECATED 5/26"):
        session_facts.read_session_facts  # 触发 __getattr__ stub

    with pytest.raises(RuntimeError, match="DEPRECATED 5/26"):
        session_facts.inject_session_facts

    with pytest.raises(RuntimeError, match="DEPRECATED 5/26"):
        session_facts.render_facts_block
