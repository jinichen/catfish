# Hermes 升级 Playbook — 治本流程 + 历史踩坑全集

> **作者**: 鸿波 + 6/21 sprint 痛苦总结
> **场景**: 每次 hermes-agent 上游 (NousResearch/hermes-agent) 发新 release, catfish 跟版
> **核心原则**: 仔细分析代码, 不要瞎猜. 每步 ground truth verify, 决不扛着旧假设跳到结论.

---

## 一、升级前 audit checklist (按这条做, 别跳)

### Step 0: 拉 hermes 新版本 source, 不要 grep truncated 文件

```bash
HERMES_VERSION="v2026.X.Y"  # 改成真新版本
HERMES_AUDIT_DIR=$(mktemp -d)
cd "$HERMES_AUDIT_DIR"

# 拉关键文件 (用 curl 不用 web_fetch — web_fetch 长文件 truncate, 拿不全)
for f in \
  gateway/platforms/api_server.py \
  gateway/run.py \
  gateway/authz_mixin.py \
  gateway/slash_commands.py \
  gateway/session.py \
  tools/approval.py \
  tools/session_search_tool.py \
  tools/tool_search.py \
  run_agent.py \
  agent/agent_init.py \
  agent/auxiliary_client.py \
  agent/title_generator.py \
  agent/conversation_loop.py \
  agent/memory_manager.py \
  model_tools.py \
  hermes_cli/tools_config.py \
  plugins/memory/__init__.py
do
  mkdir -p "$(dirname "$f")"
  curl -sL --max-time 10 \
    "https://raw.githubusercontent.com/NousResearch/hermes-agent/${HERMES_VERSION}/${f}" \
    -o "$f"
  printf "%5d lines  %s\n" "$(wc -l < "$f" 2>/dev/null)" "$f"
done
```

### Step 1: 跑 audit script 对比 catfish 19 patch target

```bash
HERMES_ROOT="$HERMES_AUDIT_DIR" bash ~/person_task/catfish/edge/catfish-cli/scripts/audit_hermes_compat.sh
# 期望: pass=53 fail=0
# 任何 fail → 看下面 "已知坑分类" 排查
```

### Step 2: 升级时机 (实际操作)

```bash
# 装机版本 pin (员工本机)
cd ~/.hermes/hermes-agent
git fetch
git checkout "$HERMES_VERSION"

# 跑 plugin 验证脚本
HERMES_ROOT=~/.hermes/hermes-agent bash ~/person_task/catfish/edge/catfish-cli/scripts/audit_hermes_compat.sh

# 重启 hermes
hermes gateway restart
sleep 12  # 等 plugin install daemon thread 跑完

# verify plugin install ✓
grep -E "delayed install ✓|catfish-xcatfish-user" ~/.hermes/logs/gateway.log | tail -5
# 期望: 看到 "delayed install ✓ (P3.5.53 路径)" 或类似 INFO

# verify 不再 "未装载"
awk '/2026-XX-XX HH:MM:/,0' ~/.hermes/logs/gateway.error.log | grep "未装载" | head -5
# 期望: 0 命中 (按 hermes restart 时刻过滤)

# verify chat e2e
# Companion 跑一条 "测试" — 看上游 LLM 真返结果, picker 真生效
```

---

## 二、Audit 真因链 — 8 步顺序 (鸿波 6/21 sprint 真血泪)

