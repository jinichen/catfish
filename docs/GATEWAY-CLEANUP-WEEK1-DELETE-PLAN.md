# Week 1 删除清单 — gateway 减债 (BL-GATEWAY-CLEANUP-POST-HERMES)

**生成时间**: 2026-05-19 (今天的 cutover 收尾)
**执行时间**: 明天清醒手动执行 (今天累 + 已上头, 不动代码)
**作者**: Claude (今天) 给 明天清醒的人 / 下一个 Claude 看
**前置完成**: hermes cutover 已完成 today, gateway 退化为 LLM 代理.

---

## 背景 (200 字)

今天 hermes cutover 后 gateway 退化为 LLM 代理. hermes-agent 自管 agent loop
(`run_agent.py:12167 run_conversation` + `IterationBudget(max_iterations=90)` +
`while api_call_count < self.max_iterations` 主循环, 实测 line 12614).

gateway 当 agent runtime 时代加的两块**LLM 行为塑造层**, 现在 hermes 接管, 双
重 prompt 注入只会撞钟摆:

1. **`compound_intent.py`** (~218 LOC) — 复合任务检测 + plan-execute prompt 块注入
2. **`self_critique.py`** (~456 LOC) — "幻觉完成" 检测 + "plan-then-stop" 检测 + hint 注入

合计 ~674 LOC. 删完 gateway chat_completions 减 ~5% 复杂度.

---

## 删什么 (精确 LOC + 调用方)

### 1. `compound_intent.py` (218 LOC, 全删)

**路径**: `/Users/chenhongbo/person_task/catfish/central/llm-gateway/src/catfish_gateway/compound_intent.py`

**函数清单**:
- `has_compound_intent(messages)` (line 67-81) — 检测复合任务 (连接词 + ≥ 2 动作动词)
- `inject_compound_plan_execute(messages, model_name=None)` (line 145-190) — 注入 plan-execute prompt 块
- `_last_user_text(messages)` (line 196-211) — helper, 取最后 user text
- 模块级常量: `_CONNECTORS` / `_ACTION_VERBS` / `_PLAN_EXECUTE_MARKER` / `_PLAN_EXECUTE_BLOCK`

**调用方** (gateway 内):
- `src/catfish_gateway/app.py:2581` — `from .compound_intent import inject_compound_plan_execute`
- `src/catfish_gateway/app.py:2585-2587` — `body["messages"] = inject_compound_plan_execute(body["messages"], model_name=...)`
- 调用包在 `try/except` 内, 失败只 log.debug, 不阻塞 — 删起来安全.

**测试**:
- `tests/test_compound_intent.py` — 应整文件一起删 (单测覆盖的就是被删的函数)
- `tests/test_lean_inject.py` — Grep 命中过 "compound_intent" 字符串, 需要确认是否真依赖. 看一眼如果只是注释就保留, 真有 import 则同改.

---

### 2. `self_critique.py` (456 LOC, 全删)

**路径**: `/Users/chenhongbo/person_task/catfish/central/llm-gateway/src/catfish_gateway/self_critique.py`

**函数清单**:
- `_has_completion_promise(content)` (line 221-246) — 检测"已完成 / 已生成 / 已保存"承诺
- `_has_productive_tool_call_recent(messages, depth)` (line 249-272) — 看历史有没有真 tool_call
- `_has_plan_intent(content)` (line 275-306) — 检测 plan 文本 (JSON plan / "step 1" / "我将" / "开始执行")
- `has_existing_hint(messages)` (line 309-322) — 完成承诺 hint 幂等检查
- `has_existing_plan_hint(messages)` (line 325-335) — plan hint 幂等检查
- `inject_completion_critique_hint(messages)` (line 338-394) — 注入 BL-A1.3 "幻觉完成" hint
- `inject_plan_then_stop_hint(messages)` (line 397-444) — 注入 BL-LLM-PLAN-WITHOUT-ACT hint
- `inject_self_critique(messages)` (line 447-455) — 聚合入口

**调用方** (gateway 内):
- `src/catfish_gateway/app.py:2635` — `from . import self_critique`
- `src/catfish_gateway/app.py:2636` — `body["messages"] = self_critique.inject_self_critique(body["messages"])`
- 在 `if not is_internal_call and not _lean and not _hints_disabled:` 内 (line 2606)

