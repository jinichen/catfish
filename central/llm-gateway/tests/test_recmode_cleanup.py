"""STUB — BL-RECMODE-MIGRATE-TO-EDGE batch 1 (5/26).

# 原测试搬哪了

`edge/tool-bridge/tests/recmode/test_cleanup.py`

# 为什么

cleanup.py 5/26 从 central 搬到 edge/tool-bridge. 留这个 stub 防中央回归.
"""
from __future__ import annotations

import pytest


def test_cleanup_module_is_stubbed_in_central():
    """中央 cleanup 必须是 fail-loud stub."""
    from catfish_gateway.recmode import cleanup

    with pytest.raises(RuntimeError, match="BL-RECMODE-MIGRATE-TO-EDGE"):
        cleanup.cleanup_old_recordings
    with pytest.raises(RuntimeError, match="BL-RECMODE-MIGRATE-TO-EDGE"):
        cleanup.list_recordings_with_meta