```
1. UI picker → 真发的 model 是啥
   ↓ 验: devtools Network → POST /v1/chat/completions → Request Data
       看 body.model 真字符串

2. body 真到 hermes 8642 没
   ↓ 验: tail -f gateway.log + 发 chat, 真看到 "POST /v1/chat/completions"
   ↓ 错则: hermes 8642 LISTEN? curl /health 200? Companion CSP/CORS?

3. hermes 8642 真接到 chat, plugin middleware 真触发没
   ↓ 验: P11 stash middleware 应 set CV_PICKER_MODEL — 加 logger.warning probe 看
   ↓ 错则: middleware register fail / Application 已 frozen

4. P11 装上 ≠ install() 全套 ≠ _INSTALLED=True
   ↓ 区分: P11 通过 Application.__init__ patch (同步段 P8/P9 顺带装上) 真生效,
       但 install() 真跑全套 patch 才 set _INSTALLED=True
   ↓ 验: grep "delayed install ✓" + grep "未装载"

5. _INSTALLED=False → pre_tool_call_safety_check raise → chat tool call block
   ↓ 验: grep "pre_tool_call_safety_check raised" 数量
   ↓ 错则: 看 _delayed_install timeout / install() 真 traceback

6. install fail 真因
   ↓ 看 traceback. 常见:
     - 30s timeout, model_tools 没 ready → 死锁 (P3.5.53 修)
     - import circular (run_agent partial init) → 移到 _delayed_install (P3.5.50 修 P10)
     - middleware register too late (Application frozen) → Application.__init__ patch (老 path)

7. Companion 端 picker UI 显 X 但 send 真发 Y
   ↓ 真因 几乎都是 store reset/effect 改了 model
   ↓ 验: console 看 useChatStore.getState().model 跟 devtools Network 真 body
   ↓ 修: store reset() 别清 modelPickedByUser (P3.5.52)

8. dev mode 通 prod build fail
   ↓ 真因: tauri.conf.json CSP / capability / ATS 严格度差异
   ↓ 验: dev vs prod 对比 CSP connect-src / img-src / ATS
   ↓ 修: connect-src 加缺的 host (P3.5.50.2 加 http://localhost:*)
```

**关键纪律**: 每步 ground truth 出来重新评估假设, **不要扛着前步的假设跑**. 6/21 sprint 我连错 8 次都是因为跳到结论.

---

## 三、已知坑分类 (按 sprint 顺序)

### 坑 1: web_fetch 长文件 truncate, grep 不到 attribute

**症状**: 我用 web_fetch 拉 api_server.py 拿到 2633 行, grep `_run_agent` 0 命中, 武断报 "P15 100% 破".

**真因**: web_fetch 实际 truncated. api_server.py 真 4406 行. `_run_agent` 在 line 3571.

**修**: 用 `curl -sL --max-time 10` 拉, 不用 web_fetch. 拉完先 `wc -l` 跟 GitHub 网页对照, 确认没截断.

### 坑 2: P10 circular import (v0.17 plugin discover 时序变)

**症状**: hermes restart 后 `cannot import name 'AIAgent' from partially initialized module 'run_agent'`. P10 patch 装不上, cascade 把 P15.2 chat_approval middleware 也跳过.

**真因**: v0.17 `run_agent.py:22` 顶 imports `from model_tools import (...)`. model_tools init 时触发 plugin discover → catfish register → P10 同步段 `from run_agent import AIAgent` 撞 partial init.

**修 (P3.5.50)**: P10 从同步段移到 `_delayed_install`. P15.2 拆独立 try (防 cascade). P10 wrap 是 AIAgent **instance** method, LLM first call 通常 > 30s 后, delayed install 装上 on time.

### 坑 3: HERMES_SERVICE_TOKEN 自动续期死链

