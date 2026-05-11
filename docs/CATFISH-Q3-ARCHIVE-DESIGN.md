# BL-Q3-ARCHIVE — tool message archive + 摘要双层设计

**作者**: 鸿波 + 鲶鱼
**起稿**: 2026-05-11 (5/14 demo 前夜, BL-FIX41 硬切已落地)
**评审**: 2026-05-12
**实施**: 2026-05-15 ~ 2026-05-22
**Ship**: 2026-05-22 灰度, 2026-05-29 全量

---

## 0. 一句话

把 tool message 超 4KB 的内容**完整存档到 PG (lossless)**, prompt 里替换成
**头 500B + 尾 500B + haiku 摘要 + archive_ref**, LLM 看到 ref 自行判断要不要调
新工具 `catfish_read_tool_archive` 拿原文片段. 替代 BL-FIX41 硬切, 实现零信息
丢失.

---

## 1. 为啥要做 — BL-FIX41 局限

### 1.1 现状链路

```
5/11 鸿波 demo 前夜 mac 实测 (装了 FIX23 L6 + FIX40 + FIX41):

BL-FIX2 pre-unwrap: total=199, tool_msgs=63
BL-FIX41 truncate_tool_messages: 截断 8 条, 省 144KB (~36K tokens)
context overflow: prompt_tokens=78043 (61%)  ✅
```

FIX41 硬切扛住 demo 没问题. 但**砍掉的中段内容是永久丢的** — LLM 再也拿不到.

### 1.2 硬切丢中段的真实痛点

| 工具          | 头 1KB           | 尾 1KB                    | 丢掉的中段 (常见)             |
|---------------|------------------|--------------------------|----------------------------|
| pytest        | session start    | PASSED/FAILED + 总结      | 每个 test 的逐行输出, 中间 traceback |
| browser_snapshot | 页面 title + 顶部 | 底部交互元素              | 中段表格行, 列表项, 表单字段 |
| execute_code  | print 头         | exit code + stderr 尾    | 中间步骤 print, 数据 dump   |
| file_read     | 文件头           | 文件尾                    | **代码中段, 段落中段 — 最致命** |
| search_results | top-3 命中       | 总数 + 分页              | 中段 hits (相关性次高)      |

file_read 是真痛点 — 用户问"刚才那个文件第 80 行 logic 怎么写的", LLM 没那段就
瞎编, 比报错还危险.

### 1.3 备选方案对比 (再写一遍 pin 死)

| 维度         | FIX41 硬切     | LLM 摘要直接替换 | **Q3 archive 双层** |
|--------------|---------------|----------------|--------------------|
| 信息保留     | 头尾保, 中段丢 | 有损 + 幻觉风险 | **0 损失** ✅     |
| 写入成本     | 0             | 1 LLM 调用/条   | 1 磁盘写 (μs)     |
| 读取成本     | 0             | 0              | LLM 主动 read 1 次 |
| 幻觉风险     | 无             | **高** ⚠      | 无 (摘要做索引, 原文兜底) |
| 调试可追溯   | 截掉的看不到   | 摘要是黑盒      | archive 持久化, 14 天可查 |
| 实施复杂度   | 已 done       | 中             | 中-高             |
| 上云演进     | 不演进         | 不演进          | PG → S3 平滑迁移   |

archive 比 LLM-only 摘要好在 **lossless** — 摘要错了, 原文还在; LLM 摘要直接
替换原文, 错了就是真错了, 没机会兜底.

---

## 2. 目标 / 非目标

### 2.1 目标

1. **零信息丢失** — 任何 tool message 内容, 14 天内可完整召回.
2. **写入低开销** — 不阻塞 chat 主链路, 不增加首字延迟.
3. **读取按需** — 大部分 case LLM 看头尾 + 摘要就够, 不动磁盘 / 不调 LLM.
4. **LLM 透明** — LLM 看到 `archive_ref://abc12345` 知道还有更多, 不会假装看过.
5. **兼容现状** — 跟 FIX41 硬切共存 (灰度), Hermes ReAct chain / tool_call_id
   / OpenAI tool-calling 协议都不变.
6. **可演进** — 上云时 PG content 列迁 S3, 业务代码不动.

### 2.2 非目标 (这版不做)

- ❌ 跨 session 知识检索 (这是 RAG 范畴, 不是 archive)
- ❌ 全文检索 (现在 grep 子串够用, 真要 FTS 走 PG `tsvector`)
- ❌ 向量召回 (Q4 GRAPH 再说)
- ❌ 自动调 read tool — LLM 自己决定, gateway 不强插
- ❌ 改 employee_journal — journal 走 FIX40, 不混

