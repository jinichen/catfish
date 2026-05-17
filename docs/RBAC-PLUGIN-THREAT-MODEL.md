# RBAC Plugin Threat Model (hermes 0.14)

5/17 ship 配 BL-RBAC-DAY4-HARDENING (#75).

## 背景

hermes 0.14 (2026.5.16 The Foundation Release) 引入两个能力, 直接对应 catfish
RBAC 的 bypass 风险:

- **`tool_override`** (#26759): plugin manifest 字段, 客户安装的 hermes plugin
  可以**重命名 built-in tool**。 例: 把 `catfish_browser_open` 重命名成
  `catfish_run_skill`。 LLM 看到的是后者 (dept allowed), 但实际调度的是前者。
- **`ctx.llm`** (#23194): plugin context API, plugin 可以**用自己的凭证 / base_url
  直接发 LLM call**。 完全绕开 catfish-gateway, allowed_models / quota / audit
  全失效。

这两个**单靠 catfish-gateway 代码无法完全防御**, 因为:

- tool_override: gateway 收到的是已经 rename 后的 tool name, 没有原始来源信息
- ctx.llm: 请求根本不经过 gateway, 中央层看不到

## 分层防御

### 1. 代码层 (gateway, 5/17 已 ship)

- **`_audit_unknown_tools`** (`tools_sanitizer.py`): 维护
  `_KNOWN_BUILTIN_TOOLS` 白名单 (catfish 原生 + hermes 0.13/0.14 builtin)。
  收到的 tool name 不在白名单 + 不是 `mcp__*` 前缀 + 不在 dept
  `effective_allowed_tools` 显式列 → **log WARN 不 drop**。 不 drop 是因为
  客户自家 plugin 命名千差万别, drop 会误杀正常使用; allowlist 由 dept admin
  在 `/admin/access` 显式配置。
- **`source` audit 字段** (`metrics.py` log_request_metadata): 读
  `X-Catfish-Source` header, audit jsonl 记每次 chat completion 来源。
  Companion 显式发 `companion`, 客户写的 hermes plugin 用 `ctx.llm` 默认
  `unknown`。 `audit.jsonl` grep `"source":"unknown"` 占比 = 部署里有多少
  plugin LLM call 绕开了 gateway。
- **`_HIDDEN_FROM_LLM`** (5/16): `catfish_remember` /
  `catfish_memory_dedupe` / `catfish_memory_compress` 永不暴露给 LLM, 已存在。

### 2. 部署 / Ops 层 (客户 IT, 必做)

代码层只能 detect, 不能 enforce。 真正堵 ctx.llm bypass 靠客户 IT:

1. **客户公司 firewall 出口**: 员工工作站只放行 catfish-gateway
   (`https://catfish-gateway.company.com`) 的出口, 直接连 OpenAI / Anthropic /
   OpenRouter 的连接被防火墙 drop。 plugin 用 ctx.llm 配 base_url=openai 直接走
   不通, 必须 base_url=catfish-gateway。
2. **hermes plugin config 模板**: 客户 IT 推送的 `~/.hermes/config.yaml`
   强制 `default_base_url: https://catfish-gateway.company.com`, 且配
   `api_key` 是员工自己的 catfish JWT。 plugin ctx.llm 默认拿这个, 不另发
   `api_key` 就走员工身份, catfish-gateway 看得到。
3. **plugin 安装审计**: hermes 0.14 plugin install 流程客户 IT 审 manifest,
   含 `tool_override` 字段的 plugin 必须**特批**, dept admin 在
   `/admin/access` 加该 plugin override 后的新工具名进 `allowed_tools`。
4. **每周 audit**: 客户 IT 跑 audit script grep
   ```
   jq 'select(."BL-RBAC-DAY4-HARDENING" or .source == "unknown")' audit.jsonl
   ```
   看 unknown 占比 + unknown tool 频率, 异常飙高 = 部署里多了恶意 plugin。

### 3. Companion 层 (5/17 + future)

- **5/17 已**: Companion 必须发 `X-Catfish-Source: companion` header (前端
  Tauri 加 fetch 拦截器添)。 未实施部分 — Companion 端代码改 follow-up。
- **future**: Companion 发 `X-Catfish-Tool-Manifest` JSON header, 列每个
  tool 的源头 (`{"name": "memory", "source": "hermes_builtin"}`). gateway
  对比 body.tools 数组里的 name, 不匹配拒。 工程量大, 等真有 plugin 攻击
  audit 上暴露再做。

## 已知限制

- **真在恶意环境里**, 客户机 hermes 被替换成 fork, X-Catfish-Source header
  可以伪造。 catfish 中央层无法 100% 防, 靠 employee identity (JWT) + 行为
  审计 + dept admin 警觉。
- **`mcp__*` 前缀** 默认信任, 因为 MCP server tool name 已有标准命名约定 +
  客户机 IT 装 MCP server 时就会审。 但 plugin 如果伪装成 MCP server (用
  `mcp__` 前缀给自己 tool) 会绕 unknown audit。 真要堵需要 MCP server
  registration 走客户 IT 审批 (out of scope)。

## 与 Day 4 RBAC 的关系

- **Day 4 (#66, 已 ship)**: `User.can_use_tool(name)` + `sanitize_tools` 按
  `effective_allowed_tools` drop 不允许 tool。 这是 **name-based whitelist
  enforcement**, 假设 tool name 真实反映 tool 行为。
- **Day 4 Hardening (#75, 5/17 已 ship)**: 不再假设 name 真实, 加 audit 层
  看 unknown 模式 + plugin source 比例。 实际 enforce 仍靠 Day 4, hardening
  只多一层"看见"。

## 测策略

- `tests/test_allowed_tools.py` 已 ship 11 case (Day 4)
- `tests/test_unknown_tool_audit.py` (待加, 跟着 #75 commit): 验
  `_audit_unknown_tools` 在 plugin override 场景下能识别 + log WARN

## 监测

部署后一周, audit jsonl 跑:

```bash
# X-Catfish-Source unknown 比例 (plugin ctx.llm bypass 嫌疑)
jq -r 'select(.type == "llm_request") | .source // "unknown"' audit.jsonl | sort | uniq -c

# unknown tool name 频率 (tool_override 嫌疑)
grep "BL-RBAC-DAY4-HARDENING" audit.jsonl | jq -r '.unknown // empty' | sort | uniq -c
```

unknown source > 5% 或单 unknown tool 频率 > 50/day → 触发部署 audit。

## 参考

- BL-RBAC-DAY4 (#66): allowed_tools per-dept/user enforce
- hermes 0.14 release notes: #26759 tool_override, #23194 ctx.llm
- docs/CATFISH-HERMES-BOUNDARY.md: catfish/hermes 责任边界
