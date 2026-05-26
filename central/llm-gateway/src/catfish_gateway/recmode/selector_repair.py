"""STUB — BL-RECMODE-MIGRATE-TO-EDGE (5/25, 鸿波拍板"现在必须现在转移 companion").

# 原 module 搬到哪了

`edge/tool-bridge/src/catfish_tool_bridge/recmode/selector_repair.py`

# 为什么搬

跟 aggregator 同批次搬 — 见 ./aggregator.py 顶部 docstring.
selector_repair 调 aggregator.call_llm + 处理 vision LLM 响应, 跟 aggregator
一起搬到 edge/tool-bridge 才一致.

gateway `/api/learn/repair_selector` 现在是 thin proxy, 通过 Unix socket JSON-RPC
转 tool-bridge.

# 这个 stub 留作

- fail-loud: 任何残留 `from .recmode import selector_repair` 立刻抛
- 给 git blame / 历史读者明确指引

# 关联

- `edge/tool-bridge/src/catfish_tool_bridge/recmode/selector_repair.py` — 真实现
- `edge/tool-bridge/src/catfish_tool_bridge/server.py::_handle_recmode_repair_selector`
- `central/llm-gateway/src/catfish_gateway/app.py::api_learn_repair_selector` — thin proxy
- `docs/CENTRAL-EDGE-DATA-BOUNDARY.md` E.1 (此搬迁兑现)
"""
from __future__ import annotations

_MIGRATED_NOTICE = (
    "central/llm-gateway/.../recmode/selector_repair.py 已 5/25 搬到 "
    "edge/tool-bridge/.../recmode/selector_repair.py. "
    "gateway 端通过 tool_bridge_rpc.call('recmode/repair_selector', ...) 调."
)


def __getattr__(name: str):  # noqa: D401
    raise RuntimeError(f"[BL-RECMODE-MIGRATE-TO-EDGE] {_MIGRATED_NOTICE} (attr: {name})")