---

## 3. 总体架构

```
┌──────────────────────────────────────────────────────────────────┐
│ Companion / Cursor                                                │
│   tool_call → tool_result                                         │
└────────────────────────────────┬──────────────────────────────────┘
                                 │ POST /v1/chat/completions
                                 ↓
┌──────────────────────────────────────────────────────────────────┐
│ Gateway (catfish_gateway.app.chat_completions)                    │
│                                                                   │
│  ① BL-FIX2 unwrap_tool_images (含图重组成 user multipart)         │
│  ② BL-Q3-ARCHIVE tool_archiver (替代 FIX41 truncate)              │
│      - tool message content > 4KB ?                              │
│        no → 原样过                                                │
│        yes → 写 archive + 替换为 ref + 头尾预览 + (待摘要)        │
│  ③ 后续 (route, sanitize, quota, LLM call)                        │
└────────────────┬─────────────────────────────────────────────────┘
                 │ async (不阻塞主请求)
                 ↓
┌──────────────────────────────────────────────────────────────────┐
│ summary_worker (asyncio 后台 task)                                │
│   - 每 5s 扫一次 tool_archives WHERE summary IS NULL              │
│   - pick_internal_model("tool_summarizer") (haiku tier)           │
│   - 写回 summary / summary_model / summary_at                     │
└──────────────────────────────────────────────────────────────────┘

LLM 推理时若要中段:
┌──────────────────────────────────────────────────────────────────┐
│ catfish_read_tool_archive(ref, line_range / grep)                 │
│   ↓ 走 gateway 内部 router (不绕 Companion / tool-bridge)         │
│ Gateway 内置 handler:                                             │
│   - SELECT content FROM tool_archives WHERE ref = ?               │
│   - 按 line_range 切 / 按 grep 召回 ± 5 行                        │
│   - 返字符串                                                       │
└──────────────────────────────────────────────────────────────────┘

GC: pg_gc.py cron, DELETE WHERE expires_at < NOW(), 每天 03:00.
```

---

## 4. 数据模型

### 4.1 PG schema (新 alembic migration)

```sql
-- 20260515_001_tool_archives.py
CREATE TABLE tool_archives (
    ref            VARCHAR(16) PRIMARY KEY,    -- sha256(content + tool_call_id)[:16]
    session_id     VARCHAR(64) NOT NULL,        -- (user_email, conversation_id) 派生
    user_email     VARCHAR(255) NOT NULL,       -- 用户隔离, 鉴权
    tool_call_id   VARCHAR(128),
    tool_name      VARCHAR(128),                -- execute_code / browser_snapshot / ...
    content        TEXT NOT NULL,               -- 全文 (5-100KB 量级)
    content_bytes  INTEGER NOT NULL,
    lines          INTEGER NOT NULL,
    summary        TEXT,                        -- haiku 摘要, 后台填
    summary_model  VARCHAR(64),                 -- haiku-4-5 / haiku-3-5 / ...
    summary_at     TIMESTAMP,
    summary_error  TEXT,                        -- 摘要失败原因 (可观测)
    created_at     TIMESTAMP DEFAULT NOW(),
    expires_at     TIMESTAMP DEFAULT (NOW() + INTERVAL '14 days')
);

CREATE INDEX ix_tool_archives_session ON tool_archives(session_id);
CREATE INDEX ix_tool_archives_user ON tool_archives(user_email);
CREATE INDEX ix_tool_archives_expires ON tool_archives(expires_at);
CREATE INDEX ix_tool_archives_summary_null ON tool_archives(created_at)
    WHERE summary IS NULL;  -- summary worker 扫表用
```

### 4.2 jsonl fallback

PG 挂时降级写 `~/.catfish/tool_archives/<session_id>/<ref>.json`:

```json
{
  "ref": "abc12345...",
  "session_id": "chenhongbo@ffcs.cn:conv_xyz",
  "user_email": "chenhongbo@ffcs.cn",
  "tool_call_id": "call_abc",
  "tool_name": "execute_code",
  "content_bytes": 12453,
  "lines": 287,
  "content": "...",
  "summary": null,
  "created_at": "2026-05-15T13:45:00Z",
  "expires_at": "2026-05-29T13:45:00Z"
}
```

跟 mcp-registry / skills-hub / facts_db 同模板 (PG 主 + jsonl 备), 双写.

### 4.3 session_id 派生规则

```python
def derive_session_id(user_email: str, conversation_id: str | None) -> str:
    if conversation_id:
        return f"{user_email}:{conversation_id}"
    # 没有 conversation_id 时按当天 + user 兜底 (Companion 每天一个隐式会话)
    return f"{user_email}:{datetime.utcnow().date().isoformat()}"
```

