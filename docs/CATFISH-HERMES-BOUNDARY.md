# Catfish — Hermes 责任边界 (反越界原则)

> **状态**: 强制纪律. 任何 PR / 设计提议越界 = 不收.
>
> **写作背景**: 5/17 04:40 凌晨, catfish + 鲶鱼一起评估"memory 系统是否完成"时, 鲶鱼建议加 `catfish_memory_compress` / `catfish_memory_dedupe` / "压缩前提取 memory hook" 等. 鸿波 5 次抓到鲶鱼**越界 hermes 责任**: hermes 已经有 LCM / context 压缩 / memory 工具, catfish 不该自己再写一套. 当晚固化这条边界, 防未来再"凡事 catfish 全包".
>
> **关联**: `PRIVACY-PRINCIPLES.md` (员工数据不上传中心), `RCA-MEMORY-PLUMBING-20260516.md` (5/16 6h RCA — catfish 跟 hermes 接口 contract 断了 6 周没察觉, 反向证明边界清晰的重要性).

---

## 一句话

**catfish 只做 hermes 没有的事. hermes 有的, catfish 不重写.**

具体:
- **hermes 干**: agent 核心 (memory / todo / context / dispatch / LLM 路由)
- **catfish 干**: 包装 hermes 给员工 (UI / 入口 / 路由教学 / 政企合规 / SaaS 化)

---

## 责任表 (按层划分)

### 1. Memory 系统

| 子项 | hermes | catfish |
|---|---|---|
| memory 工具 (add/replace/remove/search) | ✅ memory_tool.py | ❌ 不重写 |
| MemoryStore in-memory 模型 | ✅ class MemoryStore | ❌ 不重写 |
| 写盘 / atomic_replace / file lock | ✅ memory_tool.py:434 _write_file | ❌ 不重写 |
| char_limit / dedupe / LCM 压缩 | ✅ 各类 store + ContextCompressor + autocompress | ⚠️ **5/17 加的 dedupe / compress 是越界**, 周一 audit 是否砍 |
| memory 写盘路径 (~/.hermes/memories/) | ✅ get_memory_dir() | ❌ 不变 |
| Dashboard 看 / 删 memory entry | ❌ hermes 没 UI | ✅ HermesMemoryCard.tsx (catfish 责任) |
| 注入到 LLM system prompt | ❌ hermes CLI 自己注 (AIAgent 内部) | ✅ catfish gateway 走 Provider 注入 (我们 SaaS 路径不走 hermes CLI) |
| target=user vs target=memory 路由教学 | ✅ memory_tool docstring | ✅ SOUL.md 解释给 catfish 员工 (扩 hermes 默认教学) |
| 跨 session 检索 (search) | ✅ memory(action=search) | ❌ 不重写 |
| 上下游 LLM provider 切换 | ❌ hermes 不管 | ✅ catfish gateway (LiteLLM) |

**catfish 越界过的事 (5/17 凌晨)**:
- ❌ `catfish_memory_dedupe`: 自己用 jieba 写 Jaccard 找重复 — hermes 有 ContextCompressor 含语义摘要可复用
- ❌ `catfish_memory_compress`: 自己写"砍最老 N 条" 压缩 — hermes 有 catfish-autocompress plugin 已 ship
- ❌ "压缩前提取 memory hook" (一度想加进 hermes plugin): 改 hermes 主对话路径, 风险高且 hermes 已经在压

**周一 audit 决定**: 这 3 件是 catfish 越界, 砍工具暴露 / 砍 task. 留代码作 git 历史, LLM 看不到.

### 2. Context / Session 系统

| 子项 | hermes | catfish |
|---|---|---|
| session lifecycle (start / end / fork) | ✅ AIAgent / cli.py | ❌ 不重写 |
| state.db (sessions / messages 表) | ✅ hermes 写 | ✅ catfish session_write.rs 同时写 (员工本机, 文件锁 WAL) |
| FTS5 / 历史搜索 | ✅ session_search_tool.py | ⚠️ catfish 加了 catfish_search_sessions (跨 session) 跟 hermes session_search 互补 (5/5 注释明确"优先用 catfish_search_sessions") |
| context 压缩 (ContextCompressor) | ✅ agent.context_compressor | ⚠️ catfish 包装 catfish-autocompress plugin (调阈值 50%, hermes 默认是 70%) — 这是"调参不重写", 合理 |
| 主动 starter (proactive) | ❌ hermes 没 | ✅ catfish 自家 (ProactiveCard + gateway endpoint) |

