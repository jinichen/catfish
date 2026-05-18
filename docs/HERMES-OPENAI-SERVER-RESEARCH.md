# Hermes OpenAI-Compatible API Server 调研 + Companion 切换 Plan

> **状态**: 调研报告. 5/19 凌晨写, 给 BL-COMPANION-SWITCH-TO-HERMES-SERVE
> 实施者当 spec.
>
> **触发**: BL-MEMORY-OWNERSHIP-FIX Phase 2 第二步.

## 1. 关键发现

Hermes 内置 **OpenAI 兼容 API Server**, 端口默认 `127.0.0.1:8642`.

来源: `~/.hermes/hermes-agent/website/docs/user-guide/features/api-server.md`

> The API server exposes hermes-agent as an OpenAI-compatible HTTP endpoint.
> Any frontend that speaks the OpenAI format — Open WebUI, LobeChat,
> LibreChat, NextChat, ChatBox, and hundreds more — can connect to
> hermes-agent and use it as a backend.

**这正是我们要的** — Companion 当作 OpenAI 兼容 client 调它, hermes 自己跑 agent loop + memory inject + 调下游 LLM (走 catfish-gateway).

## 2. 启用

### 2.1 `~/.hermes/.env`

```bash
API_SERVER_ENABLED=true
API_SERVER_KEY=<选一个 token, 跟 Companion 共享>
# 可选: 浏览器直接调 hermes 需要 (Tauri WebView 通常不需要 CORS)
# API_SERVER_CORS_ORIGINS=http://localhost:1420
```

### 2.2 启动

```bash
hermes gateway
# 启动后 stdout 有:
# [API Server] API server listening on http://127.0.0.1:8642
```

注意 `hermes gateway` 是个聚合命令, 同时启动消息平台 gateway (Telegram/微信/etc) **和** API server (如果 env enable 了). 不是只跑消息平台.

### 2.3 验证

```bash
curl http://localhost:8642/v1/chat/completions \
  -H "Authorization: Bearer <你的 API_SERVER_KEY>" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "hermes-agent",
    "messages": [{"role": "user", "content": "Hello"}],
    "stream": false
  }'
```

## 3. 接口对齐 (Companion 切换需要改的地方)

### 3.1 endpoint URL

| 维度 | 当前 (Companion 调 gateway) | 改后 (Companion 调 hermes) |
|---|---|---|
| URL | `http://localhost:8999/v1/chat/completions` | `http://localhost:8642/v1/chat/completions` |
| Authorization | Bearer `<id_token>` 或 `<service_token>` (catfish OIDC) | Bearer `<API_SERVER_KEY>` (跟 hermes 共享密钥) |
| Model | catfish-private-main / catfish-public-qwen-flash / etc | **`hermes-agent`** (固定字符串, hermes 内部解析真 model) |
| messages | 标准 OpenAI 格式 | 标准 OpenAI 格式 (兼容) |
| tools | OpenAI tool schema 数组 | hermes 内部管理 tool, **Companion 不应该传 tools** |
| stream | true / false | true / false (hermes 都支持) |

### 3.2 流式 SSE

格式跟 OpenAI 一致, hermes 流式时还在 chunk 里**显示 tool 进度** (例如"调用 catfish_email_search..."), Companion 老 SSE parser 应该兼容.

### 3.3 tool calling

**重要变化**: hermes 内部 agent loop 处理 tool calls, Companion **不再需要**:
- 自己累积 `tool_calls` chunk
- 自己调 tool-bridge
- 自己回灌 tool result 进新一轮 chat/completions

这些**全部由 hermes 内部做**. Companion 只看到最终的 assistant 文字回复.

**影响**:
- `chat.ts` 大幅简化 — 删 tool_call 累积逻辑 + 删 tool-bridge dispatch
- `useChat.ts` `MAX_TOOL_ROUNDS=20` 不再需要 — hermes 自己管 tool rounds
- tool 进度 SSE chunk 改成在消息里显示 "🔧 调用 X..." 进度行 (跟 ChatGPT 显式 tool 进度类似)

## 4. Companion 切换 plan (拆解 BL-COMPANION-SWITCH-TO-HERMES-SERVE)

