# Week 2 迁移计划 — gateway memory 写路径搬到 catfish-memory plugin
## (BL-GATEWAY-CLEANUP-POST-HERMES Week 2)

**生成时间**: 2026-05-19 (Week 1 删除已 ship, Week 2 是搬不是删)
**执行时间**: 明天清醒手动执行 (今天累 + 已上头, 不动代码)
**作者**: Claude (今天) 给 明天清醒的人 / 下一个 Claude 看
**前置完成**:
- Week 1 已删 `compound_intent.py` + `self_critique.py` (1330 LOC 计入 backlog 闭口)
- BL-MEMORY-OWNERSHIP-FIX Phase 1+2 已 ship (5/19 凌晨):
  - `catfish-memory` plugin 装好 (`edge/hermes-plugins/catfish-memory/`)
  - `memory/bootstrap.py` 已清空 (gateway 不 register 任何 provider)
  - `CATFISH_GATEWAY_DISABLE_MEMORY` env flag 已上 (kill switch)
- **读路径**: `inject_employee_journal()` 实际已被替代 (catfish-memory plugin `prefetch()` 等价覆盖) — 但 gateway 代码还在, 是死代码
- **写路径**: 还在 gateway `app.py` 两处 `asyncio.create_task(...)`, 没搬

---

## 背景 (Week 2 任务范围)

Week 1 删的是**纯无主代码** (hermes 自管 agent loop 后多余的 prompt 塑造层).

Week 2 搬的是**有责任主, 但放错地方**的代码 — gateway 的 3 个模块本来就该归
edge plugin (`catfish-memory`), 当年加它们是因为没 hermes plugin 没地方放.

3 个目标模块:

| 模块 | LOC | 主要 API | 职责 |
|---|---|---|---|
| `session_summarizer.py` | 527 | `trigger_background_summary()` `summarize_one_session()` | LLM 总结老 session → 写 `~/.catfish/employee_journal.md` |
| `memory_distill.py` | 747 | `maybe_run_llm_distillation()` `maybe_run_distillation()` `read_distilled_facts()` | LLM 蒸馏老 journal → 写 `~/.catfish/distilled_facts.md` |
| `employee_journal.py` | 241 | `read_journal()` `append_to_journal()` `inject_employee_journal()` `journal_path()` | journal 文件读 / 写 / 注入 |
| **合计** | **1515** | | |

读路径 (`inject_employee_journal`) 已经被 catfish-memory plugin 覆盖, 但 gateway
源码还在. 写路径 (`summarize` / `distill` + `append`) 还要搬过去.

---

## Phase 1 调研结论 (caller + plugin 现状)

### 1.1 gateway 这 3 个模块的实际 caller

```
app.py:66    from .employee_journal import inject_employee_journal  # 死 import — bootstrap 没 register, 不调
app.py:80    from .session_summarizer import trigger_background_summary  # 活的 import
app.py:2642  asyncio.create_task(trigger_background_summary())  # ★ 写路径 caller #1 (每次 chat fire-and-forget)
app.py:2660  from .memory_distill import maybe_run_llm_distillation  # 活的 import
app.py:2664  asyncio.create_task(maybe_run_llm_distillation(user_email=...))  # ★ 写路径 caller #2 (每次 chat, 24h cooldown)

a2a_journal_hook.py:116  from .employee_journal import append_to_journal  # 活的 import
a2a_journal_hook.py:143  append_to_journal(entry)  # ★ 写路径 caller #3 (B 端 a2a 答完 A, 加 [a2a-help] 记录)
```

`memory/providers/employee_journal.py` 还存在, 但 `memory/bootstrap.py` 已经
不 register, 所以**整条 inject 路径就是死代码** — 已经被 catfish-memory plugin
`prefetch()` 在 hermes 侧接管.

**read 路径 (注入) 残留**: `inject_employee_journal()` 函数本身 + `memory/providers/employee_journal.py`. Week 2 一起清干净.

### 1.2 catfish-memory plugin 现状 + hermes lifecycle 真支持

`edge/hermes-plugins/catfish-memory/catfish_memory.py`:

