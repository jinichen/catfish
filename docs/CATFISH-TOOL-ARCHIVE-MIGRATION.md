# tool_archive 搬 edge 重构设计 (BL-CENTRAL-EDGE-TOOL-ARCHIVE)

> **状态**: 设计稿 v1, 2026-05-22 鸿波拍板启动
> **关联**: `docs/CENTRAL-EDGE-DATA-BOUNDARY.md` (5/17 强制纪律) — 本搬迁是该 doc B 类违规清单第 5 项收尾
> **决策记录**:
>   - 鸿波 5/22 拍: "违规是坚决禁止的, 真重构, 不留 metadata-only 空架子"
>   - 鸿波 5/22 拍: 老 PG 数据 = 导出 + 自然 TTL 过期, 1-2 周后 DROP TABLE
>   - 鸿波 5/22 拍: portable 跟搬一起设计, 不留 V2

---

## 1. 当前违规实情

`central/llm-gateway/src/catfish_gateway/tool_archive/` 1528 LOC, PG schema 长这样:

```sql
CREATE TABLE tool_archives (
    ref             VARCHAR(16) PRIMARY KEY,
    session_id      VARCHAR(128) NOT NULL,
    user_email      VARCHAR(255) NOT NULL,
    tool_call_id    VARCHAR(128),
    tool_name       VARCHAR(128),
    content         TEXT NOT NULL,    -- ⚠️ tool result 全文 (浏览器 HTML / API JSON / 文件内容)
    content_bytes   INTEGER NOT NULL,
    lines           INTEGER NOT NULL,
    summary         TEXT,             -- ⚠️ LLM summary 衍生品
    summary_model   VARCHAR(64),
    summary_at      TIMESTAMP,
    summary_error   TEXT,
    created_at      TIMESTAMP NOT NULL DEFAULT NOW(),
    expires_at      TIMESTAMP NOT NULL DEFAULT (NOW() + INTERVAL '14 days')
)
```

`content TEXT NOT NULL` = 全部 tool result content 在中央 PG. 14 天 TTL. 这是 BOUNDARY 文档 §"违规示例" 第一条 "gateway 把 chat messages 写文件" 的 PG 版.

## 2. 目标架构 (搬 catfish-tool-bridge edge in-process)

```
现在 (违规):
  LLM → gateway chat_completion
    → tool_call (e.g. browser_snapshot)
    → tool-bridge dispatch → tool returns 5MB HTML
    → gateway tool_archive.archiver: threshold check + 替换 ref + 写 PG
    → 替换后 messages 返 LLM

  LLM → catfish_read_tool_archive(ref="abc123")
    → tool-bridge → HTTP POST gateway /api/tool-archives/read
    → gateway 查 PG → 返 content

搬后 (合规):
  LLM → gateway chat_completion
    → tool_call (e.g. browser_snapshot)
    → tool-bridge dispatch → tool returns 5MB HTML
    → tool-bridge 自己: threshold check + 替换 ref + 写本机 (sqlite + 文件)
    → 替换后 messages 返 LLM (gateway 不做 archive)

  LLM → catfish_read_tool_archive(ref="abc123")
    → tool-bridge → 直接读本机 sqlite + 文件 (跳过 HTTP)
```

gateway 端: **删整目录 1528 LOC, app.py 4 处 import 改 pass-through**, alembic 加 down revision 删表.

## 3. 本机存储设计 (Portable First)

### 文件布局

```
~/.catfish/tool_archives/
├── _index.sqlite               # ref → file_path 索引, 跨 date 快查
├── 2026-05-22/                 # 按 date 分桶 (易清理 + 易整体打包)
│   ├── abc123def4567890.json   # 文件名 = ref + ".json"
│   ├── ...
├── 2026-05-23/
│   ├── ...
└── ...
```

### 单文件 schema (JSON)

```json
{
  "ref": "abc123def4567890",
  "tool_name": "catfish_browser_snapshot",
  "tool_call_id": "call_xyz",
  "session_id": "<portable>",
  "content": "<5MB HTML>",
  "content_bytes": 5242880,
  "lines": 18429,
  "summary": "页面包含 EIS 待办列表 6 条 ...",
  "summary_model": "qwen-flash",
  "summary_at": "2026-05-22T20:15:00+08:00",
  "created_at": "2026-05-22T20:14:55+08:00"
}
```

