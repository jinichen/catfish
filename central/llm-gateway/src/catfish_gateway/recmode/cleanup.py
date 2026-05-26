"""STUB — BL-RECMODE-MIGRATE-TO-EDGE batch 1 (5/26).

# 原 module 搬哪了

`edge/tool-bridge/src/catfish_tool_bridge/recmode/cleanup.py`

# 为什么搬

跟 cdp_listener 同批 (E.2). 中央代码不再读写 `~/.catfish/recordings/`.  # noqa: BOUNDARY (docstring 描述 edge 行为)
gateway `/api/learn/cleanup` thin proxy. RecordingsCard (#75) 已经直接 fs
读, 不通过 gateway, 这次搬不影响它.

# 关联

- `edge/tool-bridge/src/catfish_tool_bridge/recmode/cleanup.py` — 真实现
- `edge/tool-bridge/src/catfish_tool_bridge/server.py::_handle_recmode_cleanup`
  + `_handle_recmode_list_with_meta` (新加, 给 catfish-web admin 未来用)
- `central/.../app.py::api_learn_cleanup` — thin proxy
- `docs/CENTRAL-EDGE-DATA-BOUNDARY.md` E.2 (此搬迁兑现)
"""
from __future__ import annotations

_MIGRATED_NOTICE = (
    "central/llm-gateway/.../recmode/cleanup.py 已 5/26 搬到 "
    "edge/tool-bridge/.../recmode/cleanup.py. "
    "gateway 端通过 tool_bridge_rpc.call('recmode/cleanup', ...) 调."
)


def __getattr__(name: str):  # noqa: D401
    raise RuntimeError(f"[BL-RECMODE-MIGRATE-TO-EDGE] {_MIGRATED_NOTICE} (attr: {name})")
