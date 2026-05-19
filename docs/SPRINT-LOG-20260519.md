# Sprint Log · 2026-05-19 凌晨 · Hermes Cutover Complete

> **耗时**: 28+ 小时 (5/18 凌晨 → 5/19 早上 06:00)
> **范围**: BL-MEMORY-OWNERSHIP-FIX (Phase 1+2) + BL-TOOL-BRIDGE-HERMES-INTEGRATION + 5 个修复
> **状态**: ✅ 实盘验证. Companion → hermes 8642 → gateway 8999 全链路通.

## TL;DR — 这次切了啥

之前: Companion 直打 gateway 8999, gateway 当 agent runtime (注 memory + tools)
现在: Companion 打 hermes 8642, hermes 当 agent runtime (注 memory + 调 catfish-tool-bridge MCP), gateway 退化为纯 LLM 代理.

架构细节看 `MEMORY-OWNERSHIP-ARCHITECTURE.md`. 这里只记**实盘踩坑 + 你以后会忘的关键状态**.

## 5 个坑 (按踩坑顺序)

### 坑 1. catfish-memory plugin "no provider instance found"

**症状**: `hermes memory status` 显示 plugin 加载但 provider 没实例化, memory 不注.

**真因**: hermes plugin loader **要 `register(ctx)` 函数**, 不是只 export MemoryProvider 类. 跟 `~/.hermes/hermes-agent/plugins/memory/holographic/__init__.py` 同 pattern.

**修复**: `edge/hermes-plugins/catfish-memory/__init__.py` 加 `register(ctx)`:
```python
def register(ctx) -> None:
    from .catfish_memory import CatfishMemoryProvider
    provider = CatfishMemoryProvider()
    ctx.register_memory_provider(provider)
```

### 坑 2. hermes 看不到 catfish_* tools — agent loop 死循环

**症状**: 切 hermes 后 chat 30s 超时, hermes log 报 `Unknown tool 'catfish_email_search' — sending error to model for self-correction`. self-correction 死循环.

**真因**: catfish-tool-bridge 是 unix socket JSON-RPC (给 Companion 用), 不是 MCP. hermes 通过 MCP 协议看 tool, 看不到 50+ catfish_* tool.

**修复**: 新建 `edge/tool-bridge/src/catfish_tool_bridge/mcp_server.py` — stdio MCP server 包 adapter. `hermes mcp add catfish-tools` 注册. 详见该文件 docstring.

### 坑 3. Companion 编译了, yaml 配了, 还走老 gateway

**症状**: 日志里 user chat 全打 8999 gateway, 不打 hermes 8642. 让人怀疑代码没编进去.

**真因**: `~/.catfish/companion.yaml` 的 `hermes_api.enabled` 默认 **false**. setup-catfish-edge.sh 写完 yaml 后**没自动翻成 true** (灰度安全策略). chat.ts useHermes=false → fallback gateway.

**修复**: `sed -i '' 's/enabled: false/enabled: true/' ~/.catfish/companion.yaml`

**记住**: 任何"代码改了但行为没变"先查灰度开关.

### 坑 4. Companion 报 "Load failed" — CSP 拦 localhost

**症状**: useHermes=true, fetch `http://localhost:8642/...` 报 `Load failed`. curl 直测能通.

**真因**: Tauri 2 webview 的 CSP `connect-src` 只放了 `http://127.0.0.1:*`, 没放 `http://localhost:*`. CSP 认 host 名不认 IP, 同地址不同名照拦.

**修复**: yaml 把 `url: http://localhost:8642` 改成 `http://127.0.0.1:8642` (一字之差).

**长远修法**: `src-tauri/tauri.conf.json` 加 `http://localhost:*` 到 connect-src, 重编译. 留 5/19+ 做.

### 坑 5. CORS preflight 403 — hermes 不放 Tauri origin

**症状**: 改完 yaml 用 127.0.0.1, Companion 报 `Preflight response is not successful. Status code: 403`.

**真因**: hermes API server (aiohttp 写) 走严格 origin 白名单, `_origin_allowed()` 不认 `tauri://localhost`. curl 测没 Origin 头, 走 `if not origin: return True` 分支所以通. 浏览器带 Origin 就 403.

**修复**: `~/.hermes/hermes-agent/gateway/platforms/api_server.py` 加 `_TAURI_ORIGINS = ("tauri://localhost", "http://tauri.localhost")` 常量 + `_is_tauri_origin()`, 在 `_origin_allowed` 和 `_cors_headers_for_origin` 短路放行.