跟 employee_journal / audit 已有的 session 概念对齐. Companion 4.30 后已经传
`conversation_id` header, 直接拿.

---

## 5. tool_archiver 写流程

### 5.1 模块结构

```
central/llm-gateway/src/catfish_gateway/tool_archive/
├── __init__.py
├── archiver.py          # 主流程: archive / replace_in_prompt
├── db.py                # PG CRUD + jsonl fallback (跟 facts_db.py 同模板)
├── reader.py            # catfish_read_tool_archive 工具实现
├── summary_worker.py    # 后台 haiku 摘要
└── prompts.py           # 摘要 prompt + 替换文本模板
```

### 5.2 archiver.py 核心 API

```python
THRESHOLD_BYTES = 4_000           # 触发 archive 的下限
PREVIEW_HEAD_BYTES = 500
PREVIEW_TAIL_BYTES = 500

def archive_tool_messages(
    messages: list[dict],
    *,
    user_email: str,
    session_id: str,
) -> list[dict]:
    """对 role=tool content > THRESHOLD_BYTES 的, 写 archive + 替换 content.

    保证:
    - 消息数量不变 (Hermes / OpenAI 协议要求)
    - tool_call_id 不变 (assistant ↔ tool 配对)
    - content 类型保持 str (LLM 期望)
    """
    for i, m in enumerate(messages):
        if not _is_archive_candidate(m):
            continue
        content = m["content"]
        if len(content.encode("utf-8")) < THRESHOLD_BYTES:
            continue

        ref = _compute_ref(content, m.get("tool_call_id"))
        # 异步写 (不阻塞), 用 fire-and-forget — archiver 自己管失败重试
        asyncio.create_task(_write_archive_async(
            ref=ref, content=content, ...
        ))

        messages[i]["content"] = _build_replacement_text(
            ref=ref, content=content, tool_name=m.get("name"),
        )
    return messages
```

### 5.3 替换文本格式 (LLM 看到的)

```text
[已归档: archive_ref=abc12345f9d8e7c6, tool=execute_code, 12.4KB / 287 行]

📝 摘要 (haiku): pytest 跑 13 个测试, 12 pass, test_quota_overrun 在第 47 行
   KeyError: 'price' 触发. 总时长 2.3s.

📂 头部 (前 500B):
> ============================= test session starts ===============================
> platform darwin -- Python 3.11.6, pytest-8.4.0, pluggy-1.5.0
> rootdir: /Users/chenhongbo/person_task/catfish/central/llm-gateway
> plugins: anyio-4.6.0, asyncio-1.0.0
> collected 13 items
>
> tests/test_quota.py::test_basic_check PASSED                     [  7%]
> tests/test_quota.py::test_per_model PASSED                       [ 15%]
> ...

📂 尾部 (后 500B):
> ...
>     def _lookup_price(self, key):
> >       return self.price_map[key]
> E       KeyError: 'price'
>
> central/llm-gateway/src/catfish_gateway/quota.py:128: KeyError
> =========================== 1 failed, 12 passed in 2.34s ============================

💡 看不全? 调 catfish_read_tool_archive(ref="abc12345f9d8e7c6", grep="KeyError")
   或 (ref, line_range="40-60") 拿上下文.
```

如果摘要还没生成 (summary_worker 还没跑到), 摘要行替换成:

```text
📝 摘要 (生成中…): 该 tool result 较大, 摘要后台异步生成. 头尾预览已贴, 不全请直接调 read tool.
```

### 5.4 ref 生成

```python
def _compute_ref(content: str, tool_call_id: str | None) -> str:
    """ref = sha256(content + ":" + tool_call_id)[:16]

    选 16 字 (= 64 bit) 碰撞概率: 1B archive 撞概率 < 1e-9, 够用.
    含 tool_call_id 避免不同调用同 content 撞 ref.
    """
    h = hashlib.sha256()
    h.update(content.encode("utf-8"))
    h.update(b":")
    h.update((tool_call_id or "").encode("utf-8"))
    return h.hexdigest()[:16]
```

幂等: 同一条 tool message 多次 archive 写同 ref, PG `ON CONFLICT (ref) DO NOTHING`.

---

## 6. summary_worker 摘要流程

### 6.1 主循环