| Hook | 状态 | gateway 对应 |
|---|---|---|
| `is_available()` | ✅ 实现 (`~/.catfish/` 存在) | — |
| `initialize(session_id, **kwargs)` | ✅ 实现 (记 session_id + catfish_home) | — |
| `prefetch(query, *, session_id)` | ✅ 实现 5 段 (session_meta + employee_journal + skills_catalog + feedback + skill_guard) | `inject_employee_journal()` 注入路径 (read 部分) |
| `get_tool_schemas()` | ✅ 返 `[]` | — |
| **`on_session_end(messages)`** | ❌ **未实现 (走 ABC no-op)** | `summarize_one_session()` / `trigger_background_summary()` 应该归这 |
| `on_pre_compress(messages)` | ❌ 未实现 (返 "") | 可选 — `memory_distill` 也可挂这 |
| `sync_turn(user, asst)` | ❌ 未实现 | 暂不用 |
| `queue_prefetch(query)` | ❌ 未实现 | 暂不用 |
| `shutdown()` | ✅ 只 log | — |

hermes `memory_provider.py` 真支持的 hook 全列在 `agent/memory_provider.py:142-228`:
- `on_turn_start(turn, message, **kwargs)`
- `on_session_end(messages)` ← **核心**
- `on_session_switch(new_session_id, **kwargs)`
- `on_pre_compress(messages) -> str`
- `on_delegation(task, result, **kwargs)`
- `on_memory_write(action, target, content, metadata)`

memory_manager.py 真在调 (line 392 `on_session_end` / line 438 `on_pre_compress` / line 321 `sync_turn`, 实盘 dispatch).

`run_agent.py:5809, 5816, 5840, 16085` 在 session 真结束时调 `self._memory_manager.on_session_end(messages or [])`.

**holographic plugin** (`plugins/memory/holographic/__init__.py:237`) 就是 reference 例子, 它 `on_session_end` 调 `_auto_extract_facts(messages)` — 同 pattern. 我们要做的就是把 `summarize_one_session` 搬进 `CatfishMemoryProvider.on_session_end()`.

### 1.3 plugin.yaml 状态

`edge/hermes-plugins/catfish-memory/plugin.yaml` 已经声明:
```yaml
hooks:
  - on_session_end
```

但实际代码没实现 — **声明跟实现不一致** (Week 2 顺手修).

---

## Phase 2 迁移设计

### 2.1 数据流变化 (前 → 后)

**前** (今天):
```
员工 chat → Companion → hermes serve /v1/chat/completions
              ↓
       hermes agent loop
              ↓
   inject memory (catfish-memory.prefetch 已干这块 ✓)
              ↓
   调 gateway /v1/chat/completions
              ↓
   ★ gateway app.py asyncio.create_task(trigger_background_summary)
   ★ gateway app.py asyncio.create_task(maybe_run_llm_distillation)
              ↓
        gateway loopback → LLM (summary) → append_to_journal()
        gateway loopback → LLM (distill) → write_distilled_facts()
              ↓
       litellm → 上游 LLM
              ↓
       SSE → hermes → Companion
```

问题:
- gateway 当 LLM 代理时不应该再"知道"员工 session 边界 — 当前只能用"每次 chat fire-and-forget"近似 session 结束, 实际很糙.
- gateway 进程读 `~/.hermes/state.db` + 写 `~/.catfish/employee_journal.md` 违反 BL-CENTRAL-EDGE-BOUNDARY (gateway 应该可以独立部署机房).
- summarize 走 gateway loopback 调 LLM, **summarize 自己调 gateway**, 循环依赖, 鸿波 5/4 凌晨踩过坑加了一堆 cooldown / lock / X-Catfish-Internal 兜底.

**后** (Week 2 ship 后):
```
员工 chat → Companion → hermes serve /v1/chat/completions
              ↓
   hermes agent loop
              ↓
   prefetch: catfish-memory.prefetch() (无变化)
              ↓
   调 gateway /v1/chat/completions → litellm → 上游 → SSE 返
              ↓
   ★ hermes 检测 session 边界 (CLI exit / /reset / gateway session expiry)
              ↓
   ★ hermes memory_manager.on_session_end(messages)
              ↓
   ★ catfish-memory.on_session_end(messages):
        1. 用 hermes 内置 LLM (或 plugin 自带 httpx 调 hermes serve 自身) summarize
        2. append_to_journal()
        3. 如果累 ≥ 30 段 → 跑 distill, 写 distilled_facts.md
```

**关键改善**:
- gateway 不再读 `~/.hermes/state.db` (不再需要 `_list_unjournaled_finished_sessions`)
- session 边界判定从"每次 chat 后扫 state.db 看 message_count" → hermes 真 session end event
- summarize 调 LLM 用 hermes 自己的 LLM 调度 (它本来就在 agent loop 里有 LLM access), 不再 gateway loopback
- a2a journal append (跨员工协作记录) 改为: gateway 通过 hermes 一个 admin endpoint 通知, 或者 gateway 把要 append 的内容塞到 chat completions response metadata, hermes 接住后写. **(open question — 简单做法: a2a 这条 caller 暂保留, Week 3 处理)**

