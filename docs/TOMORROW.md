# 明天起手 checklist · 2026-04-26

> 今晚收工前钉死。明天醒来对着这个干,不要再花脑力规划。
>
> **节奏建议**: 8-10h/天可持续模式,不要再 16h 冲。Self-Evolution M2 + 测试 + 多会话 resume 三块加起来 ≈ 2.5 个工作日,本周内做完即可。

---

## 🌅 起手 30 分钟(P0-1 + P0-2 速决)

> 醒来上午做这两件,顺手清掉。

### Task 1 · Self-Evolution M2: `catfish_today_summary` tool(30 min)

**目标**:修今晚截图里 LLM 调 session_search 失败的 UX bug。给 LLM 一个明确的工具去回答"今天学了什么"。

**实现**:在 `catfish-tool-bridge` 暴露一个新 tool,内部调 Companion 的 `learning_today_stats` 命令(或者自己重新读 ~/.hermes 聚合)。

```python
# catfish/edge/tool-bridge/src/catfish_tool_bridge/catfish_tools.py (新文件)

CATFISH_NATIVE_TOOLS = [
    {
        "name": "catfish_today_summary",
        "description": "看小鲶今天学了什么:对话数 / 工具调用 / 新增 memory / 新增 skill / token 消耗。当员工问'今天学了什么''今日活动''小鲶今天做了啥'时调这个,而不是 session_search。",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        },
    },
]

def dispatch_catfish_native(name, args):
    if name == "catfish_today_summary":
        return collect_today_summary()
    raise ValueError(f"unknown native tool: {name}")

def collect_today_summary():
    # 复用 companion 那边 learning.rs 的逻辑(用 Python 重写一遍即可,~50 行)
    # 或者直接 IPC 调 Companion 也行,但增加耦合
    ...
```

在 `adapter.py` 的 `list_tools()` 加 `CATFISH_NATIVE_TOOLS`,`dispatch_tool()` 增加分发逻辑。

**验证**:Companion 对话发"今天学了什么",LLM 应该调 `catfish_today_summary` 而不是 `session_search`,然后基于真数据回答。

### Task 2 · 内网 IP 环境变量化(60 min)

**目标**:`10.10.40.102` 等公司内部 IP 散在代码里, **开源前必清**(STRATEGY Phase 0)。

**审查范围**:

```bash
# 找所有 hardcoded 内网 IP / 公司域名
grep -rn "10\.10\.40\." catfish/ --include="*.py" --include="*.rs" --include="*.ts" --include="*.yaml" --include="*.json" \
  | grep -v ".git" | grep -v "node_modules" | grep -v "venv"

# 找所有 hardcoded 飞书 / 内网 hostname
grep -rn "feishu.*\(internal\|corp\)\|\.corp\.\|\.internal\." catfish/ --include="*.py" --include="*.rs" --include="*.ts"
```

**修法**: 改成 env var(如 `CATFISH_INTERNAL_QWEN_HOST`),`.env.example` 文档化。

预期文件:
- `catfish/central/llm-gateway/config/models.yaml` (api_base 路径)
- 任何 hardcoded 在代码里的 hostname

---

## 🚀 主战场 · Plan C Week 3 多会话 + resume(3-5 天)

> 起手两件做完后, 进入这周主战场。Companion 真"能用"的分水岭。

### Sub-task 3.1 · 左侧会话列表 sidebar(1 天)

- 对话 tab 改 `[左侧 SessionList | 右侧 ChatPanel]` 布局
- 复用 `commands::sessions::sessions_list` 拉所有 sessions(已有)
- 区分 cli / companion 来源(badge 显示)
- 列表底部固定"+ 新对话"按钮

### Sub-task 3.2 · 切对话 / resume(2 天)

- 点列表 item → load 该 session 的 messages 到 store
- ChatStore 加 `loadSession(id)` action
- 调 `sessions_get(id)` 拉 SessionDetail(已有,但只返回最后一条 user/assistant) — 需要扩展为返回**全部** messages
- 改 `commands::sessions::sessions_get` 让它返回 `messages: Vec<ChatMessage>` 而不是只 last_user/last_assistant
- LLM streamChat 时把已有 messages 全部发,LLM 顺着上下文继续

### Sub-task 3.3 · 多并发 tab(1-2 天,可选)

- 顶部加"对话 1 | 对话 2 | + 新建" tab 条
- 每个 tab 独立 ChatStore 实例(改 store 为 Map<sessionId, ChatState>)
- 切 tab 切 store 切显示

> Sub-task 3.3 可选,基础多会话(3.1+3.2)做完已经能用,3.3 是体验加分。

---

## 🧪 测试 scope(明天上午起手前先列, 然后实施时同步写)

> **今晚不写**, 明天给关键路径补 critical-path tests。

### Critical(明天必补,~3 小时)

| 模块 | 测试要点 | 预估 |
|------|---------|------|
| `commands/session_write.rs` | create / append / finalize / update_title 4 个 happy path + 1 个并发(模拟 hermes 同时写) | 1h |
| `commands/learning.rs` | collect_memories / collect_new_skills / collect_db_stats 边界(空目录/空 db/今天没活动) | 1h |
| `central/llm-gateway/src/catfish_gateway/catalog.py` | 三状态字段:配 key+可达 / 配 key+不可达 / 没配 key | 30m |
| `edge/tool-bridge/src/catfish_tool_bridge/adapter.py` | list_tools 字段名 / dispatch_tool 异常处理 / 截断 | 30m |

测试用 Rust `#[cfg(test)] mod tests`(cargo test 内置)+ Python pytest(已有)。

### 已经有测试(不重补)

- `central/llm-gateway/tests/test_network.py` 14 单测 ✓
- `central/llm-gateway/tests/test_identity_inject.py` 16 单测 ✓
- `edge/local-search/tests/*` 已有 ✓
- `plugins/catfish-policy/test_matchers.py` 11 单测(早期写的)✓

### 可以放后面(P2-4 累积补)

- Companion 前端 React 组件 RTL 测试(ChatPanel / Dashboard 各卡)
- Tauri command 集成测试(IPC 端到端)
- E2E 测试(Playwright 跨进程)

---

## 📝 顺手要做的小事

- [ ] commit `Self-Evolution M2` 实现 + 测试一起上 (`feat(tool-bridge): catfish_today_summary native tool + tests`)
- [ ] commit 内网 IP 环境变量化 (`refactor: extract internal hostnames to env vars`)
- [ ] 每个新 PR 跑一次 `cargo clippy --workspace` 看有没有新警告
- [ ] CHANGELOG.md 加今天(2026-04-26)条目

---

## ⚠ 你今晚已经超载 16h

**今晚最后一件事**: 关电脑, 吃饭, 睡觉。

明天醒来:
1. 看这份 TOMORROW.md
2. 起手 30 分钟(Task 1 + 2)
3. 然后选 Plan C Week 3 多会话, OR 测试补完, OR 看心情
4. **8-10h 节奏**, 不要再连续 16h 冲

可持续节奏 > 短期英雄主义。鲶鱼是马拉松不是百米冲刺。

晚安, 鸿波。
