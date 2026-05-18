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

## 上游同步风险 (5/19+ 必须处理)

hermes CORS 修复在 `~/.hermes/hermes-agent/` (NousResearch 上游 repo) 的本地分支 `catfish-local-patches`. 风险:

1. **hermes 升级 → main 推进, 我们 detach** → 升级 hook (`apply_brand_patch.py`) 可能覆盖
2. **未 push 到任何 remote** → 员工新机装 hermes 默认没这 patch, Companion 同样 403
3. **NousResearch 同步策略未定**: PR / fork / 本地维护三选一

**短期 (本周)**: 把 `~/.hermes/hermes-agent/gateway/platforms/api_server.py` 的 diff 抽成 `edge/hermes-fork/patches/` 下的 .patch 文件, 让 setup-catfish-edge.sh 装机时自动 apply. 跟 brand patch 同 pattern.

**长期**: 考虑提 PR 上游 — CORS 是通用功能, 不太可能拒.

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

- [ ] identity-server 重启加载 30d TTL 配置 (现 1h)
- [ ] Tauri tauri.conf.json CSP 加 `http://localhost:*` (Companion 重编译, 改完可移除 yaml 的 127.0.0.1 强制)
- [ ] hermes CORS patch 抽 `.patch` 进 `edge/hermes-fork/patches/` (升级抗性)
- [ ] gateway memory 模块**真删** (`central/llm-gateway/src/catfish_gateway/memory/providers/`, 稳定 1 周后)
- [ ] 33 个 gateway → edge FS 残留 (lint warning)
- [ ] MCP stdio transport vs hermes TCP keepalive 不兼容 (hermes 内部 bug)
- [ ] 内部 qwen 服务夜间不可达 → 文档化"夜间手切外网 model" 的员工 SOP

## 给未来的你

**你最容易忘的事**:

1. **hermes CORS 修复不在 catfish repo** — 在 `~/.hermes/hermes-agent/` 的 `catfish-local-patches` 分支. hermes 升级会丢.
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

---
*作者: 鸿波 + Claude (Cowork mode)*
*相关: MEMORY-OWNERSHIP-ARCHITECTURE.md · MEMORY-ROLLBACK-PROCEDURE.md · HERMES-OPENAI-SERVER-RESEARCH.md · COMPANION-HERMES-AUTH-DESIGN.md*
