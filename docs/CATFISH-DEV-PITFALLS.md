# catfish · 工程踩坑录 (Dev Pitfalls)

> **写于 2026-06-02 晚, 鸿波在我 10 小时连发 round 复 round 失误后说"是不是该把所有碰到的坑记下来"**.
>
> 这是 **catfish 工程师 (含未来 Claude session) 真该读的反踩坑 anchor**. 跟
> `CATFISH-CORE-IDENTITY.md` (产品 / 商业定位) 同 tier — **任何新 session 上手
> catfish 工程前必读**.
>
> 跟 `docs/operations/troubleshooting.md` 不同: 那是给员工 IT 看的部署运维排查,
> 本文是给**改 catfish 代码的人**看的真坑.
>
> 维护人: 鸿波
> 状态: 锚定 (要加新条目直接 append, 但已有条目改前先 review session)

---

## 真用法

```
session start → 先读 CATFISH-CORE-IDENTITY.md (定位锚)
             → 再读本文 (避免重复踩坑)
             → 再开始真改代码
```

新踩坑发现时**当场 append 本文**, 不要等"周一再整理" (周一会忘).

---

## 反 pattern 工程纪律 (反盲猜 7 条)

我 6/2 真犯过的, 每一条都让真生产挂过 / 多 round 浪费时间:

| # | 反 pattern | 真做法 |
|---|---|---|
| 1 | **没 grep 就改代码** | **每次改前** grep 关键 symbol / 路径, 看真有几处, 真依赖谁 |
| 2 | **单测 pass = 真生产 OK** | 单测 mock 不真打 server. **跨 provider 改动必须 server 端 e2e 真测试**, 不只 mock |
| 3 | **用 default 假设 ("vite 默认 5173", "deepseek 应该 silently 忽略")** | **真 cat 配置文件** / **真 grep provider 源码**. 假设是错的根源 |
| 4 | **看见单点错就改单点** | **trace 完整流程** (e.g. cors_middleware 两步 / install patch 链 9 个 patch), 一个 middleware 真完整看完再 patch |
| 5 | **后台 patch / lazy install 自以为聪明** | **同步比异步稳**, 异步只用在真必要 (循环 import / async 启动顺序). 异步隐藏问题, 同步立即 fail loud |
| 6 | **"我之前看过这文件"** | **再 Read 一次**, 文件可能变了, mental model 真不可信. cost 0 |
| 7 | **看见 error msg 就盲改修** | **先 trace 真错来源** — log 真路径 / 进程 / 异常链. 90% 真因不是 error msg 字面意思 |

---

## 今天 6/2 真踩 8 坑 (按真踩到的顺序)

### Pit 1: `~/.hermes/.catfish_audit.jsonl` 不是 gateway audit

| 项 | 真值 |
|---|---|
| **现象** | 我让鸿波 `jq` 查 `cache_read_tokens` 总返空, 以为 cache 没生效 |
| **真因** | `~/.hermes/.catfish_audit.jsonl` 是 **tool-bridge edge audit** (tool 调用 args_preview), gateway LLM audit 真在 **`~/.catfish/gateway_audit.jsonl`** (`metrics.py:27` 真定义) |
| **真做法** | grep `metrics.py` 真路径配置, 不要凭"应该是 .hermes 下"假设 |
| **反 pattern** | #3 用 default 假设 |

### Pit 2: LiteLLM `cached_tokens` 统一字段, 不是 Anthropic 顶层

| 项 | 真值 |
|---|---|
| **现象** | 5/17 BL-CACHE-AUDIT 抓 `cache_read_input_tokens` (Anthropic 顶层), 但 deepseek/dashscope/gemini cache hit 后字段 0 |
| **真因** | LiteLLM 1.86 把所有 OpenAI 协议系 provider (DeepSeek / DashScope / Gemini / OpenAI) cache 字段统一映射到 **`usage.prompt_tokens_details.cached_tokens`** (OpenAI 标准, `llms/dashscope/cost_calculator.py:28-30` 真证), Anthropic 顶层字段只 Claude 真用 |
| **真做法** | 接 LiteLLM 字段时**真 audit `llms/<provider>/chat/transformation.py`** 看真转换, 不要凭"协议应该相似"假设 |
| **反 pattern** | #3 用 default 假设 |