### 3. Tool dispatch

| 子项 | hermes | catfish |
|---|---|---|
| tool registry | ✅ tools/registry.py | ❌ 不重写 |
| dispatch | ✅ registry.dispatch(name, args, **kw) | ✅ catfish-tool-bridge 包装 (5/16 BL-MEMORY-BRIDGE-STORE 修了 store 注入断 6 周) |
| 工具 schema 兼容 | ✅ hermes 工具 | ✅ catfish 加 catfish_* native 工具 (本地能力 hermes 没有的) |

### 4. LLM 调用

| 子项 | hermes | catfish |
|---|---|---|
| LLM SDK | ✅ hermes CLI 走 LiteLLM | ✅ catfish gateway 走 LiteLLM (catfish 自家 fallback 链, 不走 hermes CLI 那条) |
| 模型选 / 切换 | ❌ hermes CLI 启动时定 | ✅ catfish 跑时切 (BL-FALLBACK-TOGGLE 默认关 auto-fallback) |

---

## 决策原则 (catfish 该做 vs 不该做)

新功能落 catfish 前先问 3 个问题:

1. **hermes 有没有同等能力?** → grep `~/.hermes/hermes-agent/tools/` + `agent/` + `plugins/`
   - 有 → 不做. 直接复用 hermes
   - 没有 → 继续问 #2
2. **这是不是 hermes 该做但还没做?** (例如更好的 LCM)
   - 是 → 给 hermes 提 PR / issue, **不**在 catfish 写一套 (除非急用 fallback)
   - 否 → 继续问 #3
3. **这是不是 catfish SaaS 包装层独有的事?** (UI / 入口 / 政企合规 / 多员工)
   - 是 → catfish 做
   - 否 → 重新评估, 可能是 hermes 责任

---

## 反模式 (PR review / 设计提议时拦)

| 反模式 keyword | 警告 |
|---|---|
| "catfish 自己实现 X" 但 hermes 已经有 X | 越界 |
| "catfish 重写 hermes Y 因为 hermes Y 不够好" | 应该给 hermes 提 PR, 不是 catfish 包一层 |
| "在 hermes plugin 里加 catfish 专属 hook" | 改 hermes 内部 = 高风险 + 升级 hermes 时丢. 改 catfish 那层 |
| "catfish gateway 加一个 X 的 endpoint, X 是 hermes 也有的" | 同上, 应该走 hermes |
| "catfish 把 hermes 没暴露的内部 class 暴露给 LLM" | 应该给 hermes 提 PR 让它正式 expose, 不是 catfish 偷偷用 |

---

## 历史教训

**5/16 BL-MEMORY-PLUMBING-DIAG (6h RCA)**:
- hermes 升 0.13 时 memory_tool handler 从无参变成 `kw.get("store")`. catfish tool-bridge 没注 store → 静默拒 6 周.
- 教训: catfish 跟 hermes 的**接口 contract** 要在 `HERMES-UPGRADE-CHECKLIST.md` 显式守住. 升级必跑 `tests/test_memory_store_injection.py` 行为级 contract test.

**5/17 凌晨 BL-MEMORY-DEDUPE-COMPRESS (今晚 over-engineer)**:
- 我 (鲶鱼) 凌晨 03am 看 memory entries 觉得"该加去重 + 压缩工具", 没 grep hermes 现成的 ContextCompressor / autocompress plugin 就动手.
- 鸿波 5 次抓: 没核实 / 越界 / over-engineer / 没信 hermes / 复杂化.
- 教训: 凌晨疲劳期**禁止凭印象设计新功能**. 任何"加新工具"前先 3 问 (上面决策原则).

---

## 商业差异化

这条边界是 catfish 对客户的 USP 一部分:
- 客户问"你们跟 hermes 是啥关系", 答: catfish = SaaS 化包装 + 政企合规 + UI / 入口. hermes = agent 核心.
- 客户问"hermes 升级你们怎么办", 答: catfish-tool-bridge 是 adapter, hermes 升级我们跑 `HERMES-UPGRADE-CHECKLIST.md` 跟. 但**我们没动 hermes 内部**, 所以升级风险低.
- 砸边界 (catfish 改 hermes 内部) = 砸"升级跟随" 承诺.

---

**作者**: 鸿波 + 鲶鱼
**生效日期**: 2026-05-17 (凌晨 04:50)
**修订**: 需鸿波拍板, 不允许工程师 / AI 单方面松绑.
