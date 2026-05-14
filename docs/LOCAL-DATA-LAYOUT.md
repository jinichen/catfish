# 鲶鱼 · 数据所有权与落盘布局 v0.1

> **版本**: v0.1, 2026-05-14 凌晨, 鸿波两次追问"catfish 用啥 db" 后落档.
> **前因**: 之前"catfish 用什么数据库"散在 6+ 个模块的 docstring 里, 没单点 doc. 我 (Claude) 5/14 凌晨连续两次答错 ("只读 state.db" / "本地没数据库"), 鸿波两次追问纠正才搞清楚. 这文档是单点真源, 下次问"X 数据存哪" 看这里, 不要再凭印象答.
> **维护**: 任何新增/迁移数据存储位置, 必须同步改这文档.

---

## 1. 三层数据所有权 (TL;DR)

```
                    ┌─────────────────────────────────────┐
                    │  会话本体 (chat 流水)                │
                    │  ~/.hermes/state.db  (sqlite, WAL)  │
                    │  ├ sessions 表                       │
                    │  └ messages 表                       │
                    │                                       │
                    │  ★ Companion + hermes CLI 双写 ★    │
                    └────────────┬─────────────────────────┘
                                 │ 蒸馏 / 提取
                                 ▼
                    ┌─────────────────────────────────────┐
                    │  catfish 元数据小盒子 (派生)         │
                    │  ~/.catfish/*.json{l}/.md           │
                    │  画像 / 印象 / 事实 / 风格 / 反馈    │
                    │                                       │
                    │  ★ catfish 自己写, hermes 不碰 ★    │
                    └────────────┬─────────────────────────┘
                                 │ (跨员工聚合 / RBAC / quota)
                                 ▼
                    ┌─────────────────────────────────────┐
                    │  中央 PostgreSQL                      │
                    │  catfish-gateway + catfish-identity  │
                    │  audit / quota / users / RBAC 等     │
                    │                                       │
                    │  ★ 跨员工共享, 公司层 ★             │
                    └─────────────────────────────────────┘
```

**记忆术**:
- 想知道"我们聊过啥" → state.db
- 想知道"小鲶对我的认识" → ~/.catfish/
- 想知道"全公司用了多少 token / 谁能调啥模型" → 中央 PG

---

## 2. Layer 1 — 会话本体 (`~/.hermes/state.db`)

### 2.1 这是什么

hermes 0.13 自带的 SQLite, **chat session 的真本体**. WAL 模式 + busy_timeout 5s, 支持多进程并发读写.

### 2.2 表

| 表 | 字段 (核心) | 谁写 |
|---|---|---|
| `sessions` | `id` (`YYYYMMDD_HHMMSS_xxxxxx`) / `title` / `started_at` / `source` (`companion`/`cli`) / 累计 `input_tokens` / `output_tokens` / `cache_read_tokens` / `cache_write_tokens` / `reasoning_tokens` | hermes CLI + Companion |
| `messages` | `session_id` / `role` / `content` / `tool_calls` / `timestamp` | hermes CLI + Companion |

### 2.3 谁读谁写

| 模块 | 读 | 写 | 文件 |
|---|---|---|---|
| **hermes 0.13 CLI** | ✅ (原生 agent loop) | ✅ (原生) | hermes 内部 |
| **Companion** (Tauri Rust) | ✅ (Sessions tab / Dashboard "今日 sessions" 计数 / Kanban) | ✅ — `source='companion'` | `edge/companion-app/src-tauri/src/commands/session_write.rs` |
| **catfish-tool-bridge** | ✅ — `mode=ro` 只读 | ❌ | `edge/tool-bridge/src/catfish_tool_bridge/sessions_search.py` (跨 session 搜) + `catfish_tools.py:2120` (今日 token 统计) |
| **catfish-gateway** (中央) | ❌ | ❌ | gateway 不管 session 持久化, 它只是 LLM proxy |

### 2.4 关键设计 — `source` 字段

Plan C Week 2 决策 1c: Companion 起的会话标 `source='companion'`, hermes CLI 起的标 `source='cli'`. 共用一张 `sessions` 表, 这样:

- Sessions tab 列两个客户端的会话, 不区分
- `hermes --resume <id>` 能继续 Companion 起的对话 (同 schema)
- 跨客户端搜 (`catfish_search_sessions`) 一把抓
- 删 `~/.hermes/state.db` = 同时删两边历史 (单一 source of truth)

### 2.5 为什么不用 catfish 自己开 db

