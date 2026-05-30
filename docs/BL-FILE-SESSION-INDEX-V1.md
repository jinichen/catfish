# BL-FILE-SESSION-INDEX-V1 — 文件历史检索 + 会话结合

> **状态**: 待立项
> **优先级**: P1 (高)
> **预估工作量**: 2-3 周 (Phase 1 + Phase 2)
> **诊断日期**: 2026-05-30
> **诊断 trigger**: 鸿波"文件历史检索 + 会话结合是不是很弱"

---

## TL;DR

附件 (PDF / Excel / 图片 / 音频) 在鲶鱼的当下会话内能力强 (大文件 BM25 智能取段), 但**附件本身不持久化, 不跨会话**. 跨会话搜的只能搜对话文字, 找历史上传的文件内容这个真实高频场景**做不到**.

建议分两阶段补:
- **Phase 1** (1 周): 附件 metadata 持久化 (state.db, 不存内容)
- **Phase 2** (1-2 周): 跨会话搜附件内容 (复用 BM25 sidecar)

设计原则: **保持"中央 0 红线 / 附件留员工本机"不破**, 所有索引在边缘.

---

## 现状审计 (4 处独立机制, 之间没串)

### 1. session_search — 跨会话搜对话**文字** (LIKE 字面)

**实现**: `edge/tool-bridge/src/catfish_tool_bridge/sessions_search.py`

| 维度 | 现状 |
|---|---|
| 数据源 | `~/.hermes/state.db` (read-only sqlite) |
| 搜什么 | `messages.content` 字段 SQL `LIKE '%query%'` |
| 范围 | 跨 session, 默认过去 30 天 |
| 类型 | **字面匹配, 大小写不敏感, 中文 OK, 不是语义** |
| 覆盖附件 | ❌ **完全不搜附件** |

### 2. 附件 BM25 段落搜索 — **只在当下会话内**

**实现**: `edge/companion-app/src-tauri/src/commands/file_parse.rs` (BL-L26, 5/7)

工作流:
- 用户拖文件入 chat → `parse_file_from_b64` 解析
- 小文件: 全文塞 `previewText`
- 大文件 (≥50KB): 全文写 sidecar `.parsed.txt`, 记 `parsedTextPath`
- 用户发消息时, 对 bm25Targets 跑 `attachment_bm25_search(query, topK=5)`
- 取 top-5 相关段落塞 LLM context

**关键限制**:
- 只覆盖**当下会话**的 attachments
- 切会话 = 附件 + sidecar 状态丢失
- BM25 是单文件内段落搜, 不跨文件

### 3. 附件持久化 — **明确不持久化**

**实现**: `edge/companion-app/src/hooks/useChat.ts:543-558`

```typescript
// 持久化到 state.db: 附件不落库 (图片 base64 / 文件 text 都太大), 只存文字 + 占位
const persistContent = `${trimmed}\n[📄 1 份文档 (合同_v3.pdf) — in-memory, 切会话不保留]`;
```

- state.db 里只有占位字符串
- 切会话 = 附件状态消失 (但物理文件 `keptPath` 还在 `~/.catfish/uploads/`)

### 4. catfish_list_my_outputs — 只列 AI **产出**, 不列**上传**

**实现**: `edge/tool-bridge/src/catfish_tool_bridge/recent_outputs.py`

- 列 `~/.catfish/output/` 下的文件 (AI 写的 docx/xlsx/pdf 等)
- 跨会话能列, 时间倒序
- **不覆盖用户上传的附件**

---

## 真实用户场景 vs 现状

| 场景 | 现在能做? | 失败原因 |
|---|---|---|
| "上次会话里那个客户 X 的 PDF 你看下" | ❌ | 切会话附件状态没了 |
| 搜历史所有上传过的 Excel 里关于客户 Y 的内容 | ❌ | 附件不索引, session_search 也不覆盖 |
| "我们 3 月份讨论的那个合同条款" (合同是 PDF) | ❌ | session_search 只搜对话, 不搜 PDF 内容 |
| 同一 PDF 在不同会话里被引用 = 知道是同一处 | ❌ | 没附件 dedupe |
| 跨会话语义搜 "客户对价格的反应" | ❌ | session_search 字面 LIKE, 找不到 "客户嫌贵" / "客户砍价" |
| 找 "我上次让 AI 改过的那个文档" | 🟡 | `catfish_list_my_outputs` 部分覆盖 (产出方向 OK, 上传找不到) |

---

## 4 个具体 Gap

### Gap 1 · 附件不持久化
- state.db 里只有占位字符串
- 切会话 / 重启 Companion = 附件状态丢
- 物理文件 (`keptPath`) 还在, 但没索引也没 metadata