### 2.2 函数迁移映射 (1-for-1)

| gateway 函数 | catfish-memory plugin 新位置 | 说明 |
|---|---|---|
| `session_summarizer.summarize_one_session()` | `CatfishMemoryProvider._summarize_session(messages)` (新) | 入参从 `(session_id, started_at, msg_count)` 改成 `messages` (hermes 直接传完整对话历史) |
| `session_summarizer.trigger_background_summary()` | **删** | hermes `on_session_end` 自动触发, 不需要"扫 state.db 找未总结的"逻辑 |
| `session_summarizer._read_session_messages()` | **删** | 不再读 state.db, messages 由 hermes 传入 |
| `session_summarizer._read_session_model()` | **删** | hermes 自己知道当前 session model |
| `session_summarizer._update_session_title()` | 暂保留 in gateway (state.db 写回是 hermes 自己 schema, plugin 不该跨进程改) — **或** hermes 0.13+ 有 session_title API 调那个 | open question |
| `session_summarizer.JOURNALED_MARKER` (去重标记) | **删** | hermes `on_session_end` 不会重复触发同一 session, 不需要再标记 |
| `session_summarizer._SESSION_COOL_DOWN` / `_CURRENTLY_SUMMARIZING` (并发锁) | **删** | hermes 一个 session 只触发一次, 无需 race protection |
| `memory_distill.maybe_run_llm_distillation()` | `CatfishMemoryProvider._maybe_distill()` (新) — 在 `on_session_end` 末尾按计数 / 24h 间隔触发 | 入参从 `user_email` 改成自带 (plugin 知道 `CATFISH_HOME`) |
| `memory_distill._llm_distill_chunk()` | `CatfishMemoryProvider._llm_distill_chunk()` — 但调 hermes-internal LLM 不调 gateway loopback | 改 LLM 调用入口 |
| `memory_distill.read_distilled_facts()` | **保留**, 移到 plugin (plugin 自己 prefetch 时读) | 已经有等价 (`_render_employee_journal` 已经读 distilled) |
| `memory_distill._split_journal_chunks()` / `_cross_chunk_dedup()` / `_dedup_distilled_lines()` | 整个搬, 纯函数 | — |
| `memory_distill.should_run_distillation()` / `mark_distillation_run()` | 搬 (state path 改 `~/.catfish/memory_distill_state.json` 不变) | — |
| `memory_distill.distill_journal_to_traits()` (规则版) | **删** — MVP 规则版没真上线, 跟 BL-MM7 confirm 流程没接 | 6/15 PoC 时再决定要不要重起 |
| `memory_distill.maybe_run_distillation()` (规则版入口) | **删** | 同上 |
| `employee_journal.append_to_journal()` | `CatfishMemoryProvider._append_to_journal()` (内部) + 给 a2a_journal_hook 留 stable shim | shim: gateway 这边保留 `append_to_journal()` 但只 noop (a2a 路径暂保留, Week 3 设计 hermes 写入通道) |
| `employee_journal.read_journal()` | `CatfishMemoryProvider._read_journal()` (内部) | plugin 已经在 `_render_employee_journal` 实现等价逻辑 |
| `employee_journal.inject_employee_journal()` | **删** (整个函数 + caller — gateway app.py 那行 noqa import 也删) | catfish-memory plugin `prefetch` 已等价 |
| `employee_journal.JOURNAL_PATH` / `journal_path()` | 整合到 plugin `_catfish_home() / "employee_journal.md"` | plugin 已经在用 |
| `employee_journal.INJECT_MAX_BYTES` / `MAX_FILE_BYTES` | 搬到 plugin 模块常量 | plugin `_BUDGETS["employee_journal"] = 5000` 已经等价 |

### 2.3 步骤 (按风险升序, 每步 commit, 撞 bug 单步回滚)

#### Step A (零风险 / 准备) — 写 baseline test
1. 写 `tests/test_memory_features_baseline.py` (Phase 3 产物, 见下)
2. 跑 → 全 pass (lock 当前行为)
3. commit + tag `bl-gateway-cleanup-week2-baseline`

