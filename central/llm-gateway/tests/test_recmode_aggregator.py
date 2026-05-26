"""STUB — BL-RECMODE-MIGRATE-TO-EDGE (5/25).

# 原测试搬哪了

`edge/tool-bridge/tests/recmode/test_aggregator.py`

跑法:
  cd edge/tool-bridge && PYTHONPATH=src python -m pytest tests/recmode/test_aggregator.py -q

# 为什么搬

aggregator.py 5/25 整体从 `central/llm-gateway/.../recmode/` 搬到
`edge/tool-bridge/.../recmode/`. 测试跟模块走一起.

中央这边留这个 stub 文件作:
1. 历史 reference (git blame 看为啥某天突然空了)
2. 防回归: 验中央 aggregator 真是 stub 没复活
"""
from __future__ import annotations

import pytest


def test_aggregator_module_is_stubbed_in_central():
    """5/25 BL-RECMODE-MIGRATE-TO-EDGE 兑现校验: 中央这个 aggregator 必须是
    fail-loud stub, 不能再有 real impl (否则就是搬迁倒退).
    """
    from catfish_gateway.recmode import aggregator

    with pytest.raises(RuntimeError, match="BL-RECMODE-MIGRATE-TO-EDGE"):
        aggregator.aggregate_session  # 触发 __getattr__ stub

    with pytest.raises(RuntimeError, match="BL-RECMODE-MIGRATE-TO-EDGE"):
        aggregator.call_llm  # 任何 attr 都该抛


def test_selector_repair_module_is_stubbed_in_central():
    """同上, selector_repair 也是 stub."""
    from catfish_gateway.recmode import selector_repair

    with pytest.raises(RuntimeError, match="BL-RECMODE-MIGRATE-TO-EDGE"):
        selector_repair.repair_selector
