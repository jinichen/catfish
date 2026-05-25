# Skill Progressive Disclosure 架构 (BL-SKILLS-* 5/25)

> **背景**: 5/25 鸿波看 Anthropic Agent Skills "Progressive Disclosure" 文章后提出 "skill 越来越多会爆" 的合理担忧. 一夜 ship 3 phase, 把 catfish 的 skill 注入从"全部展开"改成"渐进式披露", 单 agent 撑 500+ skill 不爆.
>
> **维护**: 任何动 skills_loader / skills_inject / skills_retrieval 的人必看本文.

---

## 1. 问题 — skill 越来越多, system prompt 会爆

老格式 (5/25 之前): 每 skill 占 5 行 system prompt, ~120 tokens. 100 skill ≈ 12K tokens 只是 catalog (不算 always-on tools / memory / RBAC / identity).

```
10 skill   → ~1.2K tokens  ✅
50 skill   → ~6K tokens    🟡 开始挤
100 skill  → ~12K tokens   🟠 32K context 模型 (Qwen 122B) 难撑
200 skill  → ~25K tokens   🔴 加 memory + RBAC + tools 就爆
500 skill  → ~62K tokens   💀 当前 catfish 必崩
```

5/25 demo 时 catfish + hermes:bundled 已 10 个 skill, hermes:bundled 单独可达 180+, 加 hermes:github 员工自学的, 上 50-100 是迟早的事。

---

## 2. Anthropic 的解 — 3 层渐进式披露

| 层 | 加载时机 | 内容 | 单 skill 开销 |
|---|---|---|---|
| **Tier 1: metadata** | 永远 load (system prompt) | name + 一句话 desc | ~30-60 tokens |
| **Tier 2: SKILL.md 全文** | 模型决定调时才 load | 完整参数 schema + 用法 | 500-2000 tokens (按需) |
| **Tier 3: 参考文件** | skill 执行中按需 read_file | 模板 / 配置 / 数据 | 0 直到真用到 |

**关键句**: "A single agent can have hundreds of specialized skills without hitting memory limits."

参考: <https://docs.claude.com/en/docs/agents-and-tools/agent-skills/best-practices>

---

## 3. Catfish 实施 — 3 Phase

### Phase 1: BL-SKILLS-TIER1-SHRINK (单 skill 开销 -50%)

**文件**: `central/llm-gateway/src/catfish_gateway/skills_loader.py`

**改动**:
1. 单 skill 渲染 5 行 → **1 行** (`- \`path\` — name: desc_short`)
2. `description` hard cap **80 chars** (`SKILL_DESC_CAP`), 详细文档走 Tier 2 (`_help: True`)
3. `_help` 提示从"每 skill 重复打"挪到 header 一次说

**效果**: 单 skill 120 tokens → ~25 tokens, **5x 压缩**. 100 skill 总 catalog ~3K tokens (老 12K).

### Phase 2: BL-SKILLS-TIER1-FOLD (按 namespace 分组)

**改动**: 同文件 `format_skills_block` 按 namespace 分段:

```
#### 🎯 catfish (8 个 — 工程审定, 客户合规, 优先调)
- `department/leadership-briefing` — leadership-briefing: 上行汇报 5 段...
...

#### 🧰 hermes:bundled (35 个 — hermes 装机自带)
...

#### 🐱 hermes:github (3 个 — git clone 装)
...
```

**优先级**: `_NAMESPACE_ORDER` 排 `catfish > hermes:bundled > hermes:github > hermes:hf > hermes:local`. 模型有"硬规则 6"明示同样能搞定时优先 catfish.

**Helper 函数**:
- `_namespace_group_key(skill)`: 归一化 `hermes:github:owner/repo` → `hermes:github`
- `_group_skills_by_namespace(skills)`: 分组
- `_render_skill_line(skill)`: 单行渲染

### Phase 3: BL-SKILLS-RAG (BM25 retrieval + 折叠区)

**新文件**: `central/llm-gateway/src/catfish_gateway/skills_retrieval.py` (~150 行, 零外部依赖)

**核心**:
- `tokenize(text)`: ASCII 按词 + CJK 按 char + 大小写归一
- `BM25Index(docs, k1=1.5, b=0.75)`: Okapi BM25 with IDF smoothing
- `.rank(query, top_k=N)`: 返 `[(doc_idx, score), ...]`, score > 0 才返