**储存位置警告**: 这改动**不在 catfish repo**, 在 `~/.hermes/hermes-agent/` (NousResearch 上游 repo). 已 commit 到本地 `catfish-local-patches` 分支 (commit `58239e567`). 上游 sync 会丢, 见后面"上游同步风险".

## 关键状态 (5/19 早上)

### 当前部署

```
Companion (Tauri 2 macOS, v0.14.0)
  └→ http://127.0.0.1:8642/v1/chat/completions  (hermes API server)
       ├ X-Companion-Origin: tauri://localhost  (CORS 白名单认)
       ├ Authorization: Bearer <API_SERVER_KEY>  (~/.hermes/.env, 30d service token 待恢复)
       └ memory inject: catfish-memory plugin (37999 tokens 实盘验)
            └→ aggregates 5 sources: session_meta, employee_journal, skills_catalog, feedback, skill_guard

hermes API server (port 8642, aiohttp)
  ├ MCP server: catfish-tool-bridge stdio (121 tools 注入)
  └→ http://127.0.0.1:8999/v1/chat/completions  (catfish-gateway, LLM 代理)
       ├ quota + RBAC + tool sanitize 仍生效 (透传 user identity)
       ├ memory_registry 全 deprecated (bootstrap.py 跳过所有 provider 注册)
       └→ upstream: deepseek / gemini / qwen / nvidia / 私有 qwen
```

### 文件/配置清单

| 项 | 位置 | 备注 |
|---|---|---|
| Companion Tauri 配置 | `~/.catfish/companion.yaml` | `hermes_api.enabled` + `url` + `key` |
| hermes service token | `~/.hermes/.env` 的 `API_SERVER_KEY` | 现在 1h TTL, identity-server 重启后 30d |
| hermes config | `~/.hermes/config.yaml` | `memory.provider: catfish-memory` + `mcp_servers.catfish-tools` |
| catfish-memory plugin | `~/.hermes/plugins/catfish-memory/` (symlink → edge/hermes-plugins/catfish-memory) | |
| catfish-tool-bridge MCP | `edge/tool-bridge/src/catfish_tool_bridge/mcp_server.py` | stdio transport |
| gateway memory deprecation | `central/llm-gateway/src/catfish_gateway/memory/bootstrap.py` | 全 noop |
| hermes CORS 修复 | `~/.hermes/hermes-agent/gateway/platforms/api_server.py` | **不在 catfish repo** |

### 进程清单 (员工本机)

```bash
# 1. catfish-gateway (LLM 代理)
ps aux | grep 'catfish_gateway.app' | grep -v grep    # python -m, port 8999
# 2. hermes API server
lsof -i :8642                                          # python, hermes gateway
# 3. catfish-identity-server (OIDC)
lsof -i :8998
# 4. Companion .app
ps aux | grep 'Catfish Companion.app'
```

启动顺序: identity (8998) → gateway (8999) → hermes (8642) → Companion. 任何一个停, 上游都受影响.

## 上游同步 — 已自动化 (5/19 凌晨 BL-HERMES-PATCH-AUTOMATION 解决)

hermes 升级后**不需要手动重打 CORS patch**. 已扩展 `apply_brand_patch.py` 加 `apply_patches()` 函数 + `patches/` 目录机制:

```
edge/hermes-fork/
  ├── apply_brand_patch.py        # 既有, 扩展 apply_patches()
  └── patches/                    # 新增 (commit ca3cee3)
      ├── README.md
      └── 0001-api-server-cors-tauri-origin.patch
```

