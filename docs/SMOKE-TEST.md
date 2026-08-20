# Smoke Test · server side E2E (Phase 1)

> 6/6 鸿波 marathon Phase 1 — bash + curl 验证 P15/P27 链路 server side 不退化.
> **不验 UI** (UI 走 `docs/TEST-PLAN-20260606.md` manual). 30s 内跑完.

---

## 跑法

```bash
cd ~/person_task/catfish
./scripts/smoke-test.sh
```

退码:
- `0` 全过
- `1` 进程/端口没起
- `2` chat completions fail
- `3` approval flow fail
- `4` P15.2 endpoint fail
- `5` plugin verify log 缺

---

## 验证什么

### Section 1 — 进程 + 端口
- hermes daemon listen 8642
- catfish-gateway listen 8999
- tool-bridge Unix socket `~/.catfish/tool-bridge.sock` 存在

### Section 2 — tool-bridge RPC
- `tools/list` 返合法 JSON-RPC + 含 N 个 tool

### Section 3 — catfish-gateway
- `GET /v1/catalog` → 200

### Section 4 — P44.7 plugin verify
- `agent.log` 含 `P15 _stream_q 闭包` (说明 verify 跑过)
- 最近 plugin install 是 `✓` 不是 `破坏` (说明 hermes 没 refactor)

### Section 4b — catfish-memory 树外存活 (8/20 补)
- `~/.hermes/plugins/catfish-memory` 在
- `~/.hermes/hermes-agent/plugins/memory/` 底下**没有** catfish 残留
- hermes 真解析得到 `catfish-memory`（位置对不代表认得出来）

  软链原来建在 hermes 树里，而大版本升级换的正是整棵 `hermes-agent/`。
  失败是静默的：provider 加载失败只打一条 warning 就返 `None`，
  记忆停摆但 agent 照常回答。8/20 已改装到树外。

  第二条要单独验，是因为两条链并存时**树内优先**——那时新链一行代码都跑不到，
  却看不出任何异常。

### Section 5 — P15 approval SSE (核心)
- POST chat completions 含 `execute_code` prompt
- SSE 流含 `event: hermes.tool.progress`
- SSE 含 `"status": "approval_pending"` (P15 _approval_notify 推送)

### Section 6 — P15.2 resolve endpoint
- POST `/v1/sessions/{sid}/approval` body `{"choice": "deny"}`
- 返 JSON 含 `resolved` / `choice` 字段

### Section 6b — 审批 endpoint 必须验 token (8/20 补)
- 同一条 URL, **不带** `Authorization` 头再 POST 一次
- 必须返 `401`

  6/6 到 8/20 这条 endpoint 一直是裸的 —— catfish 的 middleware 短路 return,
  上游那行 `_check_auth` 在 handler 里, 永远跑不到。本机任意进程不带 token
  POST 一下就能替员工点"批准"。

  单元测试守代码, 这一条守**真在跑的那个进程**: 插件没装上 / 装了旧版 /
  middleware 没进链, 单测都看不见。
  拿到 `200` = 洞还开着; 拿到 `404` = 插件没装上。

---

## 前提

需要 3 个 service 都起:

```bash
# 1. hermes daemon
hermes gateway start

# 2. catfish-gateway
cd ~/person_task/catfish/central/llm-gateway
python -m catfish_gateway.app &

# 3. tool-bridge (Companion 启动时 autostart)
open "/Applications/Catfish Companion.app"

# 等 5 秒让 tool-bridge socket 创建
sleep 5
```

需要 auth key:

```bash
# 看是不是在 ~/.hermes/.env (脚本自动读)
grep -E 'HERMES_API_KEY|API_SERVER_KEY' ~/.hermes/.env

# 或显式 set env
export HERMES_API_KEY="<your key>"
```

---

## 为什么不进 ubuntu CI

CI ubuntu runner 装不上完整 hermes (~500MB venv + 沙箱配置 + macOS 特定依赖). 装的成本大于回报。

**真正自动化场景**:
1. **本地 pre-commit hook**: `.git/hooks/pre-commit` 加 `./scripts/smoke-test.sh || exit 1`
2. **dev 机器 nightly cron**: `crontab -e` 加 `0 3 * * * cd ~/person_task/catfish && ./scripts/smoke-test.sh >> /tmp/smoke.log`
3. **手动 marathon 后跑**: 大改动 ship 后, 鸿波 push 前先跑一遍

---

## 输出例子

```
━━━ 1. 进程 + 端口 ━━━
  ✓ hermes daemon 8642 listening
  ✓ catfish-gateway 8999 listening
  ✓ tool-bridge socket 存在: /Users/chenhongbo/.catfish/tool-bridge.sock

━━━ 2. tool-bridge RPC ━━━
  ✓ tools/list returns 127 tools

━━━ 3. catfish-gateway /v1/catalog ━━━
  ✓ GET /v1/catalog → 200

━━━ 4. P44.7 plugin verify ━━━
  ✓ P44.7 verify: P15 _stream_q 闭包检查存在 log
  ✓ plugin install 最近一次 ✓

━━━ 5. P15 approval SSE flow ━━━
  ✓ SSE 流含 event: hermes.tool.progress
  ✓ SSE 含 status: approval_pending (P15 _approval_notify 推送)

━━━ 6. P15.2 resolve endpoint ━━━
  ✓ POST /v1/sessions/{sid}/approval 返 JSON 含 resolved/choice
  ✓ 不带 token POST /approval 被拒 (401)

━━━ 4b. catfish-memory 树外存活 ━━━
  ✓ catfish-memory 软链在树外 (~/.hermes/plugins/)
  ✓ hermes 树内无 catfish 残留
  ✓ hermes 解析得到 catfish-memory

━━━ 汇总 ━━━
  ✓ 全过 (12/12)
```

---

## 失败 case 处理

任一 ✗ 输出含 `↳` 提示具体怎么修. 常见:

| 失败 | 原因 | 修法 |
|---|---|---|
| `hermes daemon 8642 没起` | hermes 进程挂 / 没启动 | `hermes gateway start` |
| `catfish-gateway 8999 没起` | python 进程挂 / 没启动 | `cd central/llm-gateway && python -m catfish_gateway.app` |
| `tool-bridge socket 不存在` | Companion 没起或 spawn 失败 | 打开 Companion.app, 等 5s, 看 `~/person_task/catfish/.companion-state/tool-bridge.log` |
| `hermes refactor 破坏了` | hermes 升级了 plugin patch target | audit `~/.hermes/hermes-agent/gateway/platforms/api_server.py:1892+` 跟 plugin P15 同步 |
| `SSE 没收到 hermes.tool.progress` | P15 没 fire (Companion mode? Hermes daemon 没 reload plugin?) | `hermes gateway stop && hermes gateway start` 重 load plugin |

---

## 跟其它 test 文档关系

| Doc | 角色 |
|---|---|
| `docs/TESTING-SPEC.md` | 整体测试规范 (命名/分层/coverage 目标) |
| `docs/TEST-PLAN-20260606.md` | marathon 后针对性 manual UI 验证 (8 case) |
| `docs/SMOKE-TEST.md` (本文) | server side bash + curl, ~30s 自动跑 |
| 各 module `tests/` | unit + integration test, CI 自动跑 (12 模块全覆盖, 6/6 后) |

后续 Phase 2 (Python 级 E2E) + Phase 3 (Companion UI WebDriver) 见 TESTING-SPEC §10.