**集成** (`skills_inject.py` + `skills_loader.py`):
1. `inject_skills_catalog(messages)` 从最后一条 user message 提 query
2. `format_skills_block(skills, user_query)` 接 query
3. `_select_skills_for_render`:
   - `len(skills) <= RAG_THRESHOLD (30)` OR 无 query → 全 inline
   - 否则: **catfish 全留** + hermes BM25 top-K (`RAG_TOP_K = 15`)
   - 没进 top-K 的按 namespace 折叠成 count, 提示用 `catfish_search_skills(query)` 找

**渲染** (>30 skill + 有 query 时):
```
#### 🎯 catfish (8 个 — 工程审定, 客户合规, 优先调)
... (全部 inline)

#### 🧰 hermes:bundled (BM25 选中 12 个 — 装机自带)
... (相关性最高 12 个)

#### 🗂 折叠区 (按相关性筛掉, 想用调 catfish_search_skills)

- 🧰 `hermes:bundled`: 还有 168 个 skill (hermes 装机自带)
- 🐱 `hermes:github`: 还有 14 个 skill (git clone 装)

**找不到合适的? 调 `catfish_search_skills(query='...')` 在全部 skill 里搜.**
```

---

## 4. 调优参数

`skills_loader.py` 顶部常量:

| 常量 | 默认 | 含义 | 调小风险 | 调大风险 |
|---|---|---|---|---|
| `SKILL_DESC_CAP` | 80 | 单 skill desc 字符数上限 | 描述被截掉关键信息, 模型识别困难 | Tier 1 又开始膨胀 |
| `RAG_THRESHOLD` | 30 | 总 skill 数 > 此值才启 BM25 | 小数据集也走 retrieval, 没必要 | skill 100+ 还全展示, 爆 context |
| `RAG_TOP_K` | 15 | BM25 retrieve 几个 hermes skill | 模型看不到选项, 可能漏好 skill | 折叠区作用小, 节省不明显 |

调参建议:
- **生产 50 人 skill 库 < 50**: 默认即可, 实际不会触发 RAG (catfish + hermes:bundled 也就 30-40)
- **大客户 100-500 skill**: `RAG_TOP_K=20-30`, 给模型更多选项
- **MVP 单员工 dogfood**: 降 `RAG_THRESHOLD=10` 强制走 RAG 路径做测试

---

## 5. 测试覆盖

| 文件 | 测试数 | 覆盖 |
|---|---|---|
| `tests/test_skills_inject.py` | 8 | inject_skills_catalog, fingerprint cache, multimodal |
| `tests/test_skills_loader_version.py` | 17 | Phase 1 (cap/single-line/help-once) + Phase 2 (namespace grouping/empty-skip/count) |
| `tests/test_skills_retrieval.py` | 26 | Phase 3 (tokenize/BM25/RAG/folded/query-extraction) |
| **合计** | **51** | 全过 |

**新增的 Phase 1/2/3 单测都标了 BL- tag, grep 易找:**
```bash
grep -rn "BL-SKILLS-TIER1-SHRINK\|BL-SKILLS-TIER1-FOLD\|BL-SKILLS-RAG" tests/
```

---

## 6. 跟 Anthropic 推荐的 diff

| Anthropic 推荐 | catfish 实现 | 备注 |
|---|---|---|
| Tier 1: 30-60 tokens / skill | ✅ ~25 tokens (Chinese mixed 算 4 chars/token) | 略低于推荐, 因为我们 desc cap 80 chars 比英文短 |
| Tier 2: 模型决定调时 load SKILL.md | ✅ `_help: True` 拿完整 schema | catfish 用 schema 替代 raw SKILL.md, 更紧凑 |
| Tier 3: 参考文件按需 read | ✅ 已有 read_file builtin | hermes 0.14 内置 |
| 单 agent 撑 hundreds skill | ✅ 500 skill 走 RAG 模式 catalog 仍 < 5K tokens | 实测 100 skill ~12K chars (~3K tokens) |
| 不提怎么从大池里捞 | 🟡 BM25 (lexical) | Anthropic 留空白. 我们走 BM25 v0, 后续可换 bge-m3 vector |

---

## 7. 后续路线

| 优先 | task | 估时 | 触发条件 |
|---|---|---|---|
| P1 | **#69 catfish_search_skills tool** (折叠区 → 模型主动 query) | 1-2h | Phase 3 落盘后立刻做, 完整闭环 |
| ✅ | **#70 BL-SKILLS-VECTOR** (BM25 + vector + hybrid 三 backend) | 5/25 已 ship | — |
| P2 | **vector index 持久化** (~/.catfish/skills_index.npz) | 半周 | skill 数 > 200 启动 embed 全 skill > 30s 时 |
| P3 | **per-skill usage 统计 + 排序** | 半周 | 想给模型"上周这个员工常用 X" 这种 hint |
| P3 | **kind 子分组** (procedural / instructional) | 1 天 | catfish skill > 20 时 namespace 段内再细分 |