历史决策: 如果 catfish 另开 db 存 messages 副本, 必须解决:
- 双写一致性 (Companion 一发, 写 hermes db + catfish db, 失败哪边算数)
- schema 演进同步
- 大小翻倍

复用 hermes 的 db = 零额外成本. 唯一代价: `hermes` 升级如果改 schema (e.g. 0.12 → 0.13 sessions 加字段), `session_write.rs` 得跟. 用 `optional column` + select-by-name 兼容多版本.

---

## 3. Layer 2 — catfish 元数据小盒子 (`~/.catfish/*`)

### 3.1 完整文件清单

| 文件 | 写的人 | 读的人 (UI 卡 / 模块) | 触发时机 | 数据特征 |
|---|---|---|---|---|
| `employee_journal.md` | gateway `memory_distill.py` | Dashboard "**小鲶对你的印象**" 卡 / SOUL.md 注入 / a2a journal hook | gateway 每 N session 蒸馏一次 messages → 5 段日记 | 5 KB-15 KB cap (BL-FIX21 5/13 cap 收紧) |
| `memory_distill_state.json` | gateway `memory_distill.py` | 同上模块自己 | 蒸馏完写 cursor (避免重算 messages) | < 1 KB |
| `session_facts.json` | LLM 调 `catfish_remember(key, value)` | Dashboard "**小鲶记的具体信息**" 卡 / SOUL.md 注入 | session 内员工告诉硬事实 (e.g. "eis_url=...") | v2 schema (按 key 存 revision 数组) |
| `user_profile.json` | LLM 调 `catfish_user_profile_propose/_confirm` | Dashboard "**小鲶对你的画像**" 卡 / SOUL.md 注入 | LLM 累 evidence ≥3 才 propose, 员工 confirm 才落 | 8-12 字段 (语气 / 节奏 / 偏好等) |
| `style_fingerprint.json` | `catfish_style_fingerprint_refresh` 后台 | Dashboard "**你的文书风格 (隐式)**" 卡 / 写文档 skill 注入 | 后台扫历史输出文档 (周报/通知) 抽统计 | < 5 KB |
| `feedback.jsonl` | Companion 直接 append (Rust) | Dashboard "**你给小鲶的反馈**" 卡 | 员工点 👍/👎/✏️改 时 | append-only, 永不重写 |
| `session_meta.json` | Companion 起 session 时写 | Dashboard "距上次找我 / 今天第 N 次" 字段 | 每次开 session | < 1 KB |

### 3.2 谁读谁写

| 模块 | 写哪几个 | 读哪几个 | 路径常量 |
|---|---|---|---|
| **catfish-gateway** (中央) | `employee_journal.md` / `memory_distill_state.json` | 同上 (蒸馏时回溯) | `memory_distill.py:DISTILL_STATE_PATH` + `employee_journal.py:journal_path` |
| **catfish-tool-bridge** | `session_facts.json` / `user_profile.json` / `style_fingerprint.json` | 同上 (tool 实现) | `catfish_tools.py:SESSION_FACTS_PATH` / `user_profile.py:USER_PROFILE_PATH` / `style_fingerprint.py:STYLE_FINGERPRINT_PATH` |
| **Companion** (Tauri Rust) | `feedback.jsonl` / `session_meta.json` | 全部 7 个 (Dashboard 卡渲染) | `commands/feedback.rs` / `commands/relation.rs` / `commands/memory_history.rs` |
| **hermes CLI** | ❌ 一个都不写 | ❌ 一个都不读 | catfish 私有, hermes 不知道 |

### 3.3 为什么散 JSON 不开本地 db

- **手可改可看**: `cat ~/.catfish/user_profile.json` 直接读, 不用打开 sqlite client
- **rsync 备份一行**
- **故障自愈**: 一个文件坏了 (e.g. style_fingerprint.json 写到一半 crash) 不污染别的; sqlite WAL 一坏全表挂
- **schema 零成本演进**: JSON 加字段不要 migration (BL-MM2 v1 → v2 revision 数组就是直接加, 老格式 reader 兼容)
- **写并发不存在**: 单员工单 Companion + 单 tool-bridge, 不需要事务
- **量小**: 你机器现在 ~6.6 KB 便签 + 641 KB 印象 + 几个 KB 画像, 加起来 < 1 MB

代价: 没事务 / 没索引 / 没 SQL. 但元数据本来就**只读为主, 写很稀疏** (人一天 propose 几次画像), 不需要这些.

---

## 4. Layer 3 — 中央 PostgreSQL (catfish-gateway + catfish-identity)

### 4.1 为啥要中央 PG (不像本地散文件)

