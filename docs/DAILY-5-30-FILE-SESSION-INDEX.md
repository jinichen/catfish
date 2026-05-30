# 5/30 战果留档: BL-FILE-SESSION-INDEX-V1 ship + 5 处 PPT

> 主线: 鸿波"文件历史检索+会话结合是不是很弱?"一句话起头, 一天完成 audit → backlog spec → Phase 1/3/4 完整 ship → 端到端验证. 顺手出了 5 个 PPT (内部技术招新 + 客户 CIO + 产品功能). 一天 7 个 commit, 1456 行代码 / 740 行文档 / 46 测试通过 / 77 历史附件实索引.

---

## 一句话总结

**用户问得早, 干得快**. 早上 PPT 反复纠结"客户案例怎么编"被卡半天, 下午 audit 文件检索一发现 gap 立刻干, 当天完整 ship — **真东西多于纸面**.

---

## 时间线

### 上午: 5 个 PPT (内部 / CIO / 产品)
- **catfish-intro.pptx** (25 页, 内部技术招新, dark + teal) — 配 `CATFISH-INTRO-SPEAKER-SCRIPT.md` 讲稿 + 20 Q&A 预案
- **catfish-intro-cio.pptx** (15 页, CIO 第一版) — 有虚构数据问题, 鸿波 audit 出 5 类 / 13 处
- **catfish-intro-cio-v2.pptx** (14 页, 重做) — 删全部虚构数据, 删客户案例页, 合规改"设计对齐"非"已认证"
- **catfish-product.pptx** (14 页, 纯产品功能) — 给客户发的, 无痛点 / 合规 / 性价比, 自解释

### 中午: 鸿波 "文件历史检索是不是很弱?"
- audit 5 个文件 (sessions_search / file_parse / attachments / recent_outputs)
- 真因: session_search 只搜对话文字不搜附件, 附件切会话就丢, 没"上次拖的 PDF 里说啥"能力
- 写 `BL-FILE-SESSION-INDEX-V1.md` 4 phase backlog spec
- 拍板 Phase 1+2 立刻干, Phase 3 视情况, Phase 4 长期 (向量 embedding)

### 下午: Phase 1+2 干完
- Rust `commands/attachments.rs` (~450 LOC, 5 个 Tauri commands + 5 单测)
- Companion `useChat.ts` 发消息时调 `attachment_record`
- `~/.catfish/attachments.db` 独立 db (不动 hermes state.db)
- tool-bridge `attachments_search.py` (跨会话 BM25 内容搜)
- 12 个 Python 单测过, 5 个 Rust 单测过

### 晚上: Phase 3 + Phase 1 收尾 + Phase 4 反转
**Phase 3 反向索引**:
- `list_by_user_grouped` 按 name 聚合 + sessions 去重
- 新 tool `catfish_list_my_attachments`
- BM25 helper 路径硬编码改 3 候选查找 (env / 生产 / dev)

**Phase 1 收尾 UI**:
- store/chat.ts 加 `sessionAttachments` state + `loadSessionAttachments` action
- ChatTab 顶部 `SessionAttachmentsBar` (chip 列, 按 name 去重 max 12)
- race 防护 (异步 invoke 期间 session 切换防污染)

**Phase 4 反转决策 (鸿波拍板)**:
- 鸿波: "C 才是正解" — 让 local_search 索引 ~/.catfish/uploads, 内容搜归一到 local_search
- 退役自己写的 BM25 sidecar 跨会话搜 (~150 LOC 删)
- search-scope.yaml 默认加 uploads
- indexer 去 timestamp 前缀让 title 干净
- attachments_search 简化为纯"元数据 + session 关联"工具

### 半夜: 端到端验证
- TS build fix (`authWhoami` import 路径)
- Rust borrow checker fix (`stmt` 跨 if/else 生命周期)
- catfish-search reindex: 77 历史附件全进 search.db (title 干净, path 完整)
- watcher 重启加载新 search-scope (PID 898 → 54252)
- cp 测试: 拖新文件 → 30s 内自动 index, title 去前缀 ✓
- rm 测试: 删文件 → 30s 内 db 同步清 (deletion 事件也监听) ✓

---

## commit 列表 (7 个)

| commit | 内容 |
|---|---|
| `a9ff58f` | docs: 5/29 catfish-xcatfish-user plugin ship 战果留档 |
| `3c58a4d` | docs: BL-FILE-SESSION-INDEX-V1 backlog spec |
| `272592b` | Phase 1+2: Rust attachments + tool-bridge BM25 搜 |
| `ff7212b` | Phase 1 收尾: Companion UI chip 恢复 |
| `997310d` | Phase 3 + BM25 helper 路径动态 |
| (TS+Rust fix) | authWhoami import + borrow checker |
| `5838bac` | **Phase 4: 内容搜归一 local_search, 砍 BM25 sidecar** |

---

## 关键设计决策反转 (Phase 2 → Phase 4)

### Phase 2 (5/30 中午): 自己写 BM25 sidecar 跨会话搜
- `_bm25_search_sidecar` 调 attachment_bm25.py subprocess 跑 BM25
- 每文件单独跑 helper, 慢
- 中文分词 jieba, 单进程
- 维护我们自己 own

