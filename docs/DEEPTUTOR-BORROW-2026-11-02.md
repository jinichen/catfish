# 借鉴 DeepTutor 的三件事（2026-11-02）

参照：[HKUDS/DeepTutor](https://github.com/HKUDS/DeepTutor)（Apache-2.0）—— 记忆脚注、`kb eval`、Practice。

## 1. 知识卡来源精确到「哪次对话」

- 蒸馏核对事实时，模型指认的原文段落 → 落在哪段对话的日志里 → `sources` 补 `session:<id>`
  （`catfish_memory_wiki_provenance.sessions_for_passages`，在 `catfish_memory_wiki_ground.ground_files` 里调）。
  只收本轮真读过的对话；落在 distill 记录 / 老格式条目（只留后 6 位）里的不算。合并时 `sources` 也带上。
- Companion 知识卡上新增「来自哪些对话」：精确的直接列，只到日期的列出当天的对话并标「当天的对话，不一定是这次」，
  点一下打开原对话（`commands/wiki_source_trace.rs` + `WikiSourceSessions.tsx`）。
- **没做**：「鲶鱼记的硬事实」(session_facts) 仍不带对话号 —— `catfish_remember` 经 MCP 调用时 tool-bridge
  拿不到当前对话 id，要先改调用协议。

## 2. 知识库检索打分

- 知识库页左栏「检索打分」：一行一题 `{"query": "...", "gold": ["一段话 / 标题 / wiki/...md"]}`，
  同一组题跑四种方式：关键词 / 关键词+语义+图谱（知识页默认）/ 只用语义 / 小鲶聊天时用的（tool-bridge BM25）。
  出命中率@K 和 MRR，并列出没找到的题（`commands/wiki_retrieval_eval.rs` + `WikiRetrievalEval.tsx`）。
- 注意：小鲶聊天时和知识页用的是两套检索，以前没量过谁更准 —— 这把尺子就是量它的。

## 3. 题库与错题复习

- tool-bridge 工具 `catfish_practice`（一个工具多个 action：add / import / next / answer / stats / master / delete），
  数据在 `~/.catfish/practice.db`（换机迁移 Required）。小鲶从知识库 / 资料出题入库，或导入 Excel / CSV / JSON；
  对话里一题一题考，客观题工具判对错，主观题员工自评。
- 复习排期跟 DeepTutor 同一套 SM-2 风格规则（again 1 天 / hard ×1.2 / good 首次 3 天之后 ×记忆系数 / easy 更长，最长 365 天）；
  答错进错题，次日开始复习；「已掌握」停排，再答错重来。
- 工具已提升为模型直接可见（网关 ALWAYS_ON + 插件 P43，上限 48 → 49）。
- 早安顶上「今天有 N 道错题要复习」→「开始复习」交给对话；手机早安页也提示。
- 以后可做：按员工历史训练遗忘曲线（FSRS）、题库管理页面。