#### Step B (低风险) — plugin `on_session_end` 实现
1. 在 `edge/hermes-plugins/catfish-memory/catfish_memory.py` 加:
   - `_SESSION_SUMMARIZER_PROMPT` (复制 gateway `_SUMMARY_PROMPT`)
   - `_summarize_messages(messages, *, model_name) -> str | None` (复制 gateway `_summarize_with_llm` 但用 plugin 自己的 httpx 调 `http://localhost:8642/v1/chat/completions` — hermes 自己的 OpenAI 端口)
   - `_append_to_journal(entry, *, catfish_home)` (复制 gateway 同名)
   - `_extract_short_title(summary)` (复制 gateway 同名)
   - 改写 `on_session_end(messages)`:
     - 跳过 `len(messages) < MIN_MESSAGES_TO_SUMMARIZE`
     - 跑 `_summarize_messages(messages, model_name=self._last_model_name)`
     - `_append_to_journal(...)`
     - (option) `_maybe_distill_after_summary()`
2. 加单测 `tests/test_on_session_end.py` (plugin tests/)
3. **不动 gateway 代码** — Step C 才动
4. commit + 实盘装 plugin (`install-catfish-memory.sh`) — 看 hermes restart 后 plugin status active

#### Step C (中风险) — gateway 删 summarize caller, 双跑兼容期开关
1. `app.py` 改:
   ```python
   # 5/20 BL-GATEWAY-CLEANUP-POST-HERMES Week 2: summarize/distill 已搬到
   # catfish-memory plugin on_session_end. gateway 不再 fire-and-forget.
   # env CATFISH_GATEWAY_LEGACY_SUMMARIZE=1 一键回滚 (兜底).
   if os.environ.get("CATFISH_GATEWAY_LEGACY_SUMMARIZE", "0") == "1":
       try:
           import asyncio
           asyncio.create_task(trigger_background_summary())
       except Exception:
           pass
       if not is_internal_call:
           try:
               import asyncio
               from .memory_distill import maybe_run_llm_distillation
               asyncio.create_task(
                   maybe_run_llm_distillation(user_email=effective_user_email),
               )
           except Exception:
               pass
   ```
2. **不删模块**, 只在 caller 加 env gate, 默认 OFF (走 plugin), 撞 bug 立 `CATFISH_GATEWAY_LEGACY_SUMMARIZE=1` 回退
3. 实盘观察 24-72h:
   - `~/.catfish/employee_journal.md` 真的有新条目 ↘ plugin work
   - `~/.catfish/distilled_facts.md` 24h 后真更新 ↘ plugin distill work
   - gateway log 不再有 `summarize_one_session` 行 ↘ caller 真被 gate 关掉
4. commit + tag `bl-gateway-cleanup-week2-cutover`

#### Step D (中风险) — gateway 模块标 deprecated + 整文件改 noqa
1. `session_summarizer.py` / `memory_distill.py` / `employee_journal.py`
   开头加大 deprecation header:
   ```
   """DEPRECATED 2026-05-20 — 已搬到 edge/hermes-plugins/catfish-memory/
   通过 catfish-memory plugin on_session_end / prefetch 接管.
   实施回滚: CATFISH_GATEWAY_LEGACY_SUMMARIZE=1.
   全删时机: 2026-06-01 (双跑稳定 ≥ 10 天后) — 等鸿波拍.
   """
   ```
2. **不删函数**, 让 a2a_journal_hook.py 这种独立 caller 继续 work
3. lint 加 `scripts/check-gateway-no-edge-memory.sh` (warning 模式) 防新 import
4. commit

#### Step E (高风险, 留 Week 3) — hard delete
1. 删 `session_summarizer.py` (527 LOC)
2. 删 `memory_distill.py` (747 LOC, 但 `read_distilled_facts` 已经被 `memory/providers/employee_journal.py` import — 一起删 provider 文件 134 LOC)
3. 删 `employee_journal.py` (241 LOC), 同时改 `a2a_journal_hook.py` 写直接走 `Path.home() / ".catfish" / "employee_journal.md"` (~5 行 inline, 不依赖模块) 或调 hermes 一个 admin endpoint
4. 删 `memory/providers/employee_journal.py` + bootstrap 死 import
5. 删 `app.py:66 from .employee_journal import inject_employee_journal` (死 import)
6. 删 `app.py:80 from .session_summarizer import trigger_background_summary`
7. 删 `tests/test_session_history.py` 里 `test_inject_employee_journal` 等死测试 (5 个)
8. 删 `tests/test_memory_distill.py` + `tests/test_memory_distill_live.py` (前者纯规则版, 后者搬到 plugin tests 已经覆盖)
9. **合计预估**: ~1700 LOC (3 主模块 + provider + 测试)