---

## 9. BL-SKILLS-VECTOR (5/25 加, retrieval backend 升级)

Phase 3 默认 backend 是 BM25, 5/25 鸿波 "开干" → 加 vector + hybrid 模式可切。

### 配置 (env)

```bash
# 默认 (5/25 之前 + 之后): BM25, 零外部依赖
CATFISH_SKILLS_RETRIEVAL=bm25

# 走 bge-m3 vector (内网 catfish-private-embed)
CATFISH_SKILLS_RETRIEVAL=vector

# BM25 + vector 双跑, RRF (Reciprocal Rank Fusion) 融合排序
# 鲁棒性最高 — 任一 backend 漏的 doc, 另一边可能补上
CATFISH_SKILLS_RETRIEVAL=hybrid

# 可选: 自定义 embed model (默认 'openai/bge-m3')
CATFISH_SKILLS_EMBED_MODEL=openai/bge-m3
CATFISH_SKILLS_EMBED_BASE=http://10.10.40.102:32730/...
CATFISH_SKILLS_EMBED_KEY=...
```

### 高可用 — 自动 fallback BM25

vector / hybrid mode 任一步挂 (bge-m3 不可达 / embed_fn 调用挂 / index build 失败) → log warning, retrieval **自动退回 BM25**, 不挂主流程。

| mode 设置 | bge-m3 状态 | 实际生效 |
|---|---|---|
| bm25 | 任意 | BM25 |
| vector | ✓ 可达 | Vector |
| vector | ✗ 挂 | BM25 (fallback, log warning) |
| hybrid | ✓ 可达 | BM25 + Vector RRF 融合 |
| hybrid | ✗ 挂 | BM25 (fallback) |

### Vector 工作方式

`skills_vector.py:VectorIndex` 接 caller 注入的 `embed_fn: Callable[[list[str]], np.ndarray]`:
- 启动一次性 embed 全 skill (~50ms 10 skill / ~5s 100 skill / ~50s 1000 skill)
- 缓 `_vector_cache` (按 skills_path + mtime fingerprint, skill 不变不重建)
- rank 时 embed query → cosine sim → argpartition top-K

设计哲学:
- **Dependency Injection** — `embed_fn` 由 caller 注入, `skills_vector.py` 自己**不 import litellm** (生产用 `make_litellm_embed_fn(...)`, 测试用 fake hash-based)
- **numpy 算 cosine sim** — `argpartition` 比全排序快 (N=500 时 ~3x), 不靠 FAISS 这种重 lib
- **k=60 RRF** — TREC 默认, 鲁棒性强 (两 ranker 的 score 量纲完全不同, 直接加权会偏)

### 测试 (20 新测)

`tests/test_skills_vector.py`:
- VectorIndex 基础 (8 测): 空 / 单 doc / top_k / min_score / 校验 ndim+count mismatch / embed_fn 挂时 rank 返空
- _retrieval_mode env switch (4 测): default bm25 / vector / hybrid (大小写) / 非法值 fallback
- _rrf_fuse (3 测): 两 ranker 交集排前 / 完全不交集全保留 / top_k 截断
- _select_skills_for_render 多 backend (4 测): bm25 mode 不调 vector / vector mode 真调 / vector 挂 fallback BM25 / hybrid 用 RRF
- format_skills_block 集成 (1 测): vector mode 端到端, catfish 全留 + hermes 按 vector top-K + 折叠


---

## 8. 决策签名

> v0.1 = 2026-05-25 凌晨, 鸿波看 WeChat 文章 (Anthropic Skills 文档解读) 提出 → 一夜 ship 3 phase.
>
> 关键决策:
> 1. **零外部依赖** — BM25 自己撸 ~150 行, 不引 rank-bm25 / FAISS, 现在 venv 不动
> 2. **catfish 永远 inline** — 工程审定 skill 优先级最高, 不让 RAG 把它推到折叠区
> 3. **BM25 over vector** — 中文 char-level + 短文本 (80 chars desc), BM25 准确率 ≥ vector 在我们规模
> 4. **rag_threshold=30** — 实测下不超 30 走 RAG 没必要, BM25 排序开销不小于全展示
> 5. **接口稳定** — `format_skills_block(skills, user_query=...)` 加可选参不破老 caller, 未来换 vector backend 只动 `_select_skills_for_render`