**测试**:
- `tests/test_self_critique.py` — 整文件一起删

---

## 同步删除 app.py 调用点

### compound_intent 块 (app.py:2574-2589)

删整段:

```python
# BL-COMPOUND-PLAN-EXECUTE (5/15 鸿波 '复合任务 agent 撑不住'): 复合任务
# ('分析 + 生成 PPT') 检测命中 → 追加 plan-execute 铁律到同一段 system,
# ...
if not _lean:
    try:
        from .compound_intent import inject_compound_plan_execute  # noqa: PLC0415

        # BL-LLM-PLAN-WITHOUT-ACT (5/19): 把 model.name 传进去, 内网 qwen
        # 命中 → 额外注入 "立即 act, 不许 plan" 铁律, 解决"只说不做" bug.
        body["messages"] = inject_compound_plan_execute(
            body["messages"], model_name=getattr(model, "name", None),
        )
    except Exception as e:  # noqa: BLE001
        logger.debug("compound_intent 注入失败 (%s), 不阻塞", e)
```

替换为单行注释:

```python
# BL-GATEWAY-CLEANUP-POST-HERMES (5/20 删): compound_intent.py 已删,
# hermes-agent 自管 plan-execute (run_agent.py:12614 agent loop).
```

### self_critique 块 (app.py:2630-2636)

删:

```python
# BL-A1.3: "幻觉完成" hint + BL-LLM-PLAN-WITHOUT-ACT plan-then-stop guard
# 5/19: 不再加 prompt 铁律 (qwen 钟摆已两轮翻车), 改 agent loop guard 兜底.
# inject_self_critique 聚合两条 detect:
#   - 完成承诺 ("已生成 X" 但没 tool_call) — 老路径
#   - plan-then-stop (JSON plan / "step 1 / 我将" 但没 tool_call) — 新增
from . import self_critique  # noqa: PLC0415  lazy import
body["messages"] = self_critique.inject_self_critique(body["messages"])
```

替换为单行注释:

```python
# BL-GATEWAY-CLEANUP-POST-HERMES (5/20 删): self_critique.py 已删,
# 完成承诺 / plan-then-stop 检测由 hermes-agent agent loop 兜底
# (hermes 自己跑 max_iterations=90 + tool_retry, 不再需要 gateway 注入 hint).
```

### app.py line 2531 注释 (可选清理)

```python
# 注: identity (创建 system) / compound_intent / session_goal / hints / session_meta tick
```

把 `compound_intent` 从 list 里删掉. 不删也不影响功能, 只是文档冗余.

---

## 删除步骤 (明天清醒手动执行)

### 步骤 0: 安全 — 打 git tag 备份

```bash
cd /Users/chenhongbo/person_task/catfish
git tag gateway-pre-cleanup-week1
git tag -l gateway-pre-cleanup-week1  # 确认成功
```

万一退化, `git reset --hard gateway-pre-cleanup-week1` 一键回退.

### 步骤 1: 跑 baseline 测试 (5/5 应全过)

```bash
cd /Users/chenhongbo/person_task/catfish/central/llm-gateway
python3 -m pytest tests/test_gateway_chat_integration.py -v
```

预期: `5 passed`. 如果有失败先停, 调查 — 删之前应该绿.

### 步骤 2: 录实盘 baseline (Companion 端到端)

打开 Companion (或 hermes-cli) 跑 5 个 scenario, 把响应录到文件:

```bash
# 在 Companion 里依次发送, 然后把响应贴到这里
# (或 hermes-cli companion 发 5 条 -> tee /tmp/baseline-week1-before.txt)
mkdir -p /tmp/cleanup-week1
```

5 个 scenario:
1. "帮我写本周周报"
2. "分析 ~/data/sales.csv 然后生成 PPT" (如果没文件就用任何 csv)
3. "读取 ~/docs/notes.txt 然后写出 markdown 版本"
4. "查一下王主任最近邮件" (邮件 skill 可用时)
5. "你好"

把每条响应的 finish_reason / tool_calls 序列 / 最终 content 录到
`/tmp/cleanup-week1/baseline-before.txt`.

**重点观察**: qwen 是否还有 plan-then-stop 行为. 如果有, 说明 hermes prompt 还
没完全接管, 这时**停**, 别删, Week 1 改成"先把 hermes prompt 调到位".

### 步骤 3: 执行删除

