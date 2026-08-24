# Advisor Qwen Empty Output Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 阻止早安参谋把 Qwen 的空转回复转换成虚构待办，并让 Qwen 与 DeepSeek 在相同输入下都只能返回有业务依据的结果。

**Architecture:** 保持 Hermes 上游不变。中央网关按 `X-Catfish-Source=companion-advisor` 只注入 Catfish 明确允许的只读业务工具；Companion 在 Call 1 与结构化 Call 2 之间增加语义质量门，并在最终结果写缓存前再次验证任务是否能在本次输入中找到依据。无效结果返回 `null`，UI 优先显示旧的有效缓存。

**Tech Stack:** TypeScript/Vitest、Python/Pytest、Catfish LLM Gateway、Catfish Hermes 插件与 Tool Bridge。

---

### Task 1: 固化 Qwen 空转回归样本

**Files:**
- Create: `edge/companion-app/src/lib/briefing_advisor_quality.test.ts`
- Create: `edge/companion-app/src/lib/briefing_advisor_quality.ts`

**Steps:**
1. 用线上原文 `系统提示已接收。你说吧，需要我做什么？` 编写失败用例。
2. 编写有具体待办依据的正常文本和结构化结果用例。
3. 实现纯函数质量门，拒绝通用占位语、无输入依据的任务和不合理空结果。

### Task 2: 接入参谋两阶段输出链路

**Files:**
- Modify: `edge/companion-app/src/lib/briefing_advisor.ts`

**Steps:**
1. Call 1 无法解析时，先验证原文是否包含具体业务信息，再决定是否执行 Call 2。
2. 修改 Call 2 提示词，明确禁止补造业务事实和“等待用户输入”类占位任务。
3. 对 Call 1/Call 2 最终 `AdvisorResult` 做输入依据验证，失败返回 `null`。

### Task 3: 收紧 advisor 工具注入

**Files:**
- Modify: `central/llm-gateway/src/catfish_gateway/tools_sanitizer.py`
- Modify: `central/llm-gateway/src/catfish_gateway/tools_sanitizer_constants.py`
- Modify: `central/llm-gateway/tests/test_source_tool_profile.py`

**Steps:**
1. 对已知后台 source 取消 Catfish `always-on` 绕过，严格使用 source 显式白名单。
2. 将 `companion-advisor` 的原生工具白名单设为空，移除 browser/tool bridge/web 偏航入口。
3. 回归确认普通聊天及员工自装 MCP 不受影响。

### Task 4: 保护有效缓存

**Files:**
- Modify: `edge/companion-app/src/tabs/Briefing/AdvisorView.tsx`

**Steps:**
1. advisor 返回 `null` 时读取 stale cache。
2. 有旧结果则显示旧结果和清晰提示；无旧结果才进入错误态。
3. 无效结果不得写入缓存。

### Task 5: 验证

**Files:**
- Test: `edge/companion-app/src/lib/briefing_advisor_quality.test.ts`
- Test: `central/llm-gateway/tests/test_source_tool_profile.py`
- Test: `edge/hermes-plugins/catfish-xcatfish-user/tests/test_p43_core_tools.py`

**Steps:**
1. 运行定向 Vitest、Pytest。
2. 运行 Companion 构建。
3. 运行 `bash scripts/check_file_sizes.sh --strict`。
4. 重启本地 gateway，以 Qwen 刷新早安模块，核对无 browser/tool bridge 偏航、无占位任务、缓存不被污染。