**症状**: error.log `WARNING: P3.5.44 HERMES_SERVICE_TOKEN 剩 -242044 秒该续但 CATFISH_HERMES_CLIENT_SECRET 没配`. token 过期 74h, /api/* 全 401.

**真因 (3 嵌套)**:
1. `setup-catfish-edge.sh` 装机不自动 mint token, 只注释提示员工"用 mint script"
2. mint script env name `CLIENT_SECRET` ≠ plugin auto-renew env `CATFISH_HERMES_CLIENT_SECRET`
3. mint script 不持久化 secret (注释 "明文不存历史"), plugin 永远拿不到

**修 (P3.5.48)**:
- `_client_secret()` 加 `CLIENT_SECRET` fallback
- mint script 加 `--persist-secret` flag, 写 secret 到 .env
- setup script 加 step 3 检测 + 交互式收 + 调 mint --persist-secret 一次

### 坑 4: RBAC unknown tool warning (v0.17 Progressive Tool Disclosure)

**症状**: log 反复 `BL-RBAC-DAY4-HARDENING: 3 unknown tool name(s): tool_call, tool_describe, tool_search`.

**真因**: v0.17 新加 `tools/tool_search.py` Progressive Tool Disclosure feature. MCP + 非 core plugin tools 超 context 10% 时 hermes 自动用 3 bridge tool 替换暴露给 LLM. catfish `KNOWN_BUILTIN_TOOLS` 是 hermes 0.14 时代清单.

**修 (P3.5.49)**: `tools_sanitizer_constants.KNOWN_BUILTIN_TOOLS` 加 `tool_search/tool_describe/tool_call`. warning 文案改成 "hermes 升级新 builtin / plugin 新 tool 嫌疑" (不再说 tool_override).

### 坑 5: build 模式 chat "Could not connect" / dev 模式 OK

**症状**: dev mode chat work, prod build 后 chat 报 `TypeError: Load failed`.

**真因**: `tauri.conf.json:56` CSP `connect-src` 只 allow `http://127.0.0.1:*`, 不 allow `http://localhost:*`. catfish 全栈 `http://localhost:8642`. prod build WKWebView 严格 CSP host 字符串 match (`localhost ≠ 127.0.0.1`).

**修 (P3.5.50.2)**: connect-src 加 `http://localhost:* ws://localhost:*`.

### 坑 6: setup-catfish-edge.sh 后 Companion 仍用老 API_SERVER_KEY

**症状**: rotate 完 hermes restart 后 Companion 401.

**真因**: Companion Cmd+Q 没真 quit (Tauri app 后台仍跑). 老 OnceLock cache 老 key.

**修 (P3.5.50.1)**: setup script 完成后强提示 "Dock 右键 → 退出" (Cmd+W 关窗口 ≠ quit).

### 坑 7: picker UI 显 X 但 send 真发 Y

**症状**: UI 显 Qwen3.6-Flash, gateway log `model=catfish-private-main`.

**真因**: `store/chat.ts:reset()` 清 `modelPickedByUser=false` 但**不清** `model` (BL-GLOBAL-MODEL 5/23 设计). 之后 ChatTab catalog effect 看 `!modelPickedByUser` + `catalog.default !== model` 触发 `setModel(catalog.default, false)`. store.model 被静默覆盖. UI 显示有时滞后.

**修 (P3.5.52)**: `reset()` 删 `modelPickedByUser: false`. 用户 picker 选过的 model 整 Companion lifetime 锁定. "yaml 改 default → picker 跟走" 只在 fresh launch (zustand init) 时生效.

### 坑 8 (真正 root cause): _delayed_install chicken-and-egg 死锁

**症状**: catfish_tool_bridge 进程 install ✓, hermes daemon 进程 install ✗. chat 跑 tool call block. mcp-stderr 看 ✓ 但 gateway.log 看 "未装载".

**真因**: `_delayed_install` daemon thread `while sys.modules.get("model_tools") + hasattr get_tool_definitions`. **但** v0.17 hermes daemon (gateway run 模式) 不主动 import `run_agent` (chat lazy import). run_agent 顶 imports 才会引 model_tools. 没 chat 来 → model_tools 永远不在 sys.modules → 30s timeout → install 跳过 → `_INSTALLED=False` → safety_check raise → 任何 chat 都被 block.

**修 (P3.5.53)**: `_delayed_install` daemon thread 主动 `import model_tools` trigger, 不再被动 poll. sleep 0.5s 让主线程 register 完成跳过 partial init 段, 之后 `import model_tools` → model_tools ready → `_mod.install()` 直接跑. v0.17 model_tools 顶 imports 不撞 catfish plugin (no circular).

### 坑 10: SOUL.md catfish 该是 source of truth (鸿波 2 次 catch 后真理解)

**症状**: 客户装 Companion.dmg 后跑 chat, "你是谁?" 回复出现 "Nous Research" / "AI 助手" 字样. 开发者本机 OK, 只客户场景出. 或者 catfish 升级 SOUL.md 后, 员工本机停在老版本.

**真因 (代码直查)**:
1. `edge/identity/install.sh:64` `ln -s catfish/edge/identity/SOUL.md ~/.hermes/SOUL.md` — 软链反向耦合, hermes 读 catfish 源
2. 客户场景没 catfish git clone → 软链 target 不存在 → dangling → `Path.exists() = false`
3. `hermes-agent/agent/prompt_builder.py:1623 load_soul_md()` 返 None → `system_prompt.py:154` 注不上
4. catfish gateway `identity_inject.py` 同款逻辑 — bundle 全空 → 不注 system
5. LLM 无 system → 退化默认人格

**真正诉求 (鸿波 2nd catch)**:
> "**catfish 应该能修改 hermes soul.md 才对, 保证一致**"

catfish 是 source of truth. 应该 catfish 主动写 ~/.hermes/SOUL.md, **强制跟当前 catfish 版本一致** (overwrite), 不是反向让 hermes 引用 catfish 源, 也不是被动兜底.

**修 (P3.5.55 2nd 版)**:
- `identity_bundle.rs`: `include_str!()` 编译时内嵌 4 个 SOUL (~25KB 进 binary)
- `read_file_or_baked`: fs 非空用 fs / 空缺失用 baked (给 identity_bundle 命令走)
- `bootstrap_soul_files()`: Companion setup hook 主动同步, 4 状态枚举决定行为
  - **HealthySymlink** (开发者软链 OK): 不动
  - **DanglingSymlink** (客户): 删后写 baked
  - **RegularFile** (老 Companion 写的): **overwrite 写当前 baked** ← 关键
  - **Missing**: 写 baked
- `CATFISH_SOUL_NO_BOOTSTRAP=1` env escape hatch (调试)

**为啥 regular file 也 overwrite** (1st 版不做, 鸿波 catch 错):
- catfish V1 → V2 升级, 员工新 Companion 启动应该自动同步 V2 SOUL
- 不 overwrite 永远停 V1, 跟 catfish 脱节
- 员工自定义身份走 Dashboard preamble (X-Catfish-Agent-Name/Personality), 不动 SOUL.md
- SOUL 是品牌字段, catfish 垄断控制

**教训 (1st 版被 catch 的)**:
- "软链 + 改即生效" 是开发者便利, 不是分发策略
- 但**只补"被动兜底"还不够** — 一致性诉求要求 catfish **主动同步**
- "保证一致" = catfish 控制写权, 不是"有就 OK"
- 改之前先听清诉求, 别急着补局部

---

### 坑 9: user msg 三路 SQLite 写, dedup 互相覆不到 → UI 双 user bubble

**症状**: Companion chat 输入 "hi", UI 显两个一模一样的 "hi" 用户气泡 (右边 cyan). polling 5s 后 reload session 才出 (新输入瞬间只 1 个).

**真因 (sqlite3 直查 ground truth)**:
```
session 20260621_185745_5e9945:
  12387 user 'hi' @ 1782039465.224   ← Companion 写
  12388 user 'hi' @ 1782039465.573   ← hermes 350ms 后写
  12389 assistant 'hi，有啥事？'
```
3 路 SQLite 写, dedup 互相看不见:
1. Companion: `useChat.ts:619` IIFE `persistMessage(userMsg)` → Rust `session_message_append`
2. hermes: `gateway/run.py:9786` `append_to_transcript(_user_entry, skip_db=agent_persisted)`; agent 自己用 `_flush_messages_to_session_db()` 写 (#860 注释证实)
3. Rust idempotent guard `session_write.rs:278` 写死 `if input.role == "assistant"`, user 完全不查; hermes 跟 Companion 各用各的 connection, Rust guard 也覆不到 hermes

ChatTab.tsx polling 5s 后 `getSession()` reload → store.messages 多 1 行 user → UI 多 1 个 bubble.

历史 32+ 对 dup 最早 2026-05-24, 一直都有, v0.17 升级 + polling 路径让 UI 才看见.

**修 (P3.5.54)**:
- `useChat.ts`: 砍 `persistMessage(userMsg)` IIFE. hermes 是 user msg 唯一 writer
- 附件场景: `attachment_record` 改 fire-and-forget poll `getSession()` 找 hermes 写的 user row rowid (25 × 200ms = 5s), 拿到再 `attachment_record(messageId=hermesRowid)`. timeout 兜底用 client uuid (image base64 resume 失效但 chip 仍显)
- `scripts/cleanup-user-dup-rows.py`: 一次性清洗历史 dup, 自动 backup state.db, 留 Companion 写的早行删 hermes 写的晚行 (attachments.db messageId 挂在早行上)

**教训**: 不要从 UI 现象猜后端. **优先级: sqlite3 直查 > log grep > 代码静态审计 > UI 观察**.

---

## 四、catfish plugin install 真实结构 (这次 audit 才搞清楚)

```
hermes_cli main → gateway run --replace
  → import 各 platform adapter (gateway/platforms/*.py)
  → hermes_cli.plugins.discover_plugins()
    → 扫 ~/.hermes/plugins/ (catfish-xcatfish-user 软链在这)
    → 调 register(ctx)
      → catfish-xcatfish-user/__init__.py:register()
        Step 1: pre_tool_call_safety_check hook 注册
        Step 2.5: memory tool override (ctx.register_tool)
        Step 2.7 SYNC: P0/P1/P3/P4/P7/P8/P9 直接装
          注意: P8/P9 内的 _patched_app_init 把 _aw.Application.__init__ patch
                让 hermes 之后创 Application 时自动注入 P11 + P7 + P15.2
                middleware (path B, work)
        Step 2.7b SYNC: P15.2 chat_approval middleware (独立 try, P3.5.50 拆)
        Step 3: 启 daemon thread _delayed_install
          P3.5.53: 主动 import model_tools → 触发完整 model_tools/run_agent import
          → _mod.install()
            → _verify_patch_targets()
            → _apply_patches() — 跑 P0-P19 全套
              注意: P0/P1/P3/P4/P7/P8/P9 同步段已跑, 这里幂等再跑
                    P5/P6/P11/P10/P12/P13/P14/P15/P16/P17/P18/P19 真在这里装
            → _PATCHED = True, _INSTALLED = True
        register 返
  → hermes 主流程继续
    → APIServerAdapter.connect()
      → 创 self._app = web.Application(middlewares=mws, ...)
        → _patched_app_init fence 检测 mws 含 cors_middleware → inject:
          - _request_stash_middleware (P11)
          - _proxy_404_middleware (P7)
          - _chat_approval_middleware (P15.2)
      → app listen 8642
```

**关键**: P11/P7/P15.2 中间件**通过 path B** (`_patched_app_init` Application.__init__ patch) 在 P8/P9 同步段被装上, 早于 `_delayed_install`. 即使 `_delayed_install` 失败, 这些 middleware 还是真生效的. 但 `_INSTALLED` flag 没 set, safety_check 会 raise, 让 chat 全 fail.

**结论**: `_delayed_install` 必须 work, 否则 `_INSTALLED` False → chat block.

---

## 五、catfish 在 hermes 进程间分布

```
ai.hermes.gateway.plist (launchd) → hermes 主 daemon
   PID X /usr/.../python -m hermes_cli.main gateway run --replace
   → 8642 API server (Companion chat)
   → 各 platform adapter (weixin/feishu/etc)
   → catfish-xcatfish-user plugin 装这里 (chat tool call block 看这里 _INSTALLED)
   → log: ~/.hermes/logs/gateway.log (stdout, INFO) + gateway.error.log (stderr, WARN+)

  └→ subprocess: catfish_tool_bridge mcp_server
       PID Y /usr/.../python -m catfish_tool_bridge.mcp_server
       → MCP server stdio, hermes daemon 调它
       → 它**也 import catfish plugin** (因为 venv 共享), 也跑 register/install
       → log: ~/.hermes/logs/mcp-stderr.log
       → 这进程 install ✓ ≠ hermes daemon install ✓ !!!

  └→ subprocess: catfish_tool_bridge socket-mode
       PID Z /usr/.../python -m catfish_tool_bridge --socket ~/.catfish/tool-bridge.sock
       → Companion Tauri Rust 端通过 unix socket 调它
       → log: stdout (catfish-tool-bridge 自己 log)
```

**这次最大教训**: 我看到 mcp-stderr.log "install ✓" 就以为 hermes daemon plugin 装好. 错. 这是 mcp_server 子进程的 install. **hermes daemon 自己的 install 在 gateway.log/error.log**, 跟 mcp-stderr 是 2 个独立进程.

---

## 六、cowork 跟鸿波协作的纪律 (8 次错猜后总结)

### 一定要做的
- 每次 hypothesis 提出, 写明"这是猜, 真验证靠 ground truth X"
- 真 ground truth 出来跟 hypothesis 不符 → **立即丢 hypothesis, 重新审**
- 不要在中间步骤跳到结论
- 长文件 / oversized output 一律 bash curl 拉到 host, 不要 web_fetch (truncates)
- 配置/构建模式差异 first principle: CSP / capability / ATS, 不是进程 lifecycle
- 进程 "重启" 一定 verify PID 换过 (`pgrep -fl` 真 truth)

### 一定不要做的
- "应该 work 了" 没真测过就 declare done
- 看到部分 ground truth 就推出全图 (e.g. mcp-stderr ✓ ≠ hermes daemon ✓)
- 把 web_fetch truncated 当完整 source grep
- 把 `<select>.value = ""` 推到第一个 select (页面多个 select, 用 `querySelectorAll` 全列)
- 把 zustand store.model = "X" 等于 picker UI 显 X (HMR 双 instance / selector 时序)
- reset() 改 modelPickedByUser 不动 model = 静默 bug 等 catalog effect 来覆盖

### Audit 顺序模板
1. 用户 visible (UI 显示 / 报错文字)
2. 客户端 state (devtools Console / Network)
3. 网络 (curl / lsof / hosts)
4. 服务端入口 (request log + middleware 真触发)
5. 服务端业务逻辑 (handler / function)
6. 服务端下游 (database / upstream API)
7. 配置 (env / yaml / config)
8. 进程/构建模式差异

每步 ground truth 出来再决定下一步, 不要跳级.

---

## 七、Sprint P3.5.47-P3.5.53 全 ship 清单

| ticket | 修的 | 验证 |
|---|---|---|
| P3.5.47 | hermes v0.17 audit script 补 19 patch + bug fix | 53/0 pass |
| P3.5.48 | HERMES_SERVICE_TOKEN 治本自动续期 (3 嵌套真因) | mint + persist 一次写盘 |
| P3.5.49 | RBAC unknown tool — v0.17 progressive disclosure 3 bridge | log 无 warning |
| P3.5.50 | P10 circular import + P15.2 cascade + bind poll + secret prompt | 6s bind |
| P3.5.50.1 | UX 强提示真 quit Companion | 文案 |
| P3.5.50.2 | CSP connect-src 加 http://localhost:* | build mode chat work |
| P3.5.51 | P11/P6 probe 临时 WARNING (后回 debug) | verified picker chain |
| P3.5.52 | reset 不清 modelPickedByUser | picker UI 锁定 user 选择 |
| **P3.5.53** | **_delayed_install 主动 trigger model_tools import** | **gateway 真用 catfish-public-qwen-flash** |

---

## 八、关键代码位置 cheatsheet

```
edge/hermes-plugins/catfish-xcatfish-user/
├── __init__.py
│   ├── register(ctx) — hermes plugin entry, 同步段 + _delayed_install
│   ├── _delayed_install — P3.5.53 主动 import model_tools 修死锁
│   └── pre_tool_call_safety_check — 看 _INSTALLED, False 时 raise
├── plugin.py
│   ├── install() — _PATCHED guard + _apply_patches + 设 _INSTALLED=True
│   ├── _apply_patches() — 跑 P0-P19 全套
│   ├── _request_stash_middleware (P11) — body.model → CV_PICKER_MODEL
│   ├── _patched_app_init (在 _patch_p8_p9_cors 内) — Application.__init__
│   │   monkey-patch, 创 Application 时自动 inject P11/P7/P15.2 middleware
│   └── patched_create_agent (P6) — agent.model = CV_PICKER_MODEL.get()
├── hermes_token_renewal.py
│   ├── _client_secret() — 读 CATFISH_HERMES_CLIENT_SECRET / CLIENT_SECRET fallback
│   └── get_fresh_service_token() — JWT exp 检 + mint
└── memory_router.py — memory tool 5 kind 路由

edge/companion-app/
├── src/store/chat.ts
│   ├── setModel(model, pickedByUser=true) — 设 store.model + invoke set_picker_model
│   └── reset() — 不清 model 不清 modelPickedByUser (P3.5.52)
├── src/tabs/Chat/ChatTab.tsx — catalog effect propagate default (line 166-172)
├── src/tabs/Chat/ChatModelPicker.tsx — <select value={current}> 真 picker UI
├── src/hooks/useChat.ts — sendModel = useChatStore.getState().model
├── src/lib/chat.ts — body.model = effectiveModel, fetch hermes 8642
├── src-tauri/tauri.conf.json — CSP connect-src (P3.5.50.2)
├── src-tauri/src/services/hermes_api_config.rs — OnceLock cache key
└── src-tauri/src/commands/picker_state.rs — 写 ~/.catfish/picker_state.json

central/llm-gateway/src/catfish_gateway/
├── tools_sanitizer.py — _audit_unknown_tools (P3.5.49)
├── tools_sanitizer_constants.py — KNOWN_BUILTIN_TOOLS (含 v0.17 3 bridge tool)
└── app.py — chat completions handler

scripts/
├── setup-catfish-edge.sh — 装机 (step 3 token + secret 自动配, P3.5.48)
├── mint-hermes-service-token.sh — token mint (--persist-secret flag, P3.5.48)
└── audit-old-memory.py — memory audit

edge/catfish-cli/scripts/
└── audit_hermes_compat.sh — hermes 升级前/后跑这个 (P3.5.47 加全 19 patch)
```

---

## 九、下次 hermes 升级 — 1 行 verify

```bash
# 升级 + 验证一条龙
HV="v2026.X.Y" && \
  cd ~/.hermes/hermes-agent && git fetch && git checkout "$HV" && \
  HERMES_ROOT=~/.hermes/hermes-agent bash ~/person_task/catfish/edge/catfish-cli/scripts/audit_hermes_compat.sh && \
  hermes gateway restart && sleep 12 && \
  grep -c "delayed install ✓" ~/.hermes/logs/gateway.log && \
  echo "✓ 升级成功. Companion 跑一条 chat verify chat e2e."
```

出错时, 按本文 "Audit 真因链 — 8 步顺序" 走, 每步 ground truth verify, 不要跳.