```python
async def summary_worker_loop():
    """每 5s 扫一次 summary IS NULL 的 archive, 起 haiku 摘要."""
    while True:
        try:
            rows = await pg_pick_unsummarized(limit=10)  # 一次 10 条避免雪崩
            for r in rows:
                try:
                    summary = await _summarize(r["content"], r["tool_name"])
                    await pg_update_summary(r["ref"], summary=summary,
                                             model="haiku-4-5")
                except Exception as e:
                    logger.warning("summary 失败 ref=%s err=%s", r["ref"], e)
                    await pg_update_summary(r["ref"], summary=None,
                                             error=str(e)[:200])
        except Exception as e:
            logger.error("summary_worker_loop err: %s", e)
        await asyncio.sleep(5)
```

启动: app.py `lifespan` 里 `asyncio.create_task(summary_worker_loop())`.

### 6.2 摘要 prompt (反幻觉, 保关键 fact)

```text
SYSTEM:
你是 tool output 摘要器. 输入一段 LLM 工具执行结果 (代码运行 / 浏览器快照 /
文件读取等), 输出**简短中文摘要 (50-120 字)**, 用于让另一个 LLM 快速判断
"要不要看全文". 严格遵守:

✅ 必须包含 (按优先级):
1. 执行结果 (成功 / 失败 / 部分)
2. 关键数字 (count / size / 用时 / 行数)
3. 报错信息**最后一行** (含 exception 类型 + 值)
4. 关键文件路径 / URL
5. 主要操作类型 (跑 N 个 test / 截了一个页面 / 读了 K 行代码)

❌ 严禁:
1. 凭空推断 / 加未在原文出现的事实
2. 复述命令 / 输出格式 (用户已经看到了)
3. 客套话 ("以下是..." / "总结如下" / "希望有帮助")
4. 推测原因 (除非原文里说了)
5. markdown 格式 (输出纯文本)

USER:
工具: {tool_name}
内容 ({content_bytes} 字节, {lines} 行):
---
{content}
---
摘要:
```

模型: `pick_internal_model("tool_summarizer")` → tier=private (走我们的本地
haiku 4-5 / 等价模型, 不消耗用户配额).

### 6.3 长度上限保护

```python
MAX_SUMMARIZE_INPUT_BYTES = 50_000  # 100K 以上的不全喂, 头 25K + 尾 25K
```

避免 archive 巨大 (300KB+) 时摘要器自己撑爆 context.

### 6.4 失败兜底

- 重试 3 次 (指数退避 1s/5s/15s)
- 都失败 → 写 `summary_error` + `summary=None`
- prompt 替换文本走 "摘要不可用" 分支

```text
📝 摘要 (生成失败): 头尾预览已贴, 直接调 read tool 看全文.
```

---

## 7. catfish_read_tool_archive 工具

### 7.1 注册

通过 mcp-registry 注册成内置工具 (跟 catfish_save_skill / catfish_user_profile_propose
同款), 不走外部 MCP server.

### 7.2 工具 schema

```json
{
  "name": "catfish_read_tool_archive",
  "description": "读取已归档的 tool output 内容. 当 prompt 里看到 [已归档: archive_ref=...] 且任务相关 (debug / 引用具体数字 / 复盘) 时调用. 支持三种模式: 全文(慎用), 按行号片段, 按 grep 关键字召回上下文.",
  "parameters": {
    "type": "object",
    "properties": {
      "ref": {"type": "string", "description": "archive 引用, 形如 abc12345f9d8e7c6"},
      "line_range": {"type": "string", "description": "可选, 行号范围, 形如 '50-120' 或 '47'"},
      "grep": {"type": "string", "description": "可选, 关键字, 召回匹配行 ± 5 行上下文"},
      "max_bytes": {"type": "integer", "description": "可选, 返回上限, 默认 8000, 防过载"}
    },
    "required": ["ref"]
  }
}
```

### 7.3 路由

gateway 内部 `tool_archive_router.py` 接管 (类似 mcp_registry_router):

```python
@router.post("/v1/tool-archives/read")
async def read_archive(req: ReadArchiveReq, user: User = Depends(get_user)):
    row = await pg_get_archive(req.ref)
    if not row:
        raise HTTPException(404, "archive not found / 已过期")
    if row["user_email"] != user.email and user.role not in ("admin", "sysadmin"):
        raise HTTPException(403, "不能读别人的 archive")

    content = row["content"]
    if req.grep:
        result = _grep_with_context(content, req.grep, ctx_lines=5)
    elif req.line_range:
        result = _slice_lines(content, req.line_range)
    else:
        result = content[: req.max_bytes or 8000]
    return {"content": result, "total_lines": row["lines"], "total_bytes": row["content_bytes"]}
```

### 7.4 工具执行路径