### Gap 2 · session_search 不覆盖附件
- 即使附件持久化了, search 也只看 messages.content
- 附件文本应该建独立索引

### Gap 3 · 语义搜不存在
- session_search SQL LIKE, 字面匹配
- BM25 sidecar 只针对单附件内, 不跨附件

### Gap 4 · 没有"文件库"概念
- 上传过的 PDF/Excel/录音 没集中存档列表
- 没文件 → 会话反向索引 (此文件在哪些会话被引用)

---

## 为什么会这样 — 设计原因 (trade-off, 不是 bug)

回看 commit 历史:
- 5/7 BL-L26 大文件 BM25 sidecar — 单会话内做了
- 5/13, 5/24 BL-FIX-SESSION-SEARCH — 修了 session_search 跨会话 bug, 但只针对对话 content
- 5/13 BL-FIX-TIMEOUT-OUTPUTS catfish_list_my_outputs — 只覆盖 AI 产出方向

**真因**: 团队精力集中在"对话 content"维度. 附件当成临时附加物处理. 跟"中央 0 红线 + 数据存员工本机"设计**部分冲突** — 附件集中索引必然要落 SQL/向量库, 增加 attack surface. 团队选择了"附件 ephemeral" 简化设计.

**现在反思**: 选择对了一半 — 中央不存附件内容**是对的**. 但**边缘可以也应该索引附件** (员工本机, 不出端, 跟 SOUL/memory 一样原则). 这条没做透.

---

## 设计原则 (必须遵守)

| 原则 | 含义 |
|---|---|
| **中央 0 红线** | catfish-gateway 仍**不存附件内容**. 附件索引全在边缘 (员工本机 SQLite) |
| **物理隔离** | 员工 A 的附件索引在 A 本机, 员工 B 不可见 |
| **可清理** | 附件支持 "全部删除" 操作, 跟 employee_journal 同模式 |
| **零信任** | admin 不能远程 query 员工本机附件索引 (只能员工自己用) |
| **fail loud** | 索引数据库 schema 变化 → migration 失败立刻报, 不静默 |

---

## Proposed Solution — 分 4 个 Phase

### Phase 1 (1 周, P0) · 附件 metadata 持久化

**目标**: state.db 加 `attachments` 表, 存 metadata (不存内容).

**Schema** (新增 state.db 表):

```sql
CREATE TABLE attachments (
    id TEXT PRIMARY KEY,           -- uuid
    session_id TEXT NOT NULL,
    message_id TEXT NOT NULL,
    kind TEXT NOT NULL,            -- 'image' | 'file' | 'audio'
    file_kind TEXT,                -- 'pdf' | 'xlsx' | 'docx' | ...
    name TEXT NOT NULL,
    mime_type TEXT,
    size_bytes INTEGER,
    kept_path TEXT,                -- ~/.catfish/uploads/xxx
    parsed_text_path TEXT,         -- ~/.catfish/uploads/xxx.parsed.txt (BM25 sidecar)
    meta JSON,                     -- transcript_chars / duration / ext / ...
    created_at REAL NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(id),
    FOREIGN KEY (message_id) REFERENCES messages(id)
);

CREATE INDEX idx_attachments_session ON attachments(session_id);
CREATE INDEX idx_attachments_created ON attachments(created_at);
CREATE INDEX idx_attachments_name ON attachments(name);
```

**改动**:
- `useChat.ts:543` 发消息时除占位字符串外, INSERT attachments 表
- 切会话时附件状态从 attachments 表恢复 (不只是占位)
- 物理文件 (kept_path) 已经在了, 不动

**验证**:
- 上传 PDF → 切会话 → 切回 → 附件 chip 仍显示
- LLM 看 message 时仍能看到附件 metadata (LLM-facing 不动)

---

### Phase 2 (1-2 周, P1) · 跨会话搜附件内容

**目标**: 新工具 `catfish_search_attachments` — 语义搜员工所有历史附件.

**实现**:
- 复用 Phase 1 的 attachments 表 + 现有 BM25 sidecar
- 工具调用 → 遍历所有 sidecar (`.parsed.txt`) 做 BM25 → 返 top-K + 关联 session_id

**Schema 增量**: 无 (用 Phase 1 表即可)

**新文件**: `edge/tool-bridge/src/catfish_tool_bridge/attachments_search.py`

```python
def tool_search_attachments(args):
    """跨会话语义搜附件内容.
    args: query, days_back (默认 30), limit (默认 20), file_kind (可选 filter)
    返: [{attachment_id, name, session_id, session_title, snippet, score, ...}]
    """
```