- 跨员工聚合 (manager 看本部门 audit)
- RBAC (按部门拦模型/工具/skill)
- Quota 全公司硬上限
- 多客户端共享配置 (admin 改 yaml 也写 PG, 所有 gateway 节点拉到)

### 4.2 表

#### catfish-gateway (主库)

| 表 | 用途 | 关键字段 |
|---|---|---|
| `gateway_audit` | 每次 LLM 请求一行 | `sub` / `model` / `prompt_tokens` / `completion_tokens` / `latency_ms` / `error` / `started_at` / `department` / **Dashboard "本月配额" 算这个** |
| `gateway_quota_users` | 用户 quota 配置 | `sub` / `daily_limit` / `monthly_limit` / `enabled` |
| **(5/16 加)** `dept_access_policy` | 部门访问策略 (BL-RBAC P0) | `department` / `models_mode` / `tools_mode` / `skills_mode` / `channels_mode` |
| **(5/16 加)** `dept_allowed_models` | 部门可用模型白名单 | `(department, model_name)` PK |
| **(5/17 加)** `dept_allowed_tools` | 部门可用 tool 白名单 | `(department, tool_name)` PK |
| **(5/18 加)** `dept_allowed_skills` | 部门可用 skill 白名单 | `(department, skill_namespace, skill_name)` PK |
| **(5/19 加)** `dept_allowed_channels` | 部门可用 channel 白名单 | `(department, channel_id)` PK |
| **(5/23 加)** `catfish_tasks` | 长任务追踪 (RED-2-PG) — 5/13 jsonl 过渡品迁移 | `sub` (FK) / `kind` / `status` / `started_at` / `result_preview` |

#### catfish-identity (用户库)

| 表 | 用途 | 关键字段 |
|---|---|---|
| `users` | 真用户表 (BL-D17 5/4 PG 化, yaml 退化为 dev fallback) | `email` / `password_hash` / `role` / `department` / `managed_departments` / `locked` / `last_login_at` |

#### catfish-identity 仍是 yaml (没进 PG)

| 文件 | 为什么不进 PG |
|---|---|
| `central/identity-server/config/clients.yaml` (5/14 BL-RBAC P0 + B Day 1 ship) | OAuth client 数量极少 (10-20 个服务) + 改少 + admin 直接 vim 改, 没必要 db |

### 4.3 PG 配置

`CATFISH_DB_URL=postgresql+asyncpg://...` env 配, `central/llm-gateway/.env` + `central/identity-server/.env` 各自一份. 没配 → degrade 到 yaml-only / 拒服务 (按模块).

---

## 5. 删数据 — 想忘啥删啥

| 想做的事 | 删 |
|---|---|
| 删某次具体会话内容 | hermes CLI: `hermes session delete <id>`; 或 sqlite: `DELETE FROM sessions WHERE id=?` |
| 清空所有聊天历史 (Companion + CLI) | `rm ~/.hermes/state.db` (hermes 重启会重建空 schema) |
| 让小鲶忘记对我的印象 (5 段日记) | `rm ~/.catfish/employee_journal.md ~/.catfish/memory_distill_state.json` |
| 清空具体便签 (catfish_remember 写的) | Dashboard "**清空记忆**" 按钮 / `rm ~/.catfish/session_facts.json` |
| 清空长期画像 (8 项) | Dashboard "**清空全部画像**" 按钮 / `rm ~/.catfish/user_profile.json` |
| 重置文书风格 | `rm ~/.catfish/style_fingerprint.json` (下次写文档时自动重扫) |
| 清空 feedback 历史 | Dashboard "**清空反馈**" 按钮 / `rm ~/.catfish/feedback.jsonl` |
| 完全 factory reset (本地全清, 不动中央) | `rm -rf ~/.catfish ~/.hermes/state.db ~/.hermes/skills` |
| 清中央 audit (admin) | `DELETE FROM gateway_audit WHERE started_at < ?` (PG, admin 才能调) |

---

## 6. 跨员工 / 联邦 (Plan D)

5 月 ship 的 a2a (employee → employee 跨问), 数据流:

```
Alice 调 catfish_expert_consult(question, target=bob@x.com)
   │
   ├─ 写 ~/.catfish/a2a_audit.jsonl  (本地审计 — 我问了谁啥)
   │
   ▼
Bob 的 catfish-gateway /a2a/ask 收
   │
   ├─ 写 Bob 的 ~/.catfish/employee_journal.md  (Bob 的"印象"加一条 "Alice 5/14 问我 X")
   ├─ 写 ~/.catfish/a2a_notify.jsonl  (Bob 收到的待回 / 已回)
   │
   ▼
Bob 答完 → 回到 Alice 的 ~/.catfish/a2a_audit.jsonl 标 'answered'
```