```
LLM 在 ReAct 链中决定调 catfish_read_tool_archive(ref="abc", grep="KeyError")
  ↓
Companion tool-bridge 收到 → 本地无 handler → 走 gateway loopback
  ↓
gateway 自己处理 (没绕 Companion, 没绕 tool-bridge), 直接 PG 查
  ↓
返回字符串 → 作为 tool message content 接回 LLM context
  ↓
LLM 继续推理
```

这个 loopback 模式跟 `catfish_save_skill` / `catfish_user_profile_propose`
已建立, 沿用就行.

---

## 8. prompt 替换格式 (再展开一下)

### 8.1 完整版 (摘要 ready)

```text
[已归档: archive_ref=abc12345f9d8e7c6, tool=execute_code, 12.4KB / 287 行]

📝 摘要 (haiku): pytest 跑 13 个 test, 12 pass, test_quota_overrun 在 quota.py:128 触发 KeyError: 'price'. 总时长 2.3s.

📂 头部:
> {content[:500]}

📂 尾部:
> {content[-500:]}

💡 看不全? catfish_read_tool_archive(ref="abc12345f9d8e7c6", grep="KeyError")
```

### 8.2 摘要中

```text
[已归档: archive_ref=abc12345f9d8e7c6, tool=execute_code, 12.4KB / 287 行]

📝 摘要 (生成中…)

📂 头部:
> {content[:500]}

📂 尾部:
> {content[-500:]}

💡 看不全直接调: catfish_read_tool_archive(ref="abc12345f9d8e7c6", ...)
```

### 8.3 摘要失败

```text
[已归档: archive_ref=abc12345f9d8e7c6, tool=execute_code, 12.4KB / 287 行]

📝 摘要 (生成失败, 看头尾或直接 read)

📂 头部:
> {content[:500]}

📂 尾部:
> {content[-500:]}

💡 catfish_read_tool_archive(ref="abc12345f9d8e7c6", grep="...")
```

### 8.4 token 占用对比

| 模式            | 单条占用    | 50 条总占用 | 备注                  |
|----------------|------------|------------|----------------------|
| 原文 (无处理)   | 12KB        | 600KB       | 必然 overflow         |
| FIX41 硬切      | 2KB         | 100KB       | 头尾保, 中段丢        |
| **Q3 archive**  | **~1.5KB**  | **75KB**    | 摘要 + 头尾 + ref     |

archive 模式比硬切**还省 25% token** (摘要替代了一半头尾), 且 lossless.

---

## 9. LLM 教育 — SOUL.md 新段

加到 `companion/src/soul/SOUL.md` 主章节:

```markdown
## 看到 [已归档: archive_ref=...] 时怎么办

gateway 把超过 4KB 的 tool result 自动归档到 PG, prompt 里显示成:

  [已归档: archive_ref=XXX, tool=YYY, 12.4KB / 287 行]
  📝 摘要 (haiku): ...
  📂 头部 / 尾部预览

**铁律**:

1. **不要假装看过全文**. 你看到的是摘要 + 头尾 1KB, 中段 10KB 在 PG 里. 凭空
   编中段内容是幻觉, 会被发现.

2. **任务相关一定要调 read**. 以下情形必须调 `catfish_read_tool_archive`:
   - 用户问"刚才那个 X 在哪行 / 长什么样"
   - debug — 要看完整堆栈 / 中段 print
   - 引用具体数字 / 段落 — 不能只看头尾
   - 复盘 / 总结 — 要原文支撑

3. **任务无关跳过**. 头尾 + 摘要够判断"那次 pytest 全过了" 就不用 read.

4. **read 时用 grep / line_range**, 不要盲拉全文 (大文件直接撑 context).
   - `grep="KeyError"` — 关键字召回 + 上下文 5 行
   - `line_range="40-80"` — 按行号片段

5. 调 read 时也要诚实 — 如果 ref 找不到 (过期 / 别人的), 跟用户说"那条 tool
   result 已归档过期 (14 天), 我看不到完整内容了", 不要瞎编.
```

也写一段到 `internal_models.py` 的 `KNOWN_USE_CASES`:

```python
"tool_summarizer": {
    "tier": "private",  # 用我们自己的 haiku, 不动用户配额
    "models": ["haiku-4-5", "haiku-3-5", "qwen-flash"],
    ...
}
```

---

## 10. 灰度 / 兜底策略

### 10.1 灰度开关

```yaml
# central/llm-gateway/config/features.yaml
tool_archive:
  enabled: false                 # 5/22 ship 时 false, 5/25 灰度 chenhongbo, 5/29 全量
  threshold_bytes: 4000
  preview_head_bytes: 500
  preview_tail_bytes: 500
  summary_enabled: true          # 摘要可单独关 (调试用)
  retention_days: 14
  fallback_to_fix41: true        # archive 写挂时降级走 FIX41 硬切
```

