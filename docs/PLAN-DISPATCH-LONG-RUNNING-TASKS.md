# Plan · Dispatch 异步任务系统（"deep research" 风格）

> **状态**：planning，未启动
> **作者**：catfish 团队 + 鸿波
> **创建**：2026-05-24
> **优先级**：P2（Step 3 已交付"切会话不中断"，能盖 80% 长任务诉求；P2 留给真"30 分钟自动调研"场景）
> **预计工作量**：5-8 个工作日（一个完整 sprint）

---

## 1 · 背景与目标

### 1.1 已经做了什么

| 层次 | 状态 | 说明 |
|---|---|---|
| Level 1：定时 / 一次性 future task | ✅ ship 已久 | `mcp__scheduled-tasks` 已可用，cron / fireAt 风格 |
| Level 2：切会话不中断 stream | ✅ 5/24 ship | streamRegistry + per-session controller + sidebar ⏳ |
| **Level 3：deep-research 异步派发** | **❌ 本 plan 范围** | 5-30 分钟自动调研 / 数据爬取 / 多步串行任务 |

### 1.2 用户故事

- "帮我调研 DeepSeek V4 部署最佳实践，搜 100 个网页 + GitHub issue，30 分钟后给我报告"
- "扫描这个目录的 500 个 Excel，提取每个的指标，存到 .csv"
- "每周一早上跑一遍上周的客户邮件，按主题聚类 + 摘要"（这个 Level 1 也能盖，但下面 4 选 1 看适合度）
- "把 5 个客户合同跑同一套 NLP 抽取 pipeline，分别出一份 PDF 报告"

### 1.3 跟 Level 2 区别

| 维度 | Level 2（已 ship） | Level 3（本 plan） |
|---|---|---|
| 触发方式 | 用户在对话里 send | 用户对话里说"派发" / 命令式发起 |
| 进程 | 还在 Companion / hermes 主进程 | 独立 worker daemon |
| 生命周期 | Companion 退出 → stream 跟着死（除非 hermes 跑） | Companion 退出 → 任务继续，开机自启可恢复 |
| UI | 当前 chat 流式渲染 | 独立 "派发任务" tab + 进度条 + 完成通知 |
| 适合 | 几分钟到十几分钟，员工还坐在那 | 半小时到几小时，员工可能下班 |
| 资源边界 | 跟 chat 抢同一份 quota / API rate limit | 独立 quota，可限并发数 |

---

## 2 · 架构

### 2.1 拓扑图

```
                                     ┌──────────────────────────┐
                                     │ Companion (Tauri webview) │
                                     │  ┌───────────────────┐   │
                                     │  │ 现有 chat / sidebar│   │
                                     │  └───────────────────┘   │
                                     │  ┌───────────────────┐   │
                                     │  │ ★ Dispatch tab    │   │
                                     │  │  - 派发表单        │   │
                                     │  │  - jobs 列表 + 进度 │   │
                                     │  │  - 完成日志查看    │   │
                                     │  └─────────┬─────────┘   │
                                     └────────────┼─────────────┘
                                                  │ Tauri command:
                                                  │ dispatch_create_job
                                                  │ dispatch_list_jobs
                                                  │ dispatch_cancel_job
                                                  ▼
┌───────────────────────────────────────────────────────────────┐
│ catfish-gateway (port 8999)  ← 路由层 + 鉴权                  │
│  ┌────────────────────────────────────────────────────────┐  │
│  │ POST /api/dispatch     入队                            │  │
│  │ GET  /api/dispatch     列我的 jobs                      │  │
│  │ POST /api/dispatch/{id}/cancel                         │  │
│  │ GET  /api/dispatch/{id}/result  拿落盘的报告           │  │
│  └────────────────────────────────────────────────────────┘  │
└──────────────────────────┬────────────────────────────────────┘
                           │ 通过 PG 表 dispatch_jobs 通信
                           ▼
┌───────────────────────────────────────────────────────────────┐
│ catfish-dispatch-worker (新进程, systemd / launchd)           │
│  ┌────────────────────────────────────────────────────────┐  │
│  │ 主循环 (5s tick):                                       │  │
│  │   1. SELECT jobs WHERE status='pending' LIMIT 1 FOR UPDATE │  │
│  │   2. UPDATE status='running'                            │  │
│  │   3. spawn agent (复用 hermes_cli.main agent run)       │  │
│  │   4. 把 stdout / stderr / 工具调用日志写 job_logs 表     │  │
│  │   5. agent 退 → 收集 final output → 写 dispatch_jobs   │  │
│  │ 并发: max_concurrent_jobs=3 (yaml 配)                  │  │
│  └────────────────────────────────────────────────────────┘  │
└───────────────────────────────────────────────────────────────┘
                           │
                           ▼ agent 跑的时候照常调
┌───────────────────────────────────────────────────────────────┐
│ catfish-gateway /v1/chat/completions (与正常 chat 同源)      │
│  + 上游 LLM (DeepSeek / Gemini / Qwen 等)                    │
│  + tool-bridge (browser / files / 邮件等)                    │
└───────────────────────────────────────────────────────────────┘
```