### Pit 3: `cache_control` 真破 deepseek/openai schema

| 项 | 真值 |
|---|---|
| **现象** | 下午 ship cache_control marker, 单测 pass, 但真生产所有 model 撞 **400 BadRequest** (deepseek, gemini, qwen 都挂) |
| **真因** | LiteLLM 1.86 把 content list-of-blocks + cache_control 发给非 Anthropic provider 时, server 端**真不接** (尽管 LiteLLM 自己不抛). 我之前断言"silently 忽略" 是**没真生产验证**的假设 |
| **真做法** | 跨 provider 跨字段改动**必须真 e2e 测试** — 不只 unit test mock. 写完先开 env toggle 默认关 (跟 `BL-FALLBACK-TOGGLE` 5/16 同 pattern), 验证过几个 provider 再 default 开 |
| **反 pattern** | #2 单测 = 生产 OK |

### Pit 4: hermes plugin `install()` 后台等 model_tools, gateway 进程永不 import

| 项 | 真值 |
|---|---|
| **现象** | catfish-xcatfish-user plugin 真生产**所有 P1-P11 patch 从来没跑过** — 鸿波生产 16 天没问题是巧合 (那时 Companion 直连 gateway 8999 绕过 hermes proxy) |
| **真因** | `__init__.py:151 _delayed_install()` 等 `model_tools` fully init. 但 hermes gateway 进程 (跑 api_server 8642 + 微信/飞书) **不 import model_tools** (那是 chat agent 路径用的) → 30s timeout 后 install **永远跳过** |
| **真做法** | 区分 patch 类别: **不依赖 model_tools 的** (P0/P1/P3/P4/P7/P8/P9/P10) **在 register() 同步跑**; 依赖的 (P5/P6/P11 链到 _create_agent → agent_init → model_tools) 仍走 install 后台. 不要一锅烩全后台 |
| **反 pattern** | #5 异步自以为聪明 |

### Pit 5: hermes CORS preflight **两步**, 只 patch 一步等于没 patch

| 项 | 真值 |
|---|---|
| **现象** | P9 加 `X-Catfish-Prev-Model` 到 allowlist 后 curl 真 200 (合法 origin), 但 dev origin `http://localhost:5173` 仍 403 |
| **真因** | `cors_middleware` 两步走: (1) `_origin_allowed(origin)` 校验 origin (2) `_cors_headers_for_origin(origin)` 真构造 headers. P8 patch 只 wrap 第 1 步让 tauri origin 过, **第 2 步没 wrap** → 看 `self._cors_origins` (hermes config 默认空) 返 None → OPTIONS + cors_headers is None → 403 |
| **真做法** | trace 完整 middleware 流程**真把每步看完**, 不只看见第一行 if 就以为是全部 |
| **反 pattern** | #4 单点修 |

### Pit 6: vite 真端口是 **1420** 不是 5173 (Tauri 默认)

| 项 | 真值 |
|---|---|
| **现象** | 加 `http://localhost:5173` 到 P8 allowlist 后 Companion 仍 403 |
| **真因** | Tauri 模板 `vite.config.ts` 真用 **1420** (Tauri 文档推荐), 不是 vite 标准 5173. 我下午盲加 5173 是错的 |
| **真做法** | 改任何 origin / port 配置前 **真 grep / cat `vite.config.ts` + `tauri.conf.json`** 看真值 |
| **反 pattern** | #3 用 default 假设 |

### Pit 7: hermes 自带 watcher 真自动重启, `kill PID` 不够

