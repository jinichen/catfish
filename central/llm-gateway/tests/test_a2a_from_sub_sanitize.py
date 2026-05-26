"""STUB — DEPRECATED 5/26 (跟模块 a2a_server 同批砍).

老测试 import catfish_gateway.a2a_server, 5/26 模块 stub 后 collect-time RuntimeError.
改成"验 stub 真抛 RuntimeError" 防回归 (跟 batch 0 test_recmode_aggregator 同款).
"""
from __future__ import annotations

import pytest


def test_a2a_server_module_is_stubbed_in_central():
    """a2a_server 必须是 fail-loud stub. 任何 attr 都抛 RuntimeError."""
    import catfish_gateway.a2a_server as m

    with pytest.raises(RuntimeError, match="DEPRECATED 5/26"):
        m.any_attr_should_raise  # 触发 __getattr__ stub