### 2.4 兼容期策略 (双跑 vs 直接切)

**选**: **双跑兼容期 + env gate**, 不直接删.

理由:
1. Week 1 deletion 是纯无主代码, 删了行为不变. Week 2 搬的是**真有行为**的代码 — summarize / distill 是员工长期记忆基础, 切错员工 6 个月的 journal 接不上.
2. plugin 写路径**还没在实盘验证过** (`on_session_end` 是 ABC no-op), Step C 是真上线第一次跑.
3. env gate (`CATFISH_GATEWAY_LEGACY_SUMMARIZE=1`) 让员工 / 鸿波撞 bug 时立刻**不重启代码**就能回退, 5 秒响应.

**双跑期间**:
- ❌ **不让两边同时写** — gate 是互斥的, 要么 plugin 写, 要么 gateway 写
- ✅ 让员工**可以一键回退**到 gateway 写 (worst case 失败模式: plugin 完全不 fire, 那员工 journal 不再有新条目, gate 切 ON 立马回血)

**双跑窗口**: Step C 实盘后 ≥ 10 天, Step E 才 hard delete (≥ 2026-06-01).

### 2.5 风险 + rollback (照 Week 1 套路)

| 风险 | 可能性 | 影响 | rollback |
|---|---|---|---|
| `on_session_end` 不 fire (hermes session 真没结束) | 中 | journal 不再有新条目 | `CATFISH_GATEWAY_LEGACY_SUMMARIZE=1` → 重启 gateway, 老 fire-and-forget 兜底 |
| plugin 调 hermes 自身 LLM 撞 401 (auth 没对) | 中 | summarize 失败, 但 journal 不会损坏 (failure path 已经在 gateway 里跑了, 已知 robust) | 同上 gate, 或 plugin 改回调 gateway loopback (退化但能 work) |
| plugin LLM 调死循环 (跟 5/4 quota 死循环同 bug) | 低 | LLM token 烧光 + summarize spam | plugin 自带跟 gateway 同 5min cooldown 机制 (Step B 一起搬过来) |
| distill 写到错 path (员工本机有多 catfish_home) | 低 | distilled_facts.md 错位置, 不影响 journal | plugin 已经走 `CATFISH_HOME` env, 跟 gateway 老逻辑同一 path 计算 |
| a2a_journal_hook.py append_to_journal 删了直接 break | 中 | a2a 跨员工记录丢 | Step D 不删函数, 只标 deprecated; Step E 时 a2a 走 inline 写 (~5 行) |
| baseline integration test 跟 plugin 跑出不同结果 | 中 (这就是测试目的) | 说明迁移**不等价**, 需要 debug | 不切 gate, 留 gateway 写, 排查 plugin 行为差距 |

**git tag** (跟 Week 1 同套路):
- Step A 完成 → `bl-gateway-cleanup-week2-baseline`
- Step C 完成 → `bl-gateway-cleanup-week2-cutover` (实盘观察起点)
- Step E 完成 → `bl-gateway-cleanup-week2-delete` (最终, 鸿波拍才上)

如果撞墙 git tag 之间随时可以 `git checkout <tag>` + 重启服务回血.

---

## Phase 3 baseline integration test (已写)

`/Users/chenhongbo/person_task/catfish/central/llm-gateway/tests/test_memory_features_baseline.py`

详见该文件 module docstring. 测试 lock 当前 gateway 行为 (Step A 完成),
迁移到 plugin (Step C-E) 后**同一份 test 跑 plugin 应该仍 pass** —
不 pass 说明迁移不等价.

测试覆盖:
1. `test_baseline_session_summary_writes_journal` — mock 1 个 chat session, assert `~/.catfish/employee_journal.md` 真有新条目, 含 `## YYYY-MM-DD HH:MM` 日期 + `### 主题` + 正文
2. `test_baseline_inject_employee_journal_loads_distilled` — 预置 `distilled_facts.md`, assert `inject_employee_journal()` (或 plugin `prefetch()`) 真把 distilled 注入到 system message 末尾
3. `test_baseline_inject_employee_journal_empty_noop` — 空 journal + 空 distilled, assert 不注入 (跟 BL-EMPLOYEE-JOURNAL-EMPTY-NOOP 配套)
4. `test_baseline_memory_distill_writes_distilled_facts` — 预置 30+ 条 journal, mock LLM, assert distill 跑完 `distilled_facts.md` 真有 LLM bullet
5. `test_baseline_append_to_journal_idempotent_format` — append 1 段, assert 文件追加格式 (`\n\n` 分隔, `## ` 段头)

