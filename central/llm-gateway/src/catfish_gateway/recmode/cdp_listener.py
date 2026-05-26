"""STUB — BL-RECMODE-MIGRATE-TO-EDGE batch 1 (5/26).

# 原 module 搬哪了

`edge/tool-bridge/src/catfish_tool_bridge/recmode/cdp_listener.py`

# 为什么搬

跟 5/25 搬 aggregator + selector_repair 同批次 (E.1 → E.2 闭环). 中央代码
(`central/`) 不再读 / 写 RecMode 录屏目录. gateway `/api/learn/{start_recording,
stop_recording, active, status}` 4 个 endpoint 改 thin proxy 走 Unix socket
JSON-RPC 转 tool-bridge.

# 这个 stub 留作

- fail-loud: 任何残留 `from .recmode import cdp_listener` 立刻抛
- git blame 历史指引

# 关联

- `edge/tool-bridge/src/catfish_tool_bridge/recmode/cdp_listener.py` — 真实现
- `edge/tool-bridge/src/catfish_tool_bridge/server.py::_handle_recmode_{start_recording,stop_recording,active,status}`
- `central/.../app.py::api_learn_{start_recording,stop_recording,active,status}` — thin proxy
- `central/.../tool_bridge_rpc.py` — Unix socket JSON-RPC client
- `docs/CENTRAL-EDGE-DATA-BOUNDARY.md` E.2 (此搬迁兑现)
"""
from __future__ import annotations

_MIGRATED_NOTICE = (
    "central/llm-gateway/.../recmode/cdp_listener.py 已 5/26 搬到 "
    "edge/tool-bridge/.../recmode/cdp_listener.py. "
    "gateway 端通过 tool_bridge_rpc.call('recmode/start_recording', ...) 等调."
)


def __getattr__(name: str):  # noqa: D401
    raise RuntimeError(f"[BL-RECMODE-MIGRATE-TO-EDGE] {_MIGRATED_NOTICE} (attr: {name})")
