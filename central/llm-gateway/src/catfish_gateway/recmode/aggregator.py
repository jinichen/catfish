"""STUB — BL-RECMODE-MIGRATE-TO-EDGE (5/25, 鸿波拍板"现在必须现在转移 companion").

# 原 module 搬到哪了

`edge/tool-bridge/src/catfish_tool_bridge/recmode/aggregator.py`

# 为什么搬

中央代码 (`central/`) 不再读 / 写 `~/.catfish/recordings/` `~/.catfish/skills/`.  # noqa: BOUNDARY
原 aggregator 在 gateway 进程内调 vision LLM + 落盘 3 个文件 (SKILL.md / main.py /
recmode_meta.json), 把"录屏分析结果"写到 disk — 当前部署 gateway 跟 Companion
共在员工 Mac, Path home 落员工本机暂时 OK, 但 SaaS 化后会真往中央 disk 写,
违反"录屏数据 100% 本机"的对外承诺.

5/25 把整个 aggregator 模块搬到 edge/tool-bridge 进程里跑. gateway 的
`/api/learn/analyze` 现在是 thin proxy, 通过 Unix socket JSON-RPC 转 tool-bridge.

# 这个 stub 留作

- fail-loud: 任何残留 `from .recmode import aggregator` 立刻抛, 不会偷偷复活
- 给 git blame / 历史读者一个明确指引

# 关联

- `central/llm-gateway/src/catfish_gateway/tool_bridge_rpc.py` — gateway → tool-bridge JSON-RPC client
- `central/llm-gateway/src/catfish_gateway/app.py::api_learn_analyze` — thin proxy 入口
- `edge/tool-bridge/src/catfish_tool_bridge/recmode/aggregator.py` — 真实现位置
- `edge/tool-bridge/src/catfish_tool_bridge/server.py::_handle_recmode_analyze` — tool-bridge 侧入口
- `docs/CENTRAL-EDGE-DATA-BOUNDARY.md` E.1 (此搬迁兑现)
- `docs/EMPLOYEE-PRIVACY-VERIFICATION.md` (录屏 100% 本机承诺真正兑现)
"""
from __future__ import annotations

_MIGRATED_NOTICE = (
    "central/llm-gateway/.../recmode/aggregator.py 已 5/25 搬到 "
    "edge/tool-bridge/.../recmode/aggregator.py. "
    "gateway 端通过 tool_bridge_rpc.call('recmode/analyze', ...) 调. "
    "你看到这条错误说明有代码还在直接 import 老路径 — 改成走 "
    "/api/learn/analyze HTTP endpoint 或 tool-bridge JSON-RPC."
)


def __getattr__(name: str):  # noqa: D401
    raise RuntimeError(f"[BL-RECMODE-MIGRATE-TO-EDGE] {_MIGRATED_NOTICE} (attr: {name})")