跟现有品牌 RULES (字符串替换) 并存:
- **RULES**: 单行字符串替换 — Hermes / Nous Research → 鲶鱼
- **patches/**: 多行代码块 — CORS 修复 + 未来其它代码 patch

git hooks (post-merge / post-checkout / post-rewrite) 已在 `--install-hooks` 装好, hermes `git pull` 后自动跑 `--apply`, 我们的 patch 自动重打. 幂等 (已 apply 跳过) + dry-run 抗破坏.

加新 patch 流程看 `edge/hermes-fork/patches/README.md`. 命名规范 `NNNN-描述.patch`, sort 顺序 apply.

**长期 (本月内)**: 考虑给 NousResearch 提 CORS PR — 通用功能 + 不动既有逻辑, 收的可能性高. 收了就能删 patches/0001-*.

## 验证 cutover 健康 (任何时候怀疑)

```bash
# 1. 端口在听
lsof -i :8642 && lsof -i :8999 && lsof -i :8998

# 2. CORS preflight 通
curl -i -X OPTIONS http://127.0.0.1:8642/v1/chat/completions \
  -H "Origin: tauri://localhost" \
  -H "Access-Control-Request-Method: POST" \
  -H "Access-Control-Request-Headers: authorization,content-type" 2>&1 | head -10
# 预期: HTTP/1.1 200 + Access-Control-Allow-Origin: tauri://localhost

# 3. hermes chat 真返
KEY=$(grep '^API_SERVER_KEY=' ~/.hermes/.env | cut -d= -f2)
curl -sS -m 30 http://127.0.0.1:8642/v1/chat/completions \
  -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"hermes-agent","messages":[{"role":"user","content":"早"}],"stream":false}'
# 预期: 200 + prompt_tokens > 30000 (memory 注成功)

# 4. Companion 灰度开关
grep -A3 hermes_api ~/.catfish/companion.yaml
# 预期: enabled: true + url: http://127.0.0.1:8642
```

任何一步失败, 看 `MEMORY-ROLLBACK-PROCEDURE.md` 紧急回滚.

## 剩余 backlog (5/19+ 做)

- [x] ~~identity-server 重启加载 30d TTL 配置~~ ✓ 5/19 06:31 mint 验过 expires_in=2592000s
- [ ] Tauri tauri.conf.json CSP 加 `http://localhost:*` (Companion 重编译, 改完可移除 yaml 的 127.0.0.1 强制)
- [x] ~~hermes CORS patch 抽 `.patch` 进 `edge/hermes-fork/patches/`~~ ✓ commit ca3cee3 BL-HERMES-PATCH-AUTOMATION
- [ ] 给 NousResearch 提 CORS PR (能 merge 就删本地 patch) — nice-to-have, 不阻塞
- [ ] gateway memory 模块**真删** (`central/llm-gateway/src/catfish_gateway/memory/providers/`, 稳定 1 周后)
- [ ] **BL-COMPANION-UX-POST-CUTOVER-AUDIT** — hermes cutover 后 Companion 按钮重新审视 (5/19 当天做, 见下面 §)
- [ ] 33 个 gateway → edge FS 残留 (lint warning)
- [ ] MCP stdio transport vs hermes TCP keepalive 不兼容 (hermes 内部 bug)
- [ ] 内部 qwen 服务夜间不可达 → 文档化"夜间手切外网 model" 的员工 SOP

## 给未来的你

**你最容易忘的事**:

1. **hermes CORS 修复在两个地方**:
   - 真改动位置: `~/.hermes/hermes-agent/gateway/platforms/api_server.py` (catfish-local-patches 分支)
   - **patch 源**: `~/person_task/catfish/edge/hermes-fork/patches/0001-api-server-cors-tauri-origin.patch`
   - hermes 升级时 git hook 自动 re-apply, **不需要手动**. 但 patch 源是真理来源, 要改 CORS 行为先改 .patch 文件再 --apply.
2. **yaml url 必须用 127.0.0.1 不能 localhost** — Tauri CSP 不放 localhost, 改完 CSP 才能用 hostname.
3. **`enabled: false` 是灰度安全默认** — setup 脚本写完不会自动翻 true, 员工装机后**必须手动翻**.
4. **gateway 还会收到流量, 那是 hermes 转发的, 不是 Companion 直打** — 看 user identity 是不是 chenhongbo@ffcs.cn 透传过来的就知道.
5. **session_summarizer + proactive_starter 撞内部 qwen 502 是正常** — 内部模型夜间不在, `auto_fallback=False` 是设计内, 不要"修".

## 时间线 (粗)

- 18:00 5/18 — sprint 启动, 先做 email + auth 相关 16 个 task
- 22:00 — 用户喊"memory 都交给 hermes, gateway 为啥还插手" — 触发架构 redesign
- 23:00 → 02:00 5/19 — Phase 1 (POC + safety net) + Phase 2 (Companion 切 + gateway 删 memory)
- 02:00 → 04:00 — 撞 5 个坑, 逐个修
- 04:00 → 05:30 — 真切 + CORS 修复 + 实盘验证
- 06:00 — 本文档写完

## § BL-COMPANION-UX-POST-CUTOVER-AUDIT (5/19 当天做)

> **触发**: 切到 hermes 后, Companion 输入栏的 3 个按钮 (学习 / 接续再思考 / 停在发) 原本是 Companion + gateway 时代的产物, 行为可能过时.

### Phase A — Audit (1 小时, 不动代码)

每个按钮三问: (1) 现在 wire 到哪 (2) 切 hermes 后是否仍生效 (3) 还有用没

#### A1. 🎓 学习 / Teaching Mode

- **现在 wire**: `chat.ts:451` `useTeachingStore.getState().on` → 发 `X-Catfish-Teaching-Mode: 1` header
- **gateway 时代行为**: gateway 收 header → 关 9 个 inject (memory / employee_journal / 等) + 关 feedback retry, 让 LLM "纯净"接收输入
- **hermes 时代检查**:
  - hermes API server 收到这 header, 不认识, 透传给 gateway → gateway 仍关 inject 但**inject 现在在 hermes 这层做的**, 关 gateway inject 没意义
  - hermes 是否有 "skip memory inject" 等价开关? (查 hermes 文档 / 配置)
  - 如果没, "教学模式" 这个产品概念在 hermes 架构下需要重新定义 — 或者**改成传给 hermes 的 system prompt override**
- **决策**:
  - 选 A. wire 到 hermes (找 hermes 等价 toggle / 改 hermes 加这功能)
  - 选 B. 砍掉这按钮 (员工实际用吗? 看 metrics 这按钮点击率)
  - 选 C. 改语义为"加深度推理 prompt" (变成 prompt 工程)

#### A2. 🔄 接续 / 再思考

- **现在 wire**: 找 chat.ts / useChat.ts 里的逻辑 — 大概是重新 POST 一次带 "请再深入想想" 的 instruction
- **hermes 时代检查**:
  - hermes 有 `auto_continue` (自动, 重试错误响应), 跟用户主动 "我不满意" **语义不同** — 这按钮**还有价值**
  - 但实现机制可能要调: 之前用户点接续, Companion 复用同 session 重发. hermes 走 agent loop, "再思考"应该让 hermes 自己 plan-act 一轮, 不是重 POST 整个对话
- **决策**: 大概率保留, 但**改实现** — 让 hermes agent loop 跑一轮 "请重新评估上一次回答" 的 sub-prompt

#### A3. 🎙 停在发 / 按住说话

- 跟 hermes 切换**无关**, 纯 UX/语音录入
- **不动**

### Phase B — 扩展思考: 暴露 hermes 原生特性

切完后, 理论上能给员工开放 hermes 的 4 类原生能力, 看哪些值得做:

| hermes 能力 | 当前状态 | 该做不该做 |
|---|---|---|
| hermes 自带 memory plugins (holographic / hindsight / honcho) | hermes one-external-provider-limit 只许一个外部 provider, **catfish-memory 占住了这个 slot**, 所以 hermes 自带几个 plugin 没法并行跑 | 短期不开 (catfish-memory 是主力), 长期看要不要把它们的能力 (e.g. holographic 的语义压缩) 移植进 catfish-memory |
| hermes 自带 context_engine plugins | 跟 memory plugin 不冲突, 但当前 catfish 没开 | 评估每个 plugin 的能力, 看哪些值得开 |
| skill 系统 | hermes 有自己的 skills/, catfish skill 是双轨 | **该合并** — 但是大工程, 不在本 sprint |
| multi-agent A2A 协议 | hermes 有, catfish 也有 (各自实现, 不互通) | 长期对齐, 不急 |
| 命令模式 (`/cmd`) | hermes CLI 有, Companion 没暴露 | Phase B 后期看是否值得 |

### Phase C — 改 + 验 (2-3 小时)

按 Phase A 决策, 改 chat.ts / 后端 / 加测试. 改完手测 3 按钮 + 跑一轮已有 chat 看不破回归.

### 时间预算

- 7:00 - 9:00 通勤 + 早饭 (休息一下, 别死磕)
- 9:00 - 10:00 Phase A audit
- 10:00 - 12:00 Phase B 扩展思考 + 决策
- 13:00 - 16:00 Phase C 实现 + 验证
- 16:00 commit + 收尾

**前置依赖**: 你**先休息 2 小时** (现在 7:00, 9:00 前别动代码). 28h sprint 后大脑 reasoning 能力降级, 这事是产品设计决策, 不是闭眼能写的代码, 醒着大脑做才不会做错决策.

---
*作者: 鸿波 + Claude (Cowork mode)*
*相关: MEMORY-OWNERSHIP-ARCHITECTURE.md · MEMORY-ROLLBACK-PROCEDURE.md · HERMES-OPENAI-SERVER-RESEARCH.md · COMPANION-HERMES-AUTH-DESIGN.md*