```bash
cd /Users/chenhongbo/person_task/catfish/central/llm-gateway

# 删模块
rm src/catfish_gateway/compound_intent.py
rm src/catfish_gateway/self_critique.py

# 删单测
rm tests/test_compound_intent.py
rm tests/test_self_critique.py

# 改 app.py — 用 sed 或手动编辑 (推荐手动, 这种关键文件 sed 风险大)
# 删 line 2574-2589 (compound_intent 块) 和 line 2630-2636 (self_critique 块)
# 替换为单行注释 (见上面"同步删除"段)
```

### 步骤 4: 再跑测试

```bash
python3 -m pytest tests/test_gateway_chat_integration.py -v
```

期望仍 5/5 过 — 我们的 5 个 integration test 用 mock LLM, 跟注入层无强耦合,
注入层删了不影响 mock 行为. 如果挂了, 大概率是 import 错误或漏了同步注释引用,
checkout 回 tag 重来.

```bash
# 跑整个 test suite 看有没有其它隐性依赖
python3 -m pytest tests/ -v --tb=short 2>&1 | tail -40
```

### 步骤 5: 重启 gateway

```bash
# launchd 重启 gateway (具体命令看你的 launchd 配置)
launchctl unload ~/Library/LaunchAgents/catfish-gateway.plist
launchctl load ~/Library/LaunchAgents/catfish-gateway.plist
sleep 3
curl -s http://localhost:8080/healthz  # 健康检查
```

### 步骤 6: 录实盘 after

发同样 5 个 scenario, 录到 `/tmp/cleanup-week1/baseline-after.txt`.

对比 before vs after:

```bash
diff /tmp/cleanup-week1/baseline-before.txt /tmp/cleanup-week1/baseline-after.txt
```

**判定**:
- **行为一致 / 更好**: commit, 推 origin
- **退化 (plan-then-stop / 死循环 / 复合任务挂)**: `git reset --hard gateway-pre-cleanup-week1` + 重启 gateway, 退回原状, 写复盘到 BACKLOG.

### 步骤 7: commit

```bash
git add -A
git commit -m "BL-GATEWAY-CLEANUP-POST-HERMES Week 1: 删 compound_intent + self_critique (~674 LOC)

hermes 自管 agent loop (run_agent.py:12614 run_conversation + IterationBudget),
gateway 不再需要 LLM 行为塑造层. 5 integration test (tests/test_gateway_chat_integration.py)
做 baseline 锚定, 实盘 5 scenario 行为一致.

- 删: compound_intent.py (218 LOC) + self_critique.py (456 LOC)
- 删: tests/test_compound_intent.py + tests/test_self_critique.py
- 改 app.py:2574-2589 / 2630-2636: 移除 import + 调用

rollback: git reset --hard gateway-pre-cleanup-week1
"
```

---

## 风险 (诚实评估)

### 风险 1: qwen 回到 plan-then-stop 死循环 (高)

**原**: self_critique.inject_plan_then_stop_hint 检测到 "我将 / step 1 / JSON plan"
但没 tool_call → 注入 user hint 强制 LLM 真调 tool. 删了之后兜底没了.

**缓解**: hermes 自带 `max_iterations=90` 上限 + tool_retry_hint, 死循环会被自然
止血. 但 qwen 学到的 "plan-then-stop = final answer" 行为可能复发.

**判定标志**: scenario 2 (复合 CSV + PPT) — 看 qwen 第一轮是不是输出 JSON plan
然后 stop 不发 tool_call. 如果是, 立即 rollback.

### 风险 2: 复合任务挂 (中)

**原**: compound_intent 注入了一段约 1.5K char 的 plan-execute prompt 块, 教 qwen
"第一轮列 plan 第二轮起执行". 删了之后 qwen 看不到这套铁律.

**缓解**: hermes 的 system prompt + agent loop 本身就是 plan-execute 范式, qwen
不靠 gateway 注入也能分步执行. 真实风险是 qwen 122B 在 ReAct 上的能力问题,
跟 gateway prompt 关系不大 (hermes prompt 完全可以接).

**判定标志**: scenario 2 / 3 — 看 qwen 是否一轮发多个 tool_call 或者乱拼.

### 风险 3: BL-FIX8 完成承诺检测失效 (低)