### 2.2 数据模型（PG 表）

```sql
CREATE TABLE dispatch_jobs (
  id            UUID PRIMARY KEY,
  user_email    TEXT NOT NULL,
  title         TEXT NOT NULL,         -- "调研 DeepSeek V4 部署最佳实践"
  prompt        TEXT NOT NULL,         -- 完整任务描述（含上下文 + 输出要求）
  model         TEXT,                  -- 可选指定 model（不指定走 default）
  status        TEXT NOT NULL,         -- pending / running / done / error / cancelled
  progress_pct  INTEGER DEFAULT 0,     -- worker 自己上报，0-100
  progress_msg  TEXT,                  -- "正在搜第 47/100 个网页"
  created_at    TIMESTAMP NOT NULL DEFAULT NOW(),
  started_at    TIMESTAMP,
  finished_at   TIMESTAMP,
  result_path   TEXT,                  -- 输出落盘路径，e.g. ~/.catfish/dispatch/<id>/report.md
  error_msg     TEXT,
  agent_pid     INTEGER,               -- 跑这个 job 的 worker spawn 出的 agent pid，方便 kill
  -- 资源记账
  tokens_used   INTEGER DEFAULT 0,
  tool_calls    INTEGER DEFAULT 0,
  cost_estimate REAL DEFAULT 0
);
CREATE INDEX idx_dispatch_status ON dispatch_jobs(status);
CREATE INDEX idx_dispatch_user_created ON dispatch_jobs(user_email, created_at DESC);

CREATE TABLE dispatch_job_logs (
  id         BIGSERIAL PRIMARY KEY,
  job_id     UUID NOT NULL REFERENCES dispatch_jobs(id) ON DELETE CASCADE,
  ts         TIMESTAMP NOT NULL DEFAULT NOW(),
  level      TEXT NOT NULL,          -- info / warn / error / tool_call / tool_result
  message    TEXT NOT NULL
);
CREATE INDEX idx_dispatch_logs_job ON dispatch_job_logs(job_id, ts);
```

### 2.3 Worker 进程

新建 `central/dispatch-worker/`：

```
central/dispatch-worker/
├── pyproject.toml
├── src/catfish_dispatch_worker/
│   ├── __init__.py
│   ├── app.py              # main loop
│   ├── runner.py           # spawn hermes agent + 抓 stdout
│   ├── progress_parser.py  # 解析 agent 输出里的进度信号
│   └── notifier.py         # 完成时给 Companion / 邮件推送通知
├── config/
│   └── worker.yaml         # max_concurrent_jobs, log retention, etc
└── tests/
```

启动方式：

```bash
# 本地 dev
python -m catfish_dispatch_worker.app

# 生产 (macOS launchd)
~/Library/LaunchAgents/com.catfish.dispatch-worker.plist

# 生产 (Linux systemd)
/etc/systemd/system/catfish-dispatch-worker.service
```

### 2.4 复用 hermes 的 agent runtime

不重新造 agent loop。spawn 一个 `hermes_cli.main agent run --prompt-file ./prompt.md --tools-from-mcp --output-file ./result.md`。worker 等子进程退出 → 读 result.md → 写 dispatch_jobs.result_path。

好处：
- agent 的 tool calling / multi-round / MCP 接入复用现有的
- 进度信号通过 stdout 解析（hermes 已经会打 `[tool_call] xxx` 之类）
- 升级 hermes 自动惠及 dispatch worker

---

## 3 · Companion UI 设计

### 3.1 入口

主菜单加 tab "🚀 派发任务"，跟"早安 / 工作台 / 邮件 / 仪表盘" 同层级。

### 3.2 派发表单

