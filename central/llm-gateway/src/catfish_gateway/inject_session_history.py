"""STUB — DEPRECATED 5/26 (真死代码 + catfish-memory hermes plugin 接管).

# 历史

老的 gateway 端 inject memory provider (~527 行). 5/19 BL-MEMORY-OWNERSHIP-FIX 标
disable, catfish-memory hermes plugin 通过 prefetch 接管 memory 注入.

# 5/26 真砍

5/26 grep 验证: `app.py:79` 只 `from .inject_session_history import inject_session_history`
但**全文 0 处 inject_session_history( 调用**. 真死代码.

# 关联

- catfish-memory plugin: `edge/hermes-plugins/catfish-memory/catfish_memory.py`
- 5/19 BL-MEMORY-OWNERSHIP-FIX
- 5/23 BL-GATEWAY-DROP-LEGACY-SUMMARIZE (memory_distill / session_summarizer 同批真 rm)
"""
from __future__ import annotations

_DEPRECATED_NOTICE = (
    "inject_session_history 5/26 砍 — 真死代码 (app.py 只 import 不调). "
    "catfish-memory hermes plugin 通过 prefetch + sync_turn 接管 memory 注入."
)


def __getattr__(name: str):  # noqa: D401
    raise RuntimeError(f"[DEPRECATED 5/26] {_DEPRECATED_NOTICE} (attr: {name})")