### Phase 2-2A: 配置 + 安装 (半天)

- **BL-COMPANION-HERMES-API-CONFIG**: Companion `~/.catfish/companion.yaml` 加
  `hermes_api: { url: http://localhost:8642/v1, key: <shared> }` 配置段
- **BL-CATFISH-EDGE-INSTALL-DOC**: 写 docs/CATFISH-EDGE-INSTALL.md 给员工 IT
  装 catfish-edge (含 Companion + hermes + catfish-tool-bridge +
  catfish-memory plugin + .env 配 API_SERVER_*)

### Phase 2-2B: chat.ts 大改 (3-4 天)

- **BL-COMPANION-CHAT-USE-HERMES**: `lib/chat.ts` 改 endpoint + auth header
  - URL 从 gatewayUrl → hermesApiUrl
  - Authorization 从 id_token → API_SERVER_KEY
  - body.model 从员工选的 → `"hermes-agent"`
  - 删 `tools` 数组发送 (hermes 自己管)
  - 删 fallback 切模型逻辑 (model 是 hermes-agent, 不能"切")
  - 保留 500/502 重试 (hermes 也可能挂)
  - 保留 streaming SSE 解析

- **BL-COMPANION-CHAT-DROP-TOOL-DISPATCH**: 删 useChat 内 tool_call 累积 +
  调 tool-bridge 路径. hermes 内部全管, Companion 只显进度
  - `MAX_TOOL_ROUNDS=20` 删
  - `dispatchTool` / `callToolBridge` 删
  - tool 进度 chunk 在消息里渲染为 "🔧 ..." 行

- **BL-COMPANION-FETCH-WITH-AUTH-HERMES**: `lib/me.ts` `fetchWithAuth` 路径
  改: hermes API server 用 API_SERVER_KEY (固定 token, 不过期), 不需要
  OAuth re-auth. fetchWithAuth 路径退化成"加 header + fetch"
  - 401 reauth 路径**不再有意义** — hermes API_SERVER_KEY 不过期
  - 删 `auth-refreshed` event 路径

### Phase 2-2C: model 选择 UI (1-2 天)

- **BL-COMPANION-MODEL-PICKER-VIA-HERMES**: Companion 顶部 model 选择器
  原 catalog 来自 gateway. hermes 把 model 选择是它内部 config 决定的
  (`~/.hermes/config.yaml model: ...`), 不是 Companion 传 model 名
  
  两个路径:
  - A. 删 Companion 那侧 model 选择器, model 在 hermes config 那边配 (员工
    通过 hermes CLI 或 Companion 转给 hermes 改). 简化 UI
  - B. 加 hermes API extension 让外部能选 model (hermes 内部支持
    `model: "anthropic/claude-sonnet-4"` 之类的路由 prefix). 需要 hermes
    支持外部 model 路由参数, 不确定有
  
  **倾向 A** — 简化 UI, 跟 ChatGPT 一致 (它也没法在 client 选 model).

### Phase 2-2D: 实盘验证 (1-2 天)

- **BL-COMPANION-HERMES-E2E**: 全链路实盘
  - 启动 hermes (API server enable)
  - 启动 Companion (改后)
  - chat 完整流程: 普通对话 / tool 调用 / 长 task
  - 跟 hermes 直接 curl 对比, 行为一致

- **BL-COMPANION-HERMES-AUTH-DESIGN**: API_SERVER_KEY 怎么管
  - 选 1: 安装 catfish-edge 时随机生成存 ~/.hermes/.env + ~/.catfish/companion.yaml
  - 选 2: catfish-identity 签发短期 token 给 hermes (要 hermes 接受 OIDC, 不
    确定)
  - **倾向选 1** — 简单, 单机部署够用

## 5. Hermes API Server 已知限制

1. **stateless** — full conversation 每次发, hermes 不存 session (但走自己
   state.db 写 messages 表). Companion side 没变化
2. **inline image** — `image_url: {url, detail}` 支持 (跟 OpenAI 同), 通过 hermes
   自己 vision 处理. Companion 不变
3. **streaming** — `stream: true` 支持. 流式时 tool 进度作为 chunk 一并发, 不
   是 OpenAI 标准 tool_call delta 格式. Companion SSE parser 可能要适配