注意: a2a 数据**永不进 PG** (单员工电脑边界), 只通过中央 `central/identity-server/.../registry.py` 黄页找对方 endpoint, 内容直接 P2P (网内 HTTPS).

---

## 7. RecMode 录屏数据 (BL-LEARN-RECMODE, 5/14 ship)

| 文件 | 谁写 | 隐私策略 |
|---|---|---|
| `~/.catfish/recordings/<sid>/screenshots/kf_*.png` | gateway `cdp_listener.py` | 14 天自动删 (除非 `.keep_forever` 标) |
| `~/.catfish/recordings/<sid>/events.jsonl` | 同上 | 同上 |
| `~/.catfish/recordings/<sid>/transcripts.jsonl` (语音转写, 可关) | 同上 | 同上 |
| `~/.catfish/recordings/<sid>/meta.json` | 同上 | 同上 |
| `~/.catfish/recordings/<sid>/skill_draft/<ns>/<name>/` (LLM 生成的 skill 草稿) | gateway `aggregator.py` | 同上, 但员工 "保存" 后 mv 到 `~/.catfish/skills/` 永久留 |
| `~/.catfish/skills/<ns>/<name>/` | 员工 "保存" 录屏后 mv | 永久留 (员工自己删) |

cleanup daemon: `central/llm-gateway/.../recmode/cleanup.py` 每 24h 跑一次, 见 docs/RBAC-DESIGN.md 之外 `BL-LEARN-RECMODE` track.

---

## 8. 路径常量速查

```python
# 本地 (员工电脑)
~/.hermes/state.db                              # 会话本体 (Companion + hermes 双写)
~/.hermes/skills/                               # 已装 skill (hermes runtime 用)

~/.catfish/employee_journal.md                  # 印象
~/.catfish/memory_distill_state.json            # 蒸馏 cursor
~/.catfish/session_facts.json                   # 便签 (catfish_remember)
~/.catfish/user_profile.json                    # 长期画像
~/.catfish/style_fingerprint.json               # 文书风格
~/.catfish/feedback.jsonl                       # 👍/👎/改
~/.catfish/session_meta.json                    # 距上次 / 今天第 N 次
~/.catfish/recordings/<sid>/                    # RecMode 录屏 (14 天 TTL)
~/.catfish/skills/                              # 员工录屏后保存的 skill
~/.catfish/a2a_audit.jsonl                      # 本地 a2a 审计 (我问了谁)
~/.catfish/a2a_notify.jsonl                     # 我收到的 a2a 请求

# 中央 (catfish-gateway 节点)
$CATFISH_DB_URL                                 # PostgreSQL connection (audit/quota/RBAC/tasks)

# 中央 (catfish-identity 节点)
central/identity-server/config/users.yaml       # dev fallback (PG seed 一次后退化)
central/identity-server/config/clients.yaml     # OAuth client (5/14 BL-RBAC P0+B Day 1 ship)
~/.catfish/identity-server/keys/                # JWT RSA 私钥 (生产)
$CATFISH_DB_URL                                 # PostgreSQL (users 表)
```

---

## 9. 维护与失效条件

**这文档失效 if**:
- 任何模块新增 `~/.catfish/<新文件>` 或 `~/.hermes/<新文件>` 而没回来更
- 中央 PG 加表而没回来更
- hermes upgrade 改 sessions/messages schema 而 `session_write.rs` 跟改时没同步本文 §2.2

**审计 cadence**:
- 每个 sprint 收尾扫一次 `git diff` 找 `~/.catfish/` / `~/.hermes/` / `CREATE TABLE` 新增, 同步到本文
- 每季度 (Phase 切换时) 整体 review 一次

---

## 10. 决策签名

> v0.1 = 2026-05-14 凌晨, 鸿波两次追问后落档.
> 教训:
> 1. "数据存哪" 这种基本架构问题必须有单点 doc — 散在 6 个模块 docstring 里 LLM 答不出统一答案
> 2. **state.db 是双写共用** (Companion + hermes), 不是 "hermes 自己的 catfish 只读"
> 3. **catfish 不开本地 db** — 元数据散 JSON 是有意设计 (透明可改 / 故障自愈 / 备份简单)
> 4. **中央 PG 才有 db** — 跨员工聚合 / RBAC / quota
> 5. RecMode 录屏 + a2a 都遵循 "本地散文件 + 中央只放跨员工聚合" 原则
