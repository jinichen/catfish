# catfish-memory · hermes MemoryProvider plugin

聚合 catfish 5 个边缘数据源 (employee_journal / skills_catalog / feedback /
session_meta / skill_guard) 注入 hermes system prompt. 替代 gateway 老
`memory_registry` 反 pattern.

详见 `docs/MEMORY-OWNERSHIP-ARCHITECTURE.md` (5/19 凌晨锁定).

## 为什么

5/18 之前 catfish-gateway 在 hermes 之上又造了一层 `memory_registry` 10
provider, 直接读 `~/.hermes/state.db` + `~/.catfish/*` 拼 system prompt.
违反"memory 是 hermes 唯一责任" 的架构分工 — 5/19 凌晨鸿波点穿后启动
BL-MEMORY-OWNERSHIP-FIX 重构.

本 plugin 是 Phase 2 第一个 deliverable: 把 catfish 自己加的 5 个 provider
迁到 hermes MemoryProvider 体系内, 让 hermes 唯一负责 memory inject. gateway
退化纯 LLM 代理.

## 数据源

| Provider 数据 | 文件 | 老 gateway provider | 用途 |
|---|---|---|---|
| `session_meta` | `~/.catfish/session_meta.json` | SessionMetaProvider (priority=30, 500B) | 时间感 (距上次 N 天) |
| `employee_journal` | `~/.catfish/employee_journal.md` + `distilled_facts.md` | EmployeeJournalProvider (60, 5KB) | 员工长期日志 |
| `skills_catalog` | `~/.catfish/skills/*/SKILL.md` | SkillsCatalogProvider (40, 20KB) | catfish 技能列表 |
| `feedback` | `~/.catfish/feedback.jsonl` (尾 10 条) | FeedbackProvider (70, 2KB) | 员工 👍/👎 反馈 |
| `skill_guard` | 静态 prompt (条件触发) | SkillGuardProvider (55, 3KB) | 员工提 skill 时铁律 |

**全部 read-only** — 我们只读 `~/.catfish/` 不写. 写入路径 (会话结束抽事实
/ 用户偏好确认 / 反馈写入) 走 hermes builtin / catfish-tool-bridge tool.

**子数据源容错** — 单个文件缺 / 损坏 → 该 section 跳过, 其它正常.

## 安装

### 自动 (推荐, 跟 catfish-autocompress 同模式)

```bash
# 从 catfish repo 装
cd ~/person_task/catfish/edge/hermes-plugins
bash install-catfish-memory.sh        # TODO: 写这个安装脚本
```

### 手动

```bash
# 软链到 hermes plugins/memory/
ln -s ~/person_task/catfish/edge/hermes-plugins/catfish-memory \
      ~/.hermes/hermes-agent/plugins/memory/catfish-memory

# 激活 (改 hermes config.yaml)
# memory:
#   provider: catfish-memory

# 重启 hermes
pkill -f 'hermes serve'
nohup hermes serve > ~/.hermes/serve.log 2>&1 &
```

## 验证

```bash
# 看 hermes 是否发现 plugin
hermes memory list | grep catfish-memory

# 看 plugin 是否激活
grep -A 2 "^memory:" ~/.hermes/config.yaml

# 实盘: 跟鲶鱼聊天, 看 hermes log 有没有 prefetch 调用
hermes serve 2>&1 | grep "catfish-memory"

# 单测 (POC 阶段)
cd ~/person_task/catfish/edge/hermes-plugins/catfish-memory
python -m pytest test_catfish_memory.py -v
```

## 约束 / 限制

1. **One-external-provider limit**: hermes MemoryManager 只允许一个外部
   provider 同时跑. 激活 catfish-memory 会替换 holographic / hindsight /
   honcho 等. hermes builtin (`~/.hermes/memories/`) 不受影响, 一直在
2. **不暴露 LLM tool**: `get_tool_schemas()` 返 `[]`. catfish 的 tool
   (`catfish_email_search` / `catfish_search_sessions` 等) 在 catfish-tool-bridge
   plugin 注册, 不在这里重复
3. **read-only**: `sync_turn` / `on_session_end` 走默认 no-op. 写入走 hermes
   builtin 或 tool-bridge
4. **数据本地优先**: 严守 BL-CENTRAL-EDGE-BOUNDARY — 永不上行公司机房 PG,
   只在 LLM 调用瞬间作为 prompt 内容上行上游 LLM

## 下一步

POC 验证后:

- ⬜ 写 `install-catfish-memory.sh` 安装脚本 (跟 catfish-autocompress 同模式)
- ⬜ 实盘验证: 把 hermes 切到 catfish-memory + Companion 走 hermes serve 完整闭环
- ⬜ 删 gateway 那 5 个老 provider (`memory_registry` Phase 2 后半)
- ⬜ 加单测覆盖 (本目录 `test_catfish_memory.py`)

详见 `docs/MEMORY-OWNERSHIP-ARCHITECTURE.md` Phase 2-4.