4. **tools 参数** — 不支持 (按文档). hermes 内部决定 tool. 我们 Companion 之
   前给 gateway 传 `tools` 数组, 改后**不传**
5. **model 参数** — 期望固定 `"hermes-agent"`, 不是真 model 名. 真 model 在
   hermes 内部 config 决定

## 6. 风险 / open question

1. **API_SERVER_KEY 单点**: 一旦泄漏整个 hermes 给外人调. 安装时随机生成 +
   chmod 600 .env 减风险
2. **Companion-hermes 进程协调**: Companion 启动时怎么确保 hermes 在跑?
   - 选 A: Companion Tauri 启动时检查 hermes 进程, 没起 → fork 起
   - 选 B: 装 launchd / systemd 保证 hermes 后台一直跑 (catfish-edge 安装做)
   - **倾向 B** — 跟 hermes daemon 模式对齐
3. **catfish-memory plugin 自动激活**: 安装 hermes plugin 时 install-catfish-
   memory.sh 已经改 config.yaml 激活. 但员工跑 `hermes memory setup` 可能误
   切到别的 provider. catfish-edge 安装文档加说明 "memory.provider 别动"
4. **没拿到 hermes API server gating 文档**: 还没确认 hermes 是否限制并发 /
   有 rate limit. 实盘验证时压测一下

## 7. 时间预估

- Phase 2-2A 配置 + 安装: 0.5 天
- Phase 2-2B chat.ts 大改: 3-4 天
- Phase 2-2C model UI: 1-2 天 (取决于走方案 A 还是 B)
- Phase 2-2D 实盘验证: 1-2 天
- **合计**: ~1 周

跟 MEMORY-OWNERSHIP-ARCHITECTURE.md Phase 2 BL-COMPANION-SWITCH-TO-HERMES-SERVE 估时一致.

## 8. 5/19 凌晨实盘验证步骤 (建议)

让员工 (鸿波) 本机现在做 30 分钟验证, 解最后一个 unknown 让 5/19 白天能干

```bash
# 1. 启用 hermes API server
echo "API_SERVER_ENABLED=true" >> ~/.hermes/.env
KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")
echo "API_SERVER_KEY=$KEY" >> ~/.hermes/.env
echo "API_SERVER_KEY: $KEY  # 记下来"

# 2. 启动 hermes gateway (前台跑看 log)
hermes gateway 2>&1 | tee /tmp/hermes-api-server.log &
HG_PID=$!
sleep 5

# 3. 验证 API server 起了
curl -sS http://localhost:8642/v1/models -H "Authorization: Bearer $KEY" | head -c 500
echo

# 4. 跑一句 chat 测 prefetch 真注入
curl -sS http://localhost:8642/v1/chat/completions \
  -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -d "{\"model\":\"hermes-agent\",\"messages\":[{\"role\":\"user\",\"content\":\"今天可以跑什么技能?\"}],\"stream\":false}" \
  | head -c 2000

# 5. 看 hermes log 含 catfish-memory prefetch
grep -i "catfish-memory\|prefetch" /tmp/hermes-api-server.log | head -10

# 6. 清理
kill $HG_PID 2>/dev/null
```

预期: step 4 的 response 含有"鲶鱼"基于员工真历史的回答 (因为 catfish-memory prefetch 注入了员工 EIS / 资质 / alice 等), step 5 看到 catfish-memory 被 hermes 调.

如果 step 4 报错, 看 step 2 的 hermes-api-server.log 找原因. 一般是 API_SERVER_KEY 没读到 / hermes config model 没配 / 上游 LLM 挂.

## 9. References

- hermes 0.14 docs: `~/.hermes/hermes-agent/website/docs/user-guide/features/api-server.md`
- catfish memory architecture: `docs/MEMORY-OWNERSHIP-ARCHITECTURE.md`
- catfish-memory plugin: `edge/hermes-plugins/catfish-memory/README.md`
- BL-FIX32 token 文件存储: `edge/companion-app/src-tauri/src/services/oauth.rs`
- catfish-tool-bridge: `edge/tool-bridge/`
