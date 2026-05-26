"""BL-RECMODE-MIGRATE-TO-EDGE (5/25 鸿波拍板"现在必须现在转移 companion").

RecMode 录屏综合 / selector 自愈的 vision LLM 调用 + 落盘逻辑, 从
`central/llm-gateway/src/catfish_gateway/recmode/` 整体搬这里.

# 为什么搬

原 architecture: gateway 进程内调 aggregator → 读 ~/.catfish/recordings/*.png →
base64 → POST /v1/chat/completions → 解析 → 写 ~/.catfish/skills/<ns>/<name>/{SKILL.md,main.py,recmode_meta.json}.

落盘代码在 `central/` 目录里, 项目 `docs/CENTRAL-EDGE-DATA-BOUNDARY.md` E.1 标了
"极高敏感违规". 当前部署 gateway 跟 Companion 在同一台 Mac, `Path.home()` 物理
落员工本机, 暂时 OK; 一旦 SaaS 化, 这段代码会真往中央 disk 写, 违反"录屏数据
100% 本机"承诺.

5/25 ship: 把模块搬到 edge/tool-bridge, 跑在 tool-bridge 进程里 (edge 进程
canonical). gateway 端 `/api/learn/analyze` + `/api/learn/repair_selector` 改成
thin proxy, 通过 Unix socket JSON-RPC 把请求转给 tool-bridge. 中央代码 (central/)
不再读 / 写 ~/.catfish/recordings/ ~/.catfish/skills/.

# 模块

- aggregator.py: 读 events / transcripts / screenshots → vision LLM → 落 SKILL.md
- selector_repair.py: skill runtime 找不到 selector 时, vision LLM 看截图找新 selector

# 暴露给外面 (3 个入口都基于这俩模块)

1. tool-bridge native tool `recmode_aggregate` / `recmode_repair_selector` (MCP / Unix socket)
2. gateway `/api/learn/analyze` / `/repair_selector` 通过 Unix socket 转过来 (兼容老 Companion caller)
3. 直接 import 用 (单测 / hermes skill 内调)

# 关联

- `docs/CENTRAL-EDGE-DATA-BOUNDARY.md` E.1 (此搬迁兑现)
- `docs/EMPLOYEE-PRIVACY-VERIFICATION.md` (PrivacyCard 卖点真正兑现)
- `central/llm-gateway/tests/test_central_edge_boundary.py` (ALLOWLIST 移除 aggregator.py)
"""