### 10.2 共存逻辑

```python
def prepare_tool_messages(messages, *, user_email, session_id):
    if not feature_enabled("tool_archive", user=user_email):
        # FIX41 路径
        return truncate_tool_messages(messages)

    try:
        return archive_tool_messages(messages, user_email=user_email,
                                       session_id=session_id)
    except Exception as e:
        logger.warning("tool_archive 失败, 降级 FIX41: %s", e)
        if feature_enabled("tool_archive.fallback_to_fix41"):
            return truncate_tool_messages(messages)
        raise  # 没兜底就抛
```

### 10.3 灰度计划

| 阶段           | 日期        | enabled 用户                     | 监控指标                  |
|---------------|------------|----------------------------------|--------------------------|
| Ship          | 5/22       | 关 (代码 ship 不开)               | 部署成功                  |
| 灰度 1        | 5/23-5/24  | chenhongbo (sysadmin)            | archive 写率, 摘要成功率   |
| 灰度 2        | 5/25-5/27  | dev 部门 (5 人)                   | LLM read 调用率, context % |
| 全量          | 5/29       | 全公司                            | 同上                      |

### 10.4 回滚

```bash
# 配置开关闭合 (秒级)
sed -i '' 's/enabled: true/enabled: false/' config/features.yaml
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.catfish.gateway.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.catfish.gateway.plist
# 立即切回 FIX41 硬切, archive 数据保留 (后续可重新打开)
```

---

## 11. 运维 / 监控 / GC

### 11.1 GC

```python
# scripts/tool_archives_gc.py — 每天 03:00 cron
DELETE FROM tool_archives WHERE expires_at < NOW() RETURNING ref;
```

预计 100 用户 × 10 archive/天 × 14 天 ≈ 14K rows + 50KB/row ≈ 700MB 上限.
PG 量级毫无压力.

### 11.2 Prometheus 指标

```
catfish_tool_archive_writes_total{tool_name="..."}        counter
catfish_tool_archive_write_bytes_total                    counter
catfish_tool_archive_summary_pending                      gauge  (待摘要数)
catfish_tool_archive_summary_success_total                counter
catfish_tool_archive_summary_failed_total                 counter
catfish_tool_archive_summary_latency_seconds              histogram
catfish_tool_archive_reads_total{mode="grep|line|full"}   counter
catfish_tool_archive_read_not_found_total                 counter
catfish_tool_archive_fallback_fix41_total                 counter
```

### 11.3 报警阈值

- 摘要失败率 > 5% (10 分钟窗口) → 邮件鸿波
- summary_pending > 100 (5 分钟) → 摘要 worker 卡了 / 模型挂了
- archive 写失败率 > 1% → PG 异常
- read_not_found 突增 (10x baseline) → SOUL 教育失效 / ref 格式错

### 11.4 admin/facts 风格的 UI (Q3 P1)

`/admin/archives` 页面 (catfish-web):
- 我的 archive 列表 (按时间倒序)
- 单条详情: 摘要 + 全文 + 调过我多少次
- 手动删除 (隐私场景)
- sysadmin 看全公司

P0 ship 时**不做 UI**, 只做后端 + LLM 工具. UI 5/29 之后再说.

---

## 12. 测试计划

### 12.1 单测

| 模块              | 测试点                                           | 行数 |
|------------------|-------------------------------------------------|------|
| archiver.py      | 阈值触发 / 不触发, 消息数不变, ref 幂等, 替换格式 | ~150 |
| db.py            | PG CRUD, jsonl fallback, 过期清除                | ~100 |
| reader.py        | 全文 / line_range / grep 三种模式, 鉴权          | ~120 |
| summary_worker   | 摘要 prompt 输出格式, 失败重试, 跳过已摘要        | ~80  |
| prompts.py       | 替换文本 3 种态 (ready / pending / failed)        | ~60  |

### 12.2 集成测试

```bash
# tests/integration/test_tool_archive_e2e.py
# 1. 模拟 199 messages / 63 tool_msgs (跟 BL-FIX41 同源 case)
# 2. archive_tool_messages 跑完, 验证:
#    - 消息数不变
#    - 8 条 > 4KB 的被替换成 ref + 头尾
#    - PG 里 8 条 archive 写入成功
# 3. 起 summary_worker, 等 10s, 验证 8 条摘要 ready
# 4. 模拟 LLM 调 read_tool_archive(ref, grep="error"), 拿到上下文
# 5. 验证 token 占用从 600KB → 75KB
```

### 12.3 灰度阶段实测指标