**原**: self_critique.inject_completion_critique_hint 检测 "已生成 X" + 无 tool_call
= 文字幻觉, 注入 hint 强制 LLM 真做. 删了之后 LLM 谎报完成不会被拦.

**缓解**:
- hermes 也有 `_handle_max_iterations` + curator review 兜底 (hermes_state.py)
- 央企客户拿到的是真文件, LLM 说"已完成"但没文件 → 用户自己会发现报 bug
- 4/29 demo 翻车主要原因当时 hermes 还没接管 agent loop, 现在 hermes 主导, 这个
  风险降低 (但不消失)

**判定标志**: scenario 1 (写周报) — 看 qwen 是否说"周报已生成" 但实际没文件.

---

## Rollback 策略

### Plan A: git tag 回滚 (最稳)

```bash
cd /Users/chenhongbo/person_task/catfish
git reset --hard gateway-pre-cleanup-week1
launchctl unload ~/Library/LaunchAgents/catfish-gateway.plist
launchctl load ~/Library/LaunchAgents/catfish-gateway.plist
```

完整回到删之前. 适用所有退化.

### Plan B: 部分回滚 (灵活)

只是 plan-then-stop 复发 → 只恢复 self_critique:

```bash
git checkout gateway-pre-cleanup-week1 -- \
    central/llm-gateway/src/catfish_gateway/self_critique.py \
    central/llm-gateway/tests/test_self_critique.py
# 同时手动恢复 app.py:2630-2636 块
```

compound_intent 仍删 (它更冗余).

### Plan C: 留模块但停调用 (临时止血)

如果 commit 之后才发现退化, 改 `CATFISH_DISABLE_GATEWAY_HINTS=1` 实测一下 —
但本删除已经物理删了模块, env flag 没用了. 必须 git reset.

---

## 不删的东西 (明确边界)

为防 over-eager Claude 把这些也一起删:

- **`tool_retry_hint.py`** (BL-A1.2 + BL-HERMES-AUTO-CONTINUE-LIMIT) — **保留**. 跟
  self_critique 不一样, 这是 hard cap (≥ 5 次同 tool 失败 → 合成 abort 跳过 LLM
  调用), hermes 没接管这层. 删了会重蹈 89 次重试事故.
- **`skill_guard`** — **保留**. 单 skill 意图识别注入, 跟 plan-execute 是两件事.
- **`session_goals.py`** / `inject_session_goal` (app.py:2596) — **保留**. /goal
  锁定目标, 跟 critique 完全两路.
- **Registry inject_unified / inject_subset** (app.py:2566/2571) — **保留**. 8 个
  provider 合并, 是 memory 入口.
- **`compound_intent` 字符串注释** (零散) — 不强求清理. 不影响功能, Week 2/3
  顺手扫.

---

## 备份清单

| 项 | 状态 |
|---|---|
| git tag `gateway-pre-cleanup-week1` | 步骤 0 创建 |
| 5 integration test 锚定 | `tests/test_gateway_chat_integration.py` (已生成) |
| 实盘 baseline 录像 | `/tmp/cleanup-week1/baseline-before.txt` (步骤 2 生成) |
| 实盘 after 录像 | `/tmp/cleanup-week1/baseline-after.txt` (步骤 6 生成) |
| 本删除清单文档 | `/Users/chenhongbo/person_task/catfish/docs/GATEWAY-CLEANUP-WEEK1-DELETE-PLAN.md` |

---

## Done When

- [ ] git tag 打了
- [ ] 5 integration test 删之前过
- [ ] 实盘 baseline 录了
- [ ] 模块 + 单测 + app.py 调用点都删了
- [ ] 5 integration test 删之后还过
- [ ] 整个 pytest suite 还过 (或失败的都跟本次删除无关)
- [ ] gateway 重启健康
- [ ] 实盘 after 录了, 跟 baseline 对比一致 / 更好
- [ ] commit + push

---

## 给明天清醒的人的一句话

**别一口气删完**. 按步骤来, 每步看结果. 实盘 5 个 scenario 录响应是关键 —
mock 测试只能锚定"逻辑流不挂", 真行为退化 (qwen 钟摆) 只有人眼能判定. 退化
立刻 `git reset --hard gateway-pre-cleanup-week1`, 不要硬上.

如果你是下一个 Claude 接手, 先 grep `BL-GATEWAY-CLEANUP-POST-HERMES` 看历史
进展, 别从头猜.
