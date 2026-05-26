"""STUB — DEPRECATED 5/26 晚 (BL-SESSION-META-PLUGIN-TAKEOVER).

# 原测试搬哪了

`edge/hermes-plugins/catfish-memory/tests/test_session_meta_tick.py` (Q3 SaaS
完整化时建). plugin 端 `_tick_session_meta()` 接管写, 测试该跟着 module 走.

# 为什么砍

老 gateway session_meta.tick() 写员工本机 session_meta.json. SaaS 化后
gateway 跑客户机房, 写不到员工 mac → 时间感段永远渲染空 (字段不更新).

catfish-memory hermes plugin (跑员工 mac, sync_turn hook 接管) 在
`_tick_session_meta()` 直接 update session_meta.json. plugin 读自己写
的, 闭环.

# fail-loud 防回归

中央 session_meta module 现在是 stub:
- tick() = no-op + 一次性 log warning
- 任何其他 attr = RuntimeError
"""
from __future__ import annotations

import pytest

from catfish_gateway import session_meta


def test_session_meta_tick_is_noop_after_plugin_takeover():
    """5/26 晚 兑现校验: tick() 不再写 fs, 调即 silent no-op (不抛错防回归)."""
    # 多次调不应该抛, 也不应该有副作用
    session_meta.tick()
    session_meta.tick()
    session_meta.tick()


def test_session_meta_module_is_fail_loud_stub_otherwise():
    """tick 之外的所有 attr (build_meta_block / _humanize_delta / meta_path 等)
    都该 fail-loud 防代码偷偷复活."""
    with pytest.raises(RuntimeError, match="DEPRECATED 5/26"):
        session_meta.build_meta_block

    with pytest.raises(RuntimeError, match="DEPRECATED 5/26"):
        session_meta._humanize_delta

    with pytest.raises(RuntimeError, match="DEPRECATED 5/26"):
        session_meta.meta_path

    with pytest.raises(RuntimeError, match="DEPRECATED 5/26"):
        session_meta.any_random_attr
