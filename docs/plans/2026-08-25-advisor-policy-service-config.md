# Advisor 调用边界与终端工具能力修复 Implementation Plan

> 实施状态（2026-08-25）：代码修复和本地自动化验证已完成；真实 Qwen/DeepSeek
> 双模型调用未自动触发，避免未经确认消耗线上配额。

**Goal:** 保持模型级配置由现有中央模型库统一管理，同时将 Advisor 调用语义和结果质量校验固定在 Companion 代码中，并让每个终端只向模型暴露本机真实可用的工具。

**Architecture:** 不新增 source policy 数据库、API 或管理页面。Companion 负责 Advisor prompt、结构化输出合同、场景级采样参数和本地质量门；Gateway 负责模型级参数、协议兼容、RBAC 与代码级 source 安全裁剪；Tool Bridge 负责终端运行时能力；MCP Registry 只负责组织共享连接器。

**Tech Stack:** Python、FastAPI、Pydantic、React、TypeScript、Rust/Tauri、pytest、Vitest。

---

## 一、代码复核后的修正结论

### 1. 不应建设 `gateway_source_policies`

现有模型配置已经由 `gateway_models` 和中央模型管理页维护，包含：

- 供应商、上游模型和端点；
- timeout、context window、max output tokens；
- 工具、图片、流式能力；
- 模型级 `param_overrides`；
- fallback、计费和展示属性。

再建设一套 source policy 会形成第二个配置事实源，并产生难以解释的覆盖顺序：

```text
Companion request
  → source policy override
  → model param_overrides
  → Gateway max_tokens 裁剪
  → thinking_guard
```

这会让同一次请求最终为何采用某个参数难以定位，也会让管理员误以为场景参数可以覆盖模型协议约束。

因此删除原方案中的：

- `gateway_source_policies` 表和 Alembic migration；
- source policy CRUD/API；
- 中央 Source Policy 管理页；
- Companion policy 拉取、TTL、revision 和缓存绑定；
- shadow/enforce 双轨上线模式。

### 2. 模型参数和调用参数不是同一类配置

必须按所有者区分：

| 类别 | 示例 | 唯一事实源 |
|---|---|---|
| 模型/供应商属性 | endpoint、upstream model、timeout、context window、max output、能力、协议强制参数 | 现有中央模型配置数据库 |
| Advisor 调用语义 | system prompt、source、temperature、期望 max tokens、response format、transform schema | Companion 版本化代码 |
| Gateway 安全与兼容 | token 裁剪、消息规范化、thinking guard、source 工具边界、RBAC | Gateway 代码 |
| 结果质量与缓存准入 | 占位回复识别、输入证据重叠、任务过滤、旧缓存安全 | Companion 代码 |
| 终端工具可用性 | OS、依赖、adapter、必要配置和运行状态 | Tool Bridge 运行时 |
| 组织共享工具 | 企业连接器发布、部门过滤、员工订阅 | MCP Registry |

`temperature=0.4` 和 transform 的 `temperature=0.1` 表达的是两个调用的不同任务语义。把它们放进模型级 `param_overrides` 会影响同一模型的普通聊天、邮件、画像等其他调用，因此仍应由 Advisor 调用方指定。

`max_tokens=6000` 是 Advisor 期望的输出预算；Gateway 继续根据中央模型配置的 context window 和 max output tokens 做最终裁剪。两者不存在重复管理。

### 3. Advisor 质量门必须留在 Companion

`briefing_advisor_quality.ts` 的判断依赖本轮本地输入：

- profile、emails、events、todos；
- 本机上下文和 wiki 命中；
- 任务 title、contextRefs；
- 本地 Advisor cache。

这些规则不是普通数字配置，而是和 TypeScript 类型、解析流程、缓存写入顺序绑定的确定性程序。把关键词、证据提取字段或阈值存进数据库会带来：

- 服务端规则与客户端数据结构版本不匹配；
- 老客户端读到新规则后误拒或误收结果；
- 正则或自由文本策略成为不可审查的生产代码；
- 为了质量判断额外上传终端上下文，扩大隐私边界；
- 中央不可达时无法判断本地缓存是否安全。

因此质量规则继续以代码和单元测试发布，不开放管理员编辑。中央只接收调用和审计结果，不参与本地缓存准入。

### 4. 不建设中央原生工具目录

现有 `central/mcp-registry` 只负责组织共享连接器，不是 Catfish native 或 Hermes builtin 的目录。

一次请求最终可交给模型的工具为：

```text
有效工具 = 终端本次实际发送的工具
        ∩ 用户/部门 RBAC
        ∩ Gateway 代码级 source 安全边界
        − 不可配置安全禁用项
```

终端实际工具为：

```text
终端实际工具 = 本机已安装/注册工具
            ∩ 当前 OS 支持
            ∩ 依赖、adapter 和必要配置就绪
```

Gateway 只能做减法，不能从中央配置补充终端没有发送的工具。

### 5. 当前仍需修复的真实问题