测试全 mock LLM (httpx), 不实盘, 不依赖网络.

---

## Phase 4 明天执行步骤 (照单点)

**绝对前提** (闭环):
- [ ] 跑 `git status` 干净, 不在脏树上动
- [ ] 跑 `pytest tests/test_memory_features_baseline.py -v` 全 pass (lock 当前行为)
- [ ] 备份 `~/.catfish/employee_journal.md` + `~/.catfish/distilled_facts.md` 到 `~/.catfish/backup/2026-05-20/`
- [ ] git tag `bl-gateway-cleanup-week2-prep-end`

**主流程** (按 2.3 Step B-D 顺序):
1. **Step B** — 改 `catfish_memory.py` 加 summarize 实现 (~2-3 小时)
2. **Step B-test** — `cd edge/hermes-plugins/catfish-memory && pytest -v` 全 pass
3. **Step B-install** — 跑 `edge/hermes-plugins/install-catfish-memory.sh` 重装
4. **Step B-restart** — `hermes-cli restart` 或 launchd 重启 → `hermes memory status` 看 catfish-memory active
5. **Step C** — 改 `app.py` 加 env gate
6. **Step C-test** — `cd central/llm-gateway && pytest tests/ -x -k 'not slow'`
7. **Step C-deploy** — `launchctl unload/load gateway plist`, 实盘聊 1-2 句
8. **Step C-observe** — 24h 后看:
   - `~/.catfish/employee_journal.md` tail (有新条目 ✓)
   - `gateway.log` grep `summarize_one_session` (应该没有了)
   - `~/.hermes/logs/agent.log` grep `catfish-memory on_session_end` (有)
9. **Step D** — 加 deprecation header, 不删函数 (~30 分钟)
10. **Step D-tag** — `git tag bl-gateway-cleanup-week2-cutover`
11. ✅ Week 2 ship 完, **不做 Step E (hard delete)** — 留 ≥ 10 天观察, 下次 sprint 鸿波拍才删

**Step E hard delete 触发条件**:
- 双跑 ≥ 10 天无回滚事件 (gate 没被切 ON 过)
- 鸿波 explicit "可以删了"
- 那次 sprint 单独排, 不在 Week 2 范围

---

## 附录: 测试 / lint 命令

```bash
# 跑 baseline test (Step A 验证)
cd /Users/chenhongbo/person_task/catfish/central/llm-gateway
python -m pytest tests/test_memory_features_baseline.py -v

# 跑 plugin test (Step B 验证)
cd /Users/chenhongbo/person_task/catfish/edge/hermes-plugins/catfish-memory
python -m pytest -v

# 跑 gateway 全测 (Step C 前)
cd /Users/chenhongbo/person_task/catfish/central/llm-gateway
python -m pytest tests/ -x -k 'not slow'

# 实盘装 plugin
bash /Users/chenhongbo/person_task/catfish/edge/hermes-plugins/install-catfish-memory.sh

# 看 plugin 状态
hermes memory status

# Step C 回滚 (撞 bug 时)
export CATFISH_GATEWAY_LEGACY_SUMMARIZE=1
launchctl unload ~/Library/LaunchAgents/com.catfish.gateway.plist
launchctl load ~/Library/LaunchAgents/com.catfish.gateway.plist
```

---

## 不在 Week 2 范围 (留 backlog)

- ❌ **a2a_journal_hook.py 写路径搬迁** — 这是跨员工协作记录, 涉及 gateway 跟 hermes 之间的"我帮你 append" 通道设计, 单独 sprint. Week 2 保留 `append_to_journal()` 让它继续 work.
- ❌ **session_meta tick / session_meta.json 写**: gateway `app.py:2634 session_meta.tick()` 也是 gateway 写 edge file, 应该一起搬, 但 LOC 小独立, 留下次.
- ❌ **proactive.py / facts_pipeline.py / tool_archive 等 33 处 gateway 读 edge FS** — 跟 memory 无关的 backlog, 在 Phase 4 中央部署前各自单独处理.
- ❌ **Step E hard delete** — 留 ≥ 10 天双跑观察后下次 sprint.

---

**最后**: 今天累 + 已上头, 这份计划是给明天清醒的人 / 下一个 Claude 直接照做.
不真改代码, 真改留明天.