| 指标                  | 目标                          |
|----------------------|------------------------------|
| context overflow 率   | < 5% (基准 FIX41 已 < 10%)    |
| archive 写 P99 延迟   | < 50ms                       |
| 摘要 P99 延迟         | < 5s                         |
| LLM read 调用率       | 5-30% archive (太低=没用, 太高=过载) |
| read_not_found 率    | < 1%                         |

---

## 13. 工作量明细 (5/15 - 5/22)

| Day | 任务                                                    | 行数  |
|-----|--------------------------------------------------------|------|
| 5/15 (周五) | alembic migration + db.py + 单测                       | ~250 |
| 5/16 (周六) | archiver.py + prompts.py + 单测                        | ~280 |
| 5/17 (周日) | summary_worker.py + internal_models tool_summarizer    | ~250 |
| 5/18 (周一) | reader.py + tool_archive_router.py + 注册到 mcp-registry | ~280 |
| 5/19 (周二) | 接入 app.py prepare_tool_messages + 灰度开关 + 集成测试 | ~150 |
| 5/20 (周三) | SOUL.md 段 + 文档 + 监控指标 + features.yaml              | ~100 |
| 5/21 (周四) | sync 脚本 + 跑全套测试 + push                            | -    |
| 5/22 (周五) | mac 部署 + 灰度 chenhongbo + 监控                        | -    |

**总计**: ~1310 行 + 灰度. 跟前面估的 4-5 天对齐.

---

## 14. 风险 / 未解决问题

### 14.1 LLM 不主动 read

**最大风险**. 我们能做的:
- SOUL.md 强 trigger 教育
- 摘要里加 "💡 看不全调 read" 提示
- 摘要 prompt 强调"必须保留关键 fact 让另一个 LLM 决定要不要 read"
- 真实灰度时埋点统计调 read 率, 调到 < 5% 说明 SOUL 教育不够, 加 in-context
  hint 强化 (chat 末尾 system 加一句 "如果引用了 archive, 必须 read")

### 14.2 摘要器自己幻觉

haiku 也可能编. 缓解:
- prompt 严格 "✅ 必须包含 / ❌ 严禁" 二分
- 摘要后做 raw_quote 校验 (跟 FACT pipeline 同款) — 摘要里出现的数字必须能在
  原文 grep 到
- 第一周拿 100 条真实 archive 人工校对, 改 prompt

### 14.3 PG 量级

100 用户 × 14 天 × 50KB/archive × 平均 10 个/天 = 700MB. 单台 PG 没事.
1000 用户就是 7GB, 要考虑 archive 表分区 / S3 迁移. 100 用户内不操心.

### 14.4 ref 撞 (理论)

sha256 前 16 字 (64 bit) 撞概率 1B archive 才 < 1e-9. 100 用户 14 天最多 14K
archive, 撞概率 ~ 1e-15, 不操心.

### 14.5 跟 employee_journal 重叠

journal 是"长期记忆", archive 是"短期 tool 缓存". 两个不冲突:
- journal (FIX40): prompt 顶部, 跨 session, 15KB 注入上限
- archive (本 Q3): prompt 中部 (tool messages), 单 session, 头尾 + 摘要

后续可能联动 — 重要 archive 摘要进 journal, 但本期不做.

### 14.6 fallback 链断 (PG 挂 + jsonl 也写失败)

最后兜底 → 走 FIX41 硬切. 用户感知"信息不全"但请求不挂. 加监控.

---

## 15. FAQ

**Q1**: 为啥不直接 LLM 摘要替换原文? 简单一倍.

A: 摘要会幻觉, 还不可逆 — 原文就这么没了. archive lossless 兜底, 摘要错了
原文还在 PG 里, LLM 能 read 自查. 工程多 50%, 风险降 80%.

**Q2**: 为啥 4KB 阈值不是 2KB?

A: FIX41 用 2KB 是因为它砍中段, 2KB 太小则头尾各 1KB 信息量太低. archive 模式
预览只占 1KB (头尾各 500B) + 摘要 ~200B + ref + 模板 ~200B ≈ 1.5KB. 阈值 4KB
保证 archive 后真省 token (1.5KB < 4KB), 2KB 的不归档也无所谓 (反正全显).

**Q3**: 为啥 ref 是 16 字 sha256 不是 UUID?

A: ref 要塞进 prompt 给 LLM 看, 短 + 可读 + 内容寻址 (相同内容同 ref, 幂等
天然). UUID 32 字太长占 token, 又看不出语义.

**Q4**: 上云时 archive 怎么办?

