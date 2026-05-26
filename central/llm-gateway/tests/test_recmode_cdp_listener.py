"""STUB — BL-RECMODE-MIGRATE-TO-EDGE batch 1 (5/26).

# 原测试搬哪了

`edge/tool-bridge/tests/recmode/test_cdp_listener.py`

跑法:
  cd edge/tool-bridge && PYTHONPATH=src python -m pytest tests/recmode/test_cdp_listener.py -q

# 为什么

cdp_listener.py 5/26 从 central 搬到 edge/tool-bridge. 测试跟模块走.
留这个 stub 防中央 cdp_listener.py 复活 (回归).
"""
from __future__ import annotations

import pytest


def test_cdp_listener_module_is_stubbed_in_central():
    """5/26 BL-RECMODE-MIGRATE-TO-EDGE batch 1 兑现校验: 中央 cdp_listener
    必须是 fail-loud stub, 任何 attr 抛 RuntimeError.
    """
    from catfish_gateway.recmode import cdp_listener

    with pytest.raises(RuntimeError, match="BL-RECMODE-MIGRATE-TO-EDGE"):
        cdp_listener.start_recording
    with pytest.raises(RuntimeError, match="BL-RECMODE-MIGRATE-TO-EDGE"):
        cdp_listener.stop_recording
    with pytest.raises(RuntimeError, match="BL-RECMODE-MIGRATE-TO-EDGE"):
        cdp_listener.list_active