```
┌─────────────────────────────────────────────┐
│ 派发一个长任务                                │
├─────────────────────────────────────────────┤
│ 任务标题: [_____________________________]  │
│ 任务描述:                                   │
│ ┌─────────────────────────────────────┐    │
│ │ 帮我调研 X，跑 30 分钟，给我一份报告  │    │
│ │ 涵盖 A/B/C 三个角度                  │    │
│ └─────────────────────────────────────┘    │
│ 模型: [Gemini 3.5 Flash ▼]                  │
│ 预期耗时: ⓘ 5-30 分钟                       │
│                                             │
│        [取消]        [派发，去干别的]       │
└─────────────────────────────────────────────┘
```

### 3.3 jobs 列表（这个 tab 的主视图）

```
┌──────────────────────────────────────────────────────────┐
│ ⏳ 进行中 (2)                                              │
├──────────────────────────────────────────────────────────┤
│ 🔄 调研 DeepSeek V4 部署最佳实践                          │
│    跑了 12 分钟 · 进度 60% · "正在搜第 47/80 个网页"      │
│    [日志 ▾] [⏸ 暂停] [✗ 取消]                            │
├──────────────────────────────────────────────────────────┤
│ 🔄 扫描 Q3 客户邮件聚类                                    │
│    跑了 3 分钟 · 进度 25% · "已分析 500/2000 封邮件"      │
│    [日志 ▾] [⏸ 暂停] [✗ 取消]                            │
└──────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────┐
│ ✓ 已完成 (8 · 近 7 天)                                    │
├──────────────────────────────────────────────────────────┤
│ ✓ 客户合同 NLP 抽取                  · 昨天 14:32 · 22min │
│    [查看报告] [重跑]                                       │
│ ✗ 失败：知乎话题爬取 ("被限流")        · 昨天 11:08 · 4min │
│    [查看日志] [重试]                                       │
│ ...                                                       │
└──────────────────────────────────────────────────────────┘
```

### 3.4 完成通知

- 系统通知（macOS notification center）
- Companion 顶部 toast "✓ 派发任务'调研 X' 完成，2 个产出文件"
- 可选邮件：在 worker.yaml 配 `notify_email: chenhongbo@ffcs.cn`

---

## 4 · 工作量拆分（每天）

| Day | 任务 | 文件 |
|---|---|---|
| 1 | PG 表 + alembic migration + Pydantic schema | `central/llm-gateway/alembic/versions/2026XX_dispatch_jobs.py` |
| 1 | Gateway 路由：POST/GET/DELETE `/api/dispatch` | `central/llm-gateway/src/catfish_gateway/dispatch_router.py` |
| 2 | Worker 主进程骨架（main loop + claim job + spawn） | `central/dispatch-worker/src/catfish_dispatch_worker/app.py` |
| 2 | Hermes agent spawn + stdout 抓取 + progress 解析 | `runner.py`, `progress_parser.py` |
| 3 | Worker 完成时写 result + tokens 记账 + 通知 | `notifier.py` |
| 3 | launchd plist + systemd unit 模板 | `central/dispatch-worker/deploy/` |
| 4 | Companion Tauri command（create/list/cancel）| `src-tauri/src/commands/dispatch.rs` |
| 4 | Companion UI 派发 tab：表单 + jobs 列表 | `src/tabs/Dispatch/DispatchTab.tsx` |
| 5 | Companion UI 进度条 + 日志 viewer + 通知 | `src/tabs/Dispatch/JobRow.tsx`, `LogViewer.tsx` |
| 5 | 端到端 e2e 测试 + 文档 | `central/dispatch-worker/tests/`, `docs/DISPATCH-USAGE.md` |

> 估算偏紧。出现意外预留 2-3 天 buffer，最坏 8 天。

---

## 5 · 关键设计决策

### 5.1 为什么独立 worker 进程，不直接在 gateway 里 spawn task

| 方案 | 优 | 劣 |
|---|---|---|
| gateway 内 asyncio task | 部署简单 | gateway 重启所有 in-flight 任务死；占 gateway 的 event loop 影响 chat 响应 |
| **独立 worker（本 plan）** | 解耦：gateway 重启不影响、worker 重启不影响 gateway；可独立扩缩容 | 多一个进程要部署 |
| Celery / Dramatiq 队列 | 工业标准 | 引入 redis 依赖；50 人规模过度 |

50 人规模独立 Python 进程 + PG 表当队列足够，不需要 redis。