A: PG schema 不变, content 列改存 S3 URL (s3://catfish-archives/<ref>.txt),
db.py 加一层 _resolve_content() 自动 S3 拉. 业务代码不动.

**Q5**: 跟 BL-FIX41 是替代关系?

A: 是. archive ship + 灰度全量后, FIX41 默认关 (`tool_archive.enabled=true`
就走 archive 不走硬切). 但代码保留作为 fallback (PG 挂时降级).

**Q6**: archive 跟 Claude memory tool / Claude Code 的 todo 写盘有可比性?

A: 思路同源 — 把大状态 offload 到磁盘, prompt 里只放引用. Anthropic computer
use 也是这模式 (screenshot 走 disk cache, prompt 里只放压缩缩略图). 业内成熟
模式, 不是我们首创.

**Q7**: 用户隐私? 我读了一份机密 word, archive 在 PG 里 14 天?

A: 这是真问题. 缓解:
1. 数据库本身有访问审计 (跟 quota_events 同表权限)
2. user 自己看 admin/archives UI 可手动删 (Q3 P1)
3. 文档加段 "不要让助手读密级文件, 或读完后立即清理"
4. retention 14 天 配置化, 高敏部门可调 3 天

长期看应该有"敏感内容标记 → 不 archive / 短 retention". Q4 加.

**Q8**: 跟 Hermes ReAct chain 兼容吗?

A: 兼容. 我们改的只是 content 文本, message 数量 / role / tool_call_id 都不动.
Hermes 见到 archive_ref 当成普通字符串读 + 解析摘要 + 决定要不要 read tool.

**Q9**: 为啥不放向量库做 RAG 召回?

A: 现在 grep 子串 + line_range 在小 archive (<100KB) 上够用, 加向量库一倍工程,
首期不上. Q4 GRAPH 阶段再说.

**Q10**: 测试覆盖率目标?

A: 80%. 重点覆盖:
- 阈值边界 (3999 / 4000 / 4001 字节)
- 消息数不变性
- ref 幂等 / 碰撞
- 鉴权 (跨用户 read 拒绝)
- jsonl fallback (PG 假装挂)
- 摘要 worker 失败重试

---

## 16. 决策日志 (Decision Log)

| 日期       | 决策                                | 原因                                |
|-----------|------------------------------------|------------------------------------|
| 2026-05-11 | 跳过 LLM-only 摘要方案              | 幻觉风险高, 不可逆                    |
| 2026-05-11 | 跳过 MD-only archive 中间态          | 跟 Q3 双层最终态对比, 没有节省            |
| 2026-05-11 | PG 主存 + jsonl fallback (跟 facts) | 跟 mcp-registry / skills-hub / facts 同模板 |
| 2026-05-11 | 阈值 4KB, 不是 2KB                   | 保证 archive 后真省 token (1.5KB < 4KB)  |
| 2026-05-11 | retention 14 天                     | 跟 audit / FACT 对齐, 平衡可追溯 vs 隐私  |
| 2026-05-11 | 摘要走 haiku tier=private            | 不消耗用户配额, 哀求摘要快不慢            |
| 2026-05-11 | 灰度从 chenhongbo 单人开始           | 风险可控, 改 prompt / 改 SOUL 快           |

---

## 17. 后续 (P1 / P2)

| 优先级 | 项                                            | 日期      |
|-------|-----------------------------------------------|----------|
| P1   | `/admin/archives` UI (列表 + 详情 + 手动删)     | 5/29 后  |
| P1   | "敏感标记" — 用户标记某 archive 不 archive (直走 FIX41)  | 6 月      |
| P2   | archive 内容向量化 + 跨 session 召回             | Q4 GRAPH |
| P2   | S3 迁移 (PG content → s3:// URL)                | 上云时    |
| P2   | archive 进 employee_journal — "你上次跑那个 pytest 失败了, 还记得吗"  | Q3 末    |

---

## 18. 评审 checkpoint (5/12)

实施前请鸿波过一遍:

- [ ] 数据模型 (§4) — 字段够不够, retention 14 天合适?
- [ ] 阈值 4KB (§5.2, §15-Q2) — 太严? 太松?
- [ ] 摘要 prompt (§6.2) — 反幻觉规则是否够?
- [ ] SOUL.md 段 (§9) — LLM 教育语气够强?
- [ ] 灰度计划 (§10.3) — chenhongbo → dev 部门 → 全公司, 节奏?
- [ ] 工作量 (§13) — 5/15-5/22 共 5 工作日, 排期合理?
- [ ] 风险 §14.1 (LLM 不主动 read) — 还需要加什么强 trigger?
- [ ] 隐私 §15-Q7 — 14 天保留, 高敏部门要不要单独配置?

---

**End of design doc.**