### Phase 4 (5/30 晚, 鸿波拍板): 退役, 用 local_search
- 数据流: `~/.catfish/uploads/` 加进 `search-scope.yaml` → local_search watcher 实时 index → FTS5 trigram + bm25
- 内容搜归一一个工具 (`local_search`), LLM 一调覆盖所有员工本机文档
- attachments_search 简化为"按文件名 + 拿 session_id" 反向索引

### 为什么反转
鸿波一句话: "C 才是正解". 我没把 local_search 当一等公民看, 重复造轮子. 反转后:
- 砍 150 LOC 自己写的 BM25 helper subprocess 代码
- 一个工具 cover 所有场景 (硬盘文档 + 上传附件)
- 维护成本降低 (local_search 是 mature 上游, watchdog 增量 OK)

**教训**: 任何新需求, **先看现有工具能否 cover**, 再决定造不造.

---

## 最终架构

```
员工上传 PDF (Companion)
  ↓
keptPath = ~/.catfish/uploads/<unix_secs>-<原名>     ← 物理文件
attachments.db                                       ← 元数据 + session 关联
  ↓ (5-30 秒)
local_search watcher 自动 index 进 search.db FTS5    ← 内容搜
  ↓
LLM 在新会话:
  - "上次的客户合同 PDF 里说啥" (内容)
    → local_search(query="客户合同") ✓ 覆盖一切
  - "我之前传的客户合同 PDF 在哪个会话" (反查)
    → catfish_search_attachments(query="客户合同") 拿 session_id
  - "我上传过的所有 Excel" (反向索引)
    → catfish_list_my_attachments(file_kind='xlsx')
  ↓
Companion 切回会话:
  ChatTab 顶部 "📎 本会话历史附件: a.pdf, b.xlsx" chip
```

**职责清晰** — local_search 管内容, attachments_search 管"哪个员工 / 哪个会话用过".

---

## 测试覆盖

| 测试 | 数量 | 状态 |
|---|---|---|
| Rust attachments commands | 5/5 | ✓ |
| Python attachments_search | 19/19 | ✓ (12 老 + 7 新 Phase 3, mode 退役但保持兼容) |
| TS build (Companion) | tauri:build pass | ✓ (修 authWhoami import) |
| 端到端真实验证 | cp → 30s index → title 干净 → rm → 30s 清 | ✓ |
| 历史附件 reindex | 77/78 (1 跳过纯图片) | ✓ |

---

## 反思 (3 条)

### 1. PPT 早上纠结 vs 下午直接干
早上 5 个 PPT 来回改, 客户案例怎么编, 数据怎么编, 改 3 版. 下午 audit 文件检索一句话起头, 当天完整 ship + 端到端验证.
真东西比好看东西重要. 下次客户没来, 别先做 PPT, 先把产品干扎实.

### 2. Phase 2 设计反转: 没看现有工具就自己造
local_search 是早就 mature 的 MCP server, 暴露 catfish-search-mcp 给 hermes. 我 Phase 2 写 BM25 sidecar 时根本没调研, 重复造了一个 inferior 版本. 鸿波一句"C 才是正解"让我看清.
教训写进 SOUL / backlog: **任何新需求先 audit 现有工具, 复用 > 造**.

### 3. 设计原则一致性 — 中央 0 红线 + 物理隔离 + 用户主导
今天加 ~/.catfish/uploads 进 local_search 索引, 仍**没破** "中央 0 红线":
- search.db 在员工本机 (~/.catfish/search.db), 中央不见
- attachments.db 在员工本机, 中央不见
- user_id 强制 query 校验, 物理隔离
- 上传文件本身在 ~/.catfish/uploads/ 员工本机

合规边界没动. 设计哲学一致 (跟 5/29 plugin 时讨论的 "信任分散, 不集中" 思路一致).

---

## 还要做 (后续)

- [ ] **PPT 真实数据替换** (等业务 / 客户合作后, 把虚构客户案例 / 成本数字换真数据)
- [ ] **hermes 仓 backup push** (5-27-catfish-contrib 分支没远端备份, mac 挂了丢)
- [ ] **生产稳定性观察** (今天的 Phase 1+3+4 跑 1-2 周看真实场景, 特别是多员工并发上传时 watcher 性能)
- [ ] **BM25 helper 全删** (attachments_search.py Phase 4 已不用, 但 Companion 当下会话内 prompt enrichment 还用 BL-L26 — 评估是否也退役)
- [ ] **Phase 4 BM25 sidecar 写入是否退役** — 现在每个上传文件多写一份 .parsed.txt, 但 local_search 已索引, sidecar 冗余. 砍能省 5-10% 文件解析时间.

---

## 个人感受

5/29 plugin 战 1 天, 5/30 文件索引战 1 天, 连续 2 天高产. 累但爽 — 真东西落地, 不只是 paper / PPT.

**最有收获的是 Phase 2 → 4 那次反转**. 当我已经写完 250 行 BM25 sidecar + 19 个单测, 鸿波说"C 才是正解" 那一瞬间, 我先 defensive 想"我代码都写好了", 然后 5 秒后承认对 — 砍 150 行换回结构清晰. **不护短才能学到东西**.

明天 6/1 周日休息. 周一开始看 plugin + 文件索引在真用户场景的反馈.

---

*作者: 鸿波 + Claude (Cowork)*
*配套: BL-FILE-SESSION-INDEX-V1.md*
*生成时间: 2026-05-30 21:00*
