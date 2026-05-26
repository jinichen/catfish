"""STUB — DEPRECATED 5/26 (gateway 不读员工本机 output/).

# 原测试搬哪了

无搬迁. 替代方案是 Companion 端在 timeout toast 自显本地 outputs (BL-X).
Companion 已有 reveal_in_finder Tauri 命令.

# 为什么砍

老 list_recent 扫 ~/.catfish/output/ 列最近 24h 文件 给 timeout 友好错误段     # noqa: BOUNDARY
inject "📁 过去 24h 鲶鱼已写文件" 列表. gateway 中央代码读员工 fs, SaaS 化即破.
5/26 audit 抓.

# 防回归: 这个 stub 测试验中央 recent_outputs 真是 stub 没复活.
"""
from __future__ import annotations

import pytest


def test_recent_outputs_module_is_stubbed_in_central():
    """5/26 兑现校验: 中央 recent_outputs 必须是 fail-loud stub."""
    from catfish_gateway import recent_outputs

    with pytest.raises(RuntimeError, match="DEPRECATED 5/26"):
        recent_outputs.list_recent  # 触发 __getattr__ stub

    with pytest.raises(RuntimeError, match="DEPRECATED 5/26"):
        recent_outputs.format_recent_block  # 任何 attr 都该抛