### 5.2 为什么复用 hermes 不自己写 agent runner

- hermes 已经处理 tool calling / multi-round / context truncation / model switch
- 升级 hermes 同步惠及 dispatch
- worker 代码量小 → 维护负担低
- 缺点：依赖 hermes 输出格式做 progress 解析，hermes 升级可能要跟着改解析

### 5.3 资源限制（防滥用）

| 限制 | 默认值 | 配置位 |
|---|---|---|
| 同时跑的 jobs 数 | 3 | `worker.yaml: max_concurrent_jobs` |
| 单 job 最长耗时 | 60 分钟 | `worker.yaml: max_job_duration` |
| 单 user 每天派发数 | 20 | quotas.yaml（复用现有 quota 系统） |
| 单 job tokens 上限 | 500k | 同上 |

### 5.4 跟现有 Level 2（streamRegistry）的边界

| 场景 | 用哪个 |
|---|---|
| 短任务（< 5 分钟），希望看实时进度 | 普通 chat + Level 2 切走不中断 |
| 长任务（> 10 分钟），可以等结果 | Level 3 dispatch |
| 定时 / cron | Level 1 scheduled-tasks |

UI 上派发 tab 跟 chat tab 完全独立，员工自己判断走哪条。后期可加"在 chat 里 @dispatch 自动派发"快捷语法。

---

## 6 · 风险与开放问题

| 风险 | 缓解 |
|---|---|
| Hermes 输出格式变了 → progress 解析挂 | 解析器对未识别行 fallback "running…"，不挂 worker；hermes 升级跑 dispatch e2e 测试 |
| Worker 进程崩溃 → in-flight job 状态错 | 启动时扫 status='running' 但 agent_pid 不存在的 job → 标 status='interrupted'，员工可重试 |
| 单 user 派发 50 个 job 把 worker 占死 | quotas.yaml 每天数 + 同时跑数限制 |
| LLM API rate limit 撞死 dispatch | dispatch 走单独 token bucket，跟 chat 不共享 |
| Worker 跑半夜被 macOS sleep 杀 | launchd 配 `LimitLoadToSessionType=Background` + `KeepAlive=true` |
| 跨平台部署：客户内网 Linux + 员工 mac | gateway + worker 都在客户内网（合规），员工本地只装 Companion |

**开放问题（决策待定）：**

1. **要不要让 dispatch 跑客户内网 deepseek 还是公网？** 默认走客户内网，但内网挂时是 fallback 公网还是 retry？建议：fallback 公网（带提示，员工知道走出去了）。
2. **日志保留多久？** 建议 30 天后归档到 S3/OSS（参考 audit log 策略），dispatch_jobs 主表保留 90 天 metadata。
3. **多用户隔离粒度？** 50 人规模就按 user_email 软隔离够了；上 200 人可能要按部门加 row-level security。

---

## 7 · 验收标准

启动这个 plan 时，DoD（Definition of Done）：

- [ ] alembic 自动建表，gateway 启动自动 migrate
- [ ] gateway `/api/dispatch` 4 个端点都 work，curl 能创建 / 列 / 取消 job
- [ ] worker 独立进程跑，能 claim job → spawn hermes agent → 写结果回 db
- [ ] worker 重启不丢 job（in-flight 重启后能 recover 或标 interrupted）
- [ ] Companion 派发 tab 能看到 jobs 列表 + 进度
- [ ] 完成时有系统通知 push
- [ ] launchd plist + systemd unit 文档齐全
- [ ] quotas.yaml 加 dispatch 配额段
- [ ] 一个端到端 e2e：派发"写一首关于鲶鱼的诗" → 1 分钟内完成 → 报告可查看
- [ ] 加压测：5 个并发 job 跑 30 分钟，不漏不串

---

## 8 · 接下来怎么启动

不在这个 sprint 启动。等以下任一信号触发：

- 鸿波或员工**真正抱怨过** 3 次以上"想跑长任务但 Companion 体验不行"（Level 2 cover 之后还出现）
- 客户 demo 需要"deep research"这个卖点
- 有员工提了具体的 cron 也满足不了 + chat 也满足不了的 use case（写 3 个 user story 进 issue tracker）

否则保留这份 plan，下个 quarter review 再决定。

---

*相关：Level 2 streamRegistry 实现见 `edge/companion-app/src/lib/streamRegistry.ts` + BL-MULTI-SESSION-STREAM 注释。*