| 项 | 真值 |
|---|---|
| **现象** | `kill -9 2253` 杀 hermes 后, PID 95710 自动出现 (鸿波 9 分钟后看 ps) |
| **真因** | hermes `launch_detached_profile_gateway_restart` (`gateway.py:572`) 真有 watcher 进程, polling 老 PID 120 秒, 一旦死了 `subprocess.Popen(['python', '-m', 'hermes_cli.main', 'gateway', 'run', '--replace'])` 重启. macOS launchd plist (`~/Library/LaunchAgents/*hermes*`) 也可能自动拉 |
| **真做法** | 杀 hermes 真用 `hermes gateway stop` (杀 watcher + gateway), 或 `pkill -f hermes_cli.main` (按 cmdline 杀). `killall hermes` 不工作 (真进程名 = "python") |
| **反 pattern** | #4 只看进程列表表面名字 |

### Pit 8: webview CORS preflight 缓存 10 分钟, Tauri app 不杀真不刷

| 项 | 真值 |
|---|---|
| **现象** | server 端 CORS 修通 (curl 真 200), Companion webview 仍 403 |
| **真因** | (1) CORS preflight 浏览器缓存 Max-Age 600s = 10 分钟, webview 用之前 cached 失败 (2) `pkill -9 vite` 杀 dev server, 但 **Tauri app 进程 `catfish-companion-app` 不杀** webview 仍活着 |
| **真做法** | 完全杀 Tauri app (`pkill -9 -f catfish-companion-app`), 不只杀 vite. 或 webview console 跑 `location.reload(true)` 强制 |
| **反 pattern** | #4 单点修 (只看 dev server 不看 Tauri app) |

---

## 红线 — 不改 hermes 源码, 只 monkey-patch

### Pit 0: catfish 永远不 fork / 不改 hermes 源码

| 项 | 真值 |
|---|---|
| **真原则** | catfish 用 `catfish-xcatfish-user` plugin **runtime monkey-patch** hermes 对象 (set class attribute / mutate module dict), **永远不改 `~/.hermes/hermes-agent/*.py` 文件** |
| **真原因** | hermes 是 upstream, fork = 每次 hermes 升级 (e.g. 6/1 0.14 → 0.15.1) 都要 merge conflict, 真负担太重. monkey-patch 让 catfish 只需要 audit 11 个 patch 是否仍兼容新 hermes, 文件本身不动 |
| **真做法** | 任何想"动 hermes 行为" 的改动 → 加进 `catfish-xcatfish-user/plugin.py` 的 `_patch_p*` 系列, 在 plugin install 时 runtime 注入. **绝不**直接 edit hermes 源文件 |
| **真后果 if 违反** | 5/29 真曾改过 hermes `api_server.py` (用 patch 工具), 留下 `.orig` + `.rej` 文件. 6/1 hermes 0.15.1 升级时, 那些直改的内容真丢了 (catfish 不知道 hermes 升级带来了什么), 留下隐藏 bug 等以后撞 |
| **真检测** | `find ~/.hermes/hermes-agent -name "*.orig" -o -name "*.rej"` — 有就说明真改过 hermes 源, **该清理 + 把改动迁到 plugin monkey-patch** |

---

## 历史真坑 (5/X 已踩, 上下文用)

### Pit 9: catfish-memory plugin 5/19 ship 但 14 天 0 active

| 项 | 真值 |
|---|---|
| **现象** | 5/24 鸿波 audit 发现"memory 70% 跑偏", 真因是 catfish-memory plugin 从 5/19 ship 起**一次都没真注册** |
| **真因** | hermes gateway mode (thin proxy 8999) **不 load MemoryProvider plugin**, 只 chat agent mode (8642) load. catfish 当时全用 gateway mode → plugin 0 触发 |
| **真做法** | 改 hermes 集成前**真验证哪些进程真 load 哪些 plugin**, 不要靠"plugin discovery 应该装载所有 plugin"假设 |
| **真后续** | 6/2 凌晨 BL-MEMORY-ROUTER-A2-V3 把 memory router 移到 catfish-xcatfish-user plugin (真生产装载) |