### sqlite index

```sql
CREATE TABLE tool_archives_index (
    ref           VARCHAR(16) PRIMARY KEY,
    file_path     TEXT NOT NULL,      -- 相对 ~/.catfish/tool_archives/ 的路径, 如 "2026-05-22/abc.json"
    tool_name     VARCHAR(128),
    content_bytes INTEGER NOT NULL,
    lines         INTEGER NOT NULL,
    has_summary   INTEGER NOT NULL,    -- 0/1
    created_at    TEXT NOT NULL,       -- ISO-8601
    -- portable 字段
    session_id    TEXT,                -- 可空, 老 hermes session_id 仅作历史信息, 不索引
    catfish_user  TEXT                 -- catfish OIDC sub, portable 跨 Mac 后能查
);
CREATE INDEX idx_tool_archives_created ON tool_archives_index(created_at);
CREATE INDEX idx_tool_archives_tool ON tool_archives_index(tool_name);
```

### Portable 性

| 维度 | 设计 |
|---|---|
| 跨 Mac 带走 | 整目录 `~/.catfish/tool_archives/` 一起 cp 到新 Mac, 同 catfish account 登 → ref lookup 立刻通 |
| session_id 绑定 | **解绑** — old hermes session_id 仅作历史信息存 (不索引). 新 ref lookup 只靠 ref + 本机文件存在 |
| catfish_user 字段 | 新加, 写 OIDC sub. 跨 Mac 验证: 同 sub 才能读 (sysadmin override 例外) |
| 14 天 TTL | 本机 cron 清 (跟 hermes audit cleanup 同款), 不靠 PG TTL |

## 4. 搬迁 Phase 计划

| Phase | 内容 | 工作量 |
|---|---|---|
| **1** (本 doc) | 设计稿 + 鸿波 review | 0.5d ← **当前** |
| 2 | edge 写新模块 `catfish_tool_bridge/tool_archive_local.py` (archiver + sqlite index + reader) | 3d |
| 3 | edge 改 `read_tool_archive.py` 直读本机 (剥 HTTP) | 0.5d |
| 4 | edge `catfish_tools._dispatch_native_inner` 加截胡 wrapper | 1d |
| 5 | 老 PG 数据导出脚本 (`scripts/migrate_tool_archives_pg_to_local.py`) + 一次跑 | 1d |
| 6 | gateway 删 `tool_archive/` 整目录 + app.py 4 处依赖 | 0.5d |
| 7 | gateway 加 alembic down revision 删 `tool_archives` 表 (1-2 周观察期后) | 0.5d |
| 8 | 单测 + 集成测试 | 2d |
| 9 | 灰度 (鸿波 1d → 50 人内测 1-2d) | 3d |

**总: ~12d (单人 8h 模式), ~2 周 ship + 1 周观察期**.

## 5. 风险

| 风险 | 严重度 | 缓解 |
|---|---|---|
| 老 ref 查不到 (PG 已 drop, 本机没导入) | 高 | Phase 5 导出脚本必须先跑 + 验证 row 数对得上 |
| sqlite 写入并发 (多 hermes 进程同时写) | 中 | WAL mode + busy_timeout=5s, catfish-memory plugin sqlite 同款 |
| 跨 Mac portable 没真测过 | 中 | Phase 9 灰度时刻意拷一份到第二台 Mac 验证 |
| read_tool_archive HTTP 路径还有外部 caller | 低 | grep 验证只有 catfish-tool-bridge 一个 caller, 删 HTTP endpoint 同步 |

## 6. 不在范围

- 不动 `summary_worker.py` 后台 summary 逻辑本身 (跟 archive 解耦, Phase 2 一起搬即可)
- 不动 `image_folder.py` (图片 archive, 跟 text archive 共享 ref 系统, 搬一起)
- 不引新 PG 表 (违 BOUNDARY)
- 不引 catfish-memory plugin 接管 (那 plugin 是 memory 抽象层, tool_archive 是 prompt-time replacement, 性质不同)

---

**Phase 2 启动信号**: 鸿波 review 本 doc → ack → 我开 Phase 2 写 `tool_archive_local.py`.