- `adapter.py:list_tools()` 返回全部 `CATFISH_NATIVE_TOOLS`；
- 多数 native schema 静态写 `available: true`；
- Reminders/Calendar 到执行时才判断 Darwin；
- `mcp_server.py` 转 MCP Tool 时忽略 `available`；
- Windows 因此可能向模型暴露 macOS-only 工具；
- named `tool_choice` 仍可能绕过 Gateway source allowlist；
- `adapter.py`、`tools_sanitizer.py`、`briefing_advisor.ts` 已接近 800 行，新增逻辑必须拆模块。

---

## 二、实施任务

### Task 1：用测试固定参数所有权和覆盖顺序

**Files:**

- Modify: `central/llm-gateway/tests/test_litellm_params_max_tokens.py`
- Modify: `central/llm-gateway/tests/test_thinking_guard.py`
- Create: `edge/companion-app/src/lib/briefing_advisor_request.test.ts`

**Steps:**

1. 增加测试：Advisor 的期望 `max_tokens` 只能被 Gateway 按模型上限向下裁剪，不能超过中央模型配置。
2. 增加测试：模型 `param_overrides` 最后执行，用于供应商协议硬约束；测试中不得按 Qwen、DeepSeek 的公开模型名分支。
3. 增加测试：强制 named `tool_choice` 时 `thinking_guard` 只调整该次请求，不修改模型持久配置。
4. 增加 Companion 测试：agent call 和 transform call 使用不同的场景参数，但共用同一模型 ID。
5. 运行定向测试并确认先失败。

### Task 2：从大文件抽出 Advisor 请求合同

**Files:**

- Create: `edge/companion-app/src/lib/briefing_advisor_request.ts`
- Modify: `edge/companion-app/src/lib/briefing_advisor.ts`（只保留薄接线）
- Reuse: `edge/companion-app/src/lib/briefing_advisor_prompts.ts`
- Test: `edge/companion-app/src/lib/briefing_advisor_request.test.ts`

**Steps:**

1. 将 Call 1 请求体构造抽成纯函数，保留：`source=companion-advisor`、`max_tokens=6000`、`temperature=0.4`、`stream=false` 和 JSON response format。
2. 将 Call 2 请求体构造抽成纯函数，保留：`source=companion-advisor-transform`、`max_tokens=6000`、`temperature=0.1`、本地 `submit_advisor_result` schema 和 named tool choice。
3. 参数常量按“agent 分析”和“structured transform”命名，不出现 Qwen/DeepSeek 条件分支。
4. `briefing_advisor.ts` 只调用构造函数，不继续堆请求协议代码。
5. 测试两个调用合同的差异和固定字段。

### Task 3：保持本地质量门并补齐缓存准入测试

**Files:**

- Modify: `edge/companion-app/src/lib/briefing_advisor_quality.test.ts`
- Modify: `edge/companion-app/src/lib/briefing_advisor_quality.ts`（仅在测试暴露缺口时修改）
- Modify: `edge/companion-app/src/tabs/Briefing/AdvisorView.tsx`（只允许薄接线）

**Steps:**

1. 固定“系统初始化、等待用户输入、需要我做什么”等占位回复必须拒绝。
2. 固定有 TODO 但没有有效主任务时不得写缓存。
3. 固定混合结果只删除无输入证据的任务，保留有证据任务。
4. 固定旧缓存至少经过 `isAdvisorResultCacheSafe()` 后才能显示。
5. 不增加中央规则拉取、revision 或 fail-open/fail-closed 网络分支。
6. 质量规则修改必须跟 Companion 版本和测试一起发布。

### Task 4：建立 Tool Bridge 终端能力判断

**Files:**

- Create: `edge/tool-bridge/src/catfish_tool_bridge/tool_availability.py`
- Modify: `edge/tool-bridge/src/catfish_tool_bridge/catfish_tool_schemas_*.py`
- Modify: `edge/tool-bridge/src/catfish_tool_bridge/adapter.py`（只做 import 和调用）
- Create: `edge/tool-bridge/tests/test_tool_availability.py`

**Steps:**

1. 为 native schema 声明代码级运行平台元数据；Reminders/Calendar 标记 Darwin-only。
2. `list_tools()` 复制 schema 后动态覆盖 `supported`、`available` 和封闭 `reason_code`，不得修改全局 schema 对象。
3. 当前有证据的 Darwin-only 工具使用 `unsupported_platform`；静态关闭使用
   `runtime_unavailable`。后续只有在具体工具声明依赖/配置探针时才增加新的封闭
   reason code，不预先虚构依赖目录。
4. 探测必须无副作用，不能为了列表查询触发 TCC 权限弹窗或外部动作。
5. dispatch 前使用同一能力函数二次检查，避免缓存后的 TOCTOU。
6. `reason_code` 不包含本机路径、账号、密钥或用户数据。

### Task 5：让 Companion 与 Hermes 都过滤不可用工具

**Files:**

- Modify: `edge/tool-bridge/src/catfish_tool_bridge/mcp_server.py`
- Modify: `edge/tool-bridge/tests/test_mcp_server_filter.py`
- Modify: `edge/companion-app/src/hooks/chat/toolsCache.test.ts`
- Modify: `edge/companion-app/src/lib/tauri_services.ts`
- Modify: `edge/companion-app/src-tauri/src/commands/tool_bridge.rs`