### Pit 10: dash 包名 → `spec_from_file_location` import 不稳

| 项 | 真值 |
|---|---|
| **现象** | catfish-memory plugin 加载时 `from .catfish_memory import ...` 失败, `register()` 没被定义 |
| **真因** | hermes plugin loader 用 `importlib.util.spec_from_file_location` 加载 `__init__.py`, spec name 不是真 Python package (因为目录名含 dash), `__package__` 没设 → relative + absolute import 都 fail → `__init__.py` exec 中断 |
| **真做法** | dash 包名的 plugin **必须三段 fallback import**: relative → absolute → `spec_from_file_location` 按文件路径. 真套路 (`catfish-xcatfish-user/__init__.py:27 _load_plugin_module()` 是范本) |
| **真现状** | 5/28 修, 6/1 又踩 (catfish-xcatfish-user 同套) — **每个 dash 包名 plugin 都要做这个** |

### Pit 11: OAuth dev token cache 死循环

| 项 | 真值 |
|---|---|
| **现象** | Companion 启动早期偶发 401, 之后 OAuth 续好但仍 401 死循环, **必须 killall Companion 才恢复** |
| **真因** | `getToken()` 启动早期 `invoke('auth_get_access_token')` 偶发返 null (catfish-identity 没起好 / OAuth 还没完成), 落 .env 兜底拿 dev-token-local 并**永久 cache**. 之后即便 OAuth 续好, 某次 invoke 返 null 就立即回到老 cache → gateway 解 dev_token → 虚构 user → 真员工 quota 永远 0 |
| **真做法** | dev token / fallback 决不 cache, 每次现拉. 5/24 BL-FIX-STALE-TOKEN-CACHE 修 |

### Pit 12: hermes 0.15.1 升级 — discover_plugins 真在 model_tools partial init 中触发

| 项 | 真值 |
|---|---|
| **现象** | 6/1 升 hermes 0.15.1 后, catfish-xcatfish-user plugin 加载撞 `from model_tools import get_tool_definitions` partial circular ImportError, 连续 16 次 fail |
| **真因** | hermes 0.15 `tools/skills_tool.py:850` 顶部触发 `discover_plugins()`, 主线程 stack 还在 `model_tools` partial init 中. plugin install 直接调 `from model_tools import ...` 撞 circular |
| **真做法** | hermes 升级时**真 trace plugin load 时机**, 看是不是在 model_tools partial init 中. 6/1 BL-PLUGIN-HERMES-015-LAZY-INSTALL 改延迟 install 兜底 (**但这又留下了 Pit 4 真坑**, 见上) |

### Pit 13: gateway 5/14 + 5/16 不同 fallback 行为

| 项 | 真值 |
|---|---|
| **现象** | 5/14 加 `BL-FALLBACK-PROMPT-CAP` 大 prompt 跳公网; 5/16 鸿波拍 `BL-FALLBACK-TOGGLE` 默认关 auto_fallback. 两者真叠加生效后 chat 经常"内网挂 + 公网也跳 + 0 fallback" → 撞 LargePromptFallbackBlocked |
| **真因** | 历史 fallback 设计真 evolve 复杂, 不同 BL 锁定不同行为, 真叠加时谁也不知道真路径 |
| **真做法** | 改 fallback 前**真过一遍**: `should_fallback` / `resolve_chain` / `auto_fallback` / `max_fallback_prompt_tokens` / `BL-TRANSIENT-NETWORK-RETRY` 这些条件全列, 再改 |

---

## 真生产 / dev 环境差异表

防"我 dev 测 OK, 生产挂" 类失误:

| 维度 | dev | 真生产 |
|---|---|---|
| Companion origin | `http://localhost:1420` (Tauri vite dev) | `tauri://localhost` / `http://tauri.localhost` (packaged app) |
| hermes 启动 | 终端跑 `python -m hermes_cli.main gateway run --replace` | launchd `~/Library/LaunchAgents/ai.hermes.gateway.plist` |
| catfish-gateway 启动 | `python -m catfish_gateway.app` (HOST=0.0.0.0) | launchd `com.catfish.gateway.plist` (HOST=127.0.0.1) |
| log 路径 | stdout + `~/Library/Logs/catfish/gateway.log` | launchd plist `StandardOutPath` 指 `gateway.log` + `gateway.error.log` |
| .env 加载 | shell 自然继承 | **launchd 不读 ~/.hermes/.env**, 必须 `launchd-wrapper.sh` 显式 source |
| Python 进程名 | `python3` 或 `python` | 都是 "python" 不是 "hermes" → `killall hermes` 不工作 |
| 上游 LLM 可达 | dev mac 有外网 | 客户机房可能内网 only |

---

## 关键路径 cheatsheet (真路径速查表)

防再次指错路径:

| 内容 | 真路径 | 谁写 |
|---|---|---|
| **gateway LLM audit** (cache/quota/model) | `~/.catfish/gateway_audit.jsonl` | `catfish_gateway/metrics.py:log_request_metadata` |
| **tool-bridge edge audit** (tool 调用) | `~/.hermes/.catfish_audit.jsonl` | `catfish_tool_bridge/audit.py` |
| **hermes agent log** (含 plugin register) | `~/.hermes/logs/agent.log` | hermes 自己 logger |
| **hermes gateway log** (微信/飞书 + api_server) | `~/.hermes/logs/gateway.log` | hermes `gateway.run` logger |
| **hermes error log** | `~/.hermes/logs/errors.log` | hermes logger ERROR level |
| **catfish-gateway log (launchd 启)** | `~/Library/Logs/catfish/gateway.log` (file logging) + launchd plist 的 stdout/stderr | `catfish_gateway/app.py` logger |
| **catfish CLI 跑 gateway log** | stdout + 同上 file logging | 同上 |
| **catfish memory audit (6/2 加)** | `~/.catfish/memory_audit.jsonl` | `memory_router._append_audit_log` |
| **catfish employee journal** | `~/.catfish/employee_journal.md` | `memory_router._route_to_journal` |
| **RecMode 录屏** | `~/.catfish/recordings/<sid>/` | `tool-bridge recmode/cdp_listener.py` |
| **RecMode 生成 skill** | `~/.catfish/skills/<ns>/<name>/` (5/21 后 default) 或 `<catfish_root>/skills/` (5/21 前 workspace) | `tool-bridge recmode/aggregator.py` 调 `save_skill` |
| **hermes memories (USER.md / MEMORY.md)** | `~/.hermes/memories/` | hermes `memory_tool.py` |

---

## 真 catfish-xcatfish-user plugin 速查 (今天最大单坑)

### Patch 列表 + 真依赖

| Patch | 真做啥 | 依赖 model_tools? | 真该哪里跑 |
|---|---|---|---|
| P0 `_patch_asyncio_executor_for_contextvars` | asyncio executor CV 跨 thread | ❌ | **同步 (register)** |
| P1 `_patch_p1_agent_init` | `agent_init.init_agent` 跑完 → apply_headers (X-Catfish-User 注入) | ❌ (agent_init 是 lazy import) | **同步 (register)** |
| P2 `_patch_p2_current_main_runtime` | main runtime normalize | ❌ | **同步 (register)** |
| P3 `_patch_p3_auxiliary_client` | auxiliary OpenAI client | ❌ | **同步 (register)** |
| P4 `_patch_p4_auto_title_session` | auto title session | ❌ | **同步 (register)** |
| P5/P6/P11 `_patch_p5_p6_p11_api_server_create_agent_and_picker` | `APIServerAdapter._create_agent` wrap + post-init headers + picker middleware | ✅ **依赖 model_tools** (chain to agent_init → model_tools) | **后台 install (等 model_tools)** |
| P7 `_patch_p7_companion_proxy_route` | `_handle_companion_proxy` catch-all + X-Catfish-User 透传 | ❌ | **同步 (register)** ← 这是 X-Catfish-User 透传真关键 |
| P8/P9 `_patch_p8_p9_cors` | Tauri origin + Allow-Headers + `_cors_headers_for_origin` | ❌ | **同步 (register)** |
| P10 `_patch_p10_apply_client_headers_localhost` | `AIAgent._apply_client_headers_for_base_url` localhost:8999 分支 | ❌ | **同步 (register)** |