**LLM 使用场景**:
- 用户问 "上次客户 X 的合同里说啥" → LLM 调 `search_attachments(query="客户 X")` → 拿到附件 + session_id
- LLM 决定要不要再调 `read_attachment(id)` 看全文

---

### Phase 3 (1 周, P2) · 文件 → 会话反向索引

**目标**: 新工具 `catfish_list_my_attachments` — 列员工所有附件 + 关联会话.

**实现**: Phase 1 表的 query, 按 name / file_kind / created_at 排.

**返**:
```
{
  attachments: [
    {name, file_kind, size, mtime, sessions: [{id, title}], reference_count}
  ]
}
```

**LLM 使用场景**:
- 用户 "我上传过的所有 Excel" → LLM 调 `list_my_attachments(file_kind='xlsx')`
- 反向: 某文件在多个会话被引用 → 一目了然

---

### Phase 4 (3-4 周, P3, optional) · 向量 embedding 长期记忆

**目标**: 附件 chunk → embedding → 语义搜跨附件.

**为什么放后**:
- 工程复杂 (需选 embedding model / vector DB / chunking 策略)
- 合规上要看 embedding 算不算"附件副本" (理论上是有损压缩, 但内容仍可重构部分)
- Phase 1-3 + BM25 已经覆盖 80% 真实场景

**先做 Phase 1-3, Phase 4 看实际反馈**.

---

## 影响范围

| 文件 | 改动类型 |
|---|---|
| `edge/companion-app/src/hooks/useChat.ts` | 发消息时 INSERT attachments 表 |
| `edge/companion-app/src-tauri/src/services/session_*.rs` | 切换 session 时 SELECT attachments 恢复 |
| `edge/companion-app/src-tauri/migrations/*.sql` | 新 attachments 表 + index |
| `edge/tool-bridge/src/catfish_tool_bridge/attachments_search.py` | 新文件 (Phase 2) |
| `edge/tool-bridge/src/catfish_tool_bridge/list_attachments.py` | 新文件 (Phase 3) |
| `edge/tool-bridge/src/catfish_tool_bridge/catfish_tool_schemas.py` | 注册 2 个新 tool |
| `edge/tool-bridge/src/catfish_tool_bridge/catfish_tools.py` | 路由 2 个新 tool |
| `edge/tool-bridge/tests/test_attachments_search.py` | 新文件 |

---

## 风险与开放问题

| 风险 | 缓解 |
|---|---|
| state.db schema migration 出问题, 老用户数据丢 | Alembic-style migration + 升级前自动备份 state.db |
| 附件物理文件被员工手动删但 metadata 还在 | 定期 cleanup job (检查 kept_path 是否存在, 不存在标记 stale) |
| 附件文件名含敏感信息 (客户名/项目代号), 索引会让 grep 历史命令也看见 | metadata 表本身就归员工本机, 跟其它员工数据同保护 |
| BM25 sidecar 跨 100+ 附件查询性能 | Phase 2 先测 50/100/200 附件性能, 慢就分页 / 加 cache |
| 客户敏感数据通过附件流入员工本机, 长期累积大 | 数据保留策略 (TTL 自动归档老附件), 跟 Phase 4 一起规划 |

---

## 开放问题 (设计时定)

1. **音频附件怎么处理**? 已经转录成 `previewText` 文字了, 也建 BM25 sidecar 吗?
2. **图片附件怎么处理**? 不是文本, BM25 不适用. 要不要做 OCR + index?
3. **多个会话引用同一文件** (kept_path 相同) → 算多个 attachment 记录还是关联?
4. **文件改名了 / 移动了** → metadata 表怎么更新?
5. **企业管理员视角**? admin 是否需要"看到所有员工上传过哪些类型文件 (不看内容)" 的能力? 跟"零信任"原则冲突, 要不要破例?

---

## 决策

待 鸿波 拍板:
- [ ] Phase 1 立项 (1 周, 必做)
- [ ] Phase 2 立项 (1-2 周, 必做)
- [ ] Phase 3 立项 (1 周, 看 Phase 2 用户反馈)
- [ ] Phase 4 立项 (3-4 周, 长期 backlog)

如果只做 Phase 1+2 = **2-3 周工作量, 解决 80% 的"跨会话找附件"场景**.

---

## 同步链路

- 客户提案 PPT 里如果讲到"跨会话语义搜索 / 文件历史" 注意不要 oversell — 当前真能力只是对话文字 LIKE 搜
- 内部讲稿 (招新) 可以提到 "这块是 backlog, 招进来可以做"
- catfish/docs/FEATURE-TRACKS.md 加一条 track 指向本 ticket

---

*作者: 鸿波 + Claude (Cowork)*
*BL-FILE-SESSION-INDEX-V1*
*创建于: 2026-05-30*