**Steps:**

1. MCP server 在构造 MCP `Tool` 前过滤 `available=false`，保证 Hermes/Advisor 看不到终端不可用工具。
2. Companion 普通聊天继续在 tools cache 中过滤不可用工具，并补 Windows/macOS 测试。
3. `reason_code` 只进入本机服务状态和诊断，不进入 LLM prompt。
4. Rust/TypeScript 新字段使用 optional/default，保证新旧 Tool Bridge 与 Companion 可滚动升级。
5. 发布或本地联调时重启 Tool Bridge 并让 Hermes 重连；只清 Companion 60 秒缓存不视为完成。

### Task 6：修复 Gateway named tool choice 越权

**Files:**

- Modify: `central/llm-gateway/src/catfish_gateway/tools_sanitizer.py`（薄接线）
- Create: `central/llm-gateway/src/catfish_gateway/tool_choice_policy.py`
- Modify: `central/llm-gateway/tests/test_source_tool_profile.py`
- Modify: `central/llm-gateway/tests/test_tools_sanitizer.py`

**Steps:**

1. 删除“caller 点名就保留任意工具”的通用 bypass。
2. `companion-advisor` 点名 `execute_code`、`write_file` 等非许可工具时返回 400。
3. `companion-advisor-transform` 只允许点名 `submit_advisor_result`。
4. source profile、native profile 和 structured-output 许可关系继续是代码级安全规则，不进入数据库或管理页面。
5. deferred bridge 可达性保持现有行为并补独立安全测试，不通过中央目录授权。

### Task 7：跨模型、跨终端联调

**Verification matrix:**

| 场景 | 预期 |
|---|---|
| 同一 Advisor 输入 + Qwen | 能调用允许的业务工具并返回有效结果或明确失败，不生成占位缓存 |
| 同一 Advisor 输入 + DeepSeek | 与 Qwen 使用同一调用合同，无模型名特判 |
| 同一模型用于普通聊天和 transform | 场景 temperature 不互相污染 |
| 模型 max output 小于 6000 | Gateway 按中央模型配置裁剪 |
| macOS + Reminders 可用 | 工具在 Companion 和 Hermes 中可见 |
| Windows | Reminders/Calendar 不进入任何 LLM tools |
| 依赖或 adapter 缺失 | 工具不可见，dispatch 二次检查仍稳定拒绝 |
| 终端没有某工具 | Gateway 不补齐、不注入 |
| RBAC/source 不允许 | Gateway 从请求已有工具中删除 |
| Advisor 点名执行工具 | 400 拒绝 |
| Transform 点名结构化工具 | 保留并强制结构化输出 |

**Commands:**

```bash
cd /Users/chenhongbo/person_task/catfish

edge/tool-bridge/venv/bin/pytest -q \
  edge/tool-bridge/tests/test_tool_availability.py \
  edge/tool-bridge/tests/test_mcp_server_filter.py

central/llm-gateway/venv/bin/pytest -q \
  central/llm-gateway/tests/test_litellm_params_max_tokens.py \
  central/llm-gateway/tests/test_source_tool_profile.py \
  central/llm-gateway/tests/test_thinking_guard.py \
  central/llm-gateway/tests/test_tools_sanitizer.py

npm --prefix edge/companion-app test -- --run
npm --prefix edge/companion-app run build

bash scripts/check_file_sizes.sh --strict
git diff --check
```

---

## 三、不实施的内容

1. 不新增 source policy 数据库、migration、API 或管理页。
2. 不把 Advisor 质量规则做成数据库 JSON、正则或自由文本策略。
3. 不把场景级 temperature/max tokens 塞进模型全局 `param_overrides`。
4. 不建设中央 Catfish native/Hermes builtin 工具目录。
5. 不扩展 MCP Registry 管理终端原生工具。
6. 不修改 Hermes 上游源码。
7. 不按 Qwen、DeepSeek 名称写 Advisor 特判。
8. 不上报包含用户内容的终端能力或质量判定数据。

---

## 最终验收标准

- 中央数据库仍只有现有模型/供应商配置，没有新增 Advisor source policy。
- Advisor 调用语义集中在 Companion 的版本化请求合同中，两个调用阶段边界清楚。
- 模型协议差异只由中央模型配置和 Gateway 适配层处理。
- Advisor 质量门继续在本地根据本轮输入执行，失败结果不能污染缓存。
- Windows 不再向模型暴露 macOS-only 工具，macOS 可用能力不退化。
- Companion 和 Hermes 两条工具消费链都只接收终端真实可用工具。
- Gateway 只做 RBAC/source 安全减法，不从中央配置注入工具。
- MCP Registry 继续只承担组织共享连接器的发布、过滤和订阅。
- named `tool_choice` 不能绕过 source 安全边界。
- 所有新增/修改源文件低于 800 行，定向测试、构建和严格文件检查全部通过。