### 真 register 流程 (6/2 晚 final)

```
register(ctx):
  Step 1: _verify_patch_targets() — 静态文件 grep, 不 import
  Step 2: register pre_tool_call hook
  Step 2.5: register memory tool override (BL-MEMORY-ROUTER-A2-V3)
  Step 2.7: 同步跑所有不依赖 model_tools 的 patch
            (P0/P1/P3/P4/P7/P8/P9/P10) ← 6/2 晚 round 2 真修
  Step 3: 后台线程 _delayed_install():
            等 model_tools fully init → install() 跑全套 (P5/P6/P11 真生效, 其它幂等)
```

### 真验证 plugin 真生效

```bash
# 看 agent.log 真有这两行
grep -E "同步 patch.*X-Catfish-User|P8/P9 CORS patch 同步应用" ~/.hermes/logs/agent.log | tail -2

# 期望:
#   "catfish-xcatfish-user: 同步 patch ✓ (P0/P1/P3/P4/P7/P8/P9/P10) — X-Catfish-User 透传 + CORS allowlist 真生效"

# 再 curl 验 CORS:
curl -sI -X OPTIONS http://127.0.0.1:8642/v1/chat/completions \
  -H "Origin: http://localhost:1420" \
  -H "Access-Control-Request-Method: POST" \
  -H "Access-Control-Request-Headers: X-Catfish-Prev-Model" \
  | grep -i "Allow-Headers"
# 期望含: X-Catfish-Prev-Model, X-Catfish-User, ...

# 再 chat 真测 (验 X-Catfish-User 透传):
# Companion 工作台问任一句话, 不再撞 400 "service token requires X-Catfish-User"
```

---

## 改任何 patch 前 — 反盲猜清单 (我 6/2 真该做的)

1. **真 grep 真 patch 列表**: `grep -nE "_patch_p[0-9]" plugin.py`
2. **真 trace install 流程**: 哪些在 register 同步, 哪些后台 install
3. **真 audit 依赖图**: 每个 patch import 啥, 是不是 lazy
4. **真在哪个进程 load**: gateway run vs chat agent vs api_server — 不同进程 import 不同
5. **真生产 e2e 测试**: 不只 unit test mock, 真打 server, 真 curl, 真看 console
6. **真改前 git status + git diff**: 看上次改了啥, 没 commit 的不要被覆盖

---

## 文件维护规则

- **新踩坑当场 append** — 不要等"周一再整理" (会忘)
- **改老条目前先 review session** — 这是 anchor, 不轻改
- **每条必带"真因 + 真做法 + 反 pattern"** — 不只描述现象
- **真路径 cheatsheet 改时要同时改本文 + `metrics.py` 之类的真源** (双向真理)

---

*创建: 2026-06-02 22:30, 鸿波 10 小时 round 复 round 失误后拍板*
*关联: `docs/CATFISH-CORE-IDENTITY.md` (产品定位) · `docs/operations/troubleshooting.md` (员工 IT 排查) · `docs/CATFISH-DEBT-AUDIT-2026-05-19.md` (减债 audit)*
