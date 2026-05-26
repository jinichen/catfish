"""STUB — DEPRECATED 5/26 (真死代码 + catfish_remember 已黑名单).

# 历史

老的 session_facts inject (~230 行). 5/16 `BL-MEMORY-CATFISH-REMEMBER-BLACKLIST`
后 catfish_remember tool 被禁用 (写本机 session_facts.json), 本模块的 inject
失去数据源. 5/19 BL-MEMORY-OWNERSHIP-FIX 切到 catfish-memory hermes plugin.

# 5/26 真砍

5/26 grep 验证: `app.py:83` 只 `from .session_facts import inject_session_facts`
但**全文 0 处 inject_session_facts( 调用**. 真死代码.

# 关联

- catfish-memory plugin: `edge/hermes-plugins/catfish-memory/catfish_memory.py`
- 5/16 BL-MEMORY-CATFISH-REMEMBER-BLACKLIST (catfish_remember 禁)
- 5/19 BL-MEMORY-OWNERSHIP-FIX
"""
from __future__ import annotations

_DEPRECATED_NOTICE = (
    "session_facts 5/26 砍 — 真死代码 (app.py 只 import 不调). "
    "catfish_remember tool 已黑名单, catfish-memory hermes plugin 接管 facts."
)


def __getattr__(name: str):  # noqa: D401
    raise RuntimeError(f"[DEPRECATED 5/26] {_DEPRECATED_NOTICE} (attr: {name})")
