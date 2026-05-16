# 算力扩容提案 — 私有部署 LLM 升级

**日期**: 2026-05-15
**作者**: 鸿波 (catfish 工程)
**收件人**: 客户 IT 决策层 / 产品负责人 / 销售
**目的**: 解释为什么客户当前的 Qwen 122B 私有部署在 catfish 实盘使用中体验明显落后于公网模型, 给出 3 档算力升级选项及预算参考

---

## 一、问题陈述

客户保密线规定 **数据不能走公网 LLM** (Gemini / Claude / GPT-4 等), 强制使用部署在客户机房的 **Qwen 122B** (10.10.40.102, 当前显卡配置约等于 1 张 A30/A40)。

5/15 实盘测试中, catfish 工程层 (skills 系统 / plan-execute / 长上下文 / 工具调用兜底) **架构层全打通**, 但实际员工体验跟我们 demo 给客户看的差异很大。**根因不是工程, 是模型本身能力**。

**两个验证**:
1. 同一个 catfish 平台, 同一份用户输入 ("分析这两份 CSV 然后用归藏 skill 生成 PPT"):
   - 跑 Qwen 122B → 5+ 轮交互, 反复嘴炮 stop, 用户多次点"继续", 最终没出 PPT
   - 切 Gemini 2.5 Pro (开发自测验证, 客户生产不能用) → **1 次发送, 完整执行**, 出 PPT 文件
2. 同一份系统提示 / 工具列表 / 输入数据下, Gemini 不靠任何 patch 自然走完 ReAct (Reasoning + Acting) 链路, Qwen 122B 需要 gateway 层加 8+ 个保护补丁 (BL-COMPOUND-PLAN-EXECUTE / BL-TASK-ASSESS / BL-MAX-TOKENS-DYNAMIC / BL-ARCHIVE-SKIP-INSTRUCTIONAL / ...) 才勉强能跑通基本单步任务

---

## 二、Qwen 122B 实盘症状 — 具体数据

从 5/15 下午约 4 小时实盘 log 抽取 (单一员工, chenhongbo@ffcs.cn):

| 指标 | Qwen 122B 实测 | 业界 Gemini 2.5 Pro 同等 |
|---|---|---|
| **嘴炮率** (assistant `finish_reason=stop` 且 `tool_call_count=0`) | 约 36% (11 轮中 4 轮空 stop) | < 5% |
| **复合任务一次性完成率** (例: "做 A 然后做 B") | 0/3 (3 次实测全失败, 都要催 2-5 次) | 3/3 |
| **单 turn 输出 token (策略性 stop)** | 平均 ~600 token 后 stop, 远低于 max_tokens 上限 (32K-120K) | 跑到自然结束, 平均 2-5K |
| **TTFT (time to first token)** | 5-26 秒 | 1-3 秒 |
| **典型卡点表现** | 输出文字 plan "模板太长, 让我直接基于..." 然后 stop, 不发 tool_call | 直接 emit tool_call, 不犹豫 |

**真实员工原话**:
- "怎么还是中断？"
- "怎么还是不干活"
- "一直断的原因是什么？"
- "都是不干活"

最严重场景: 单条 user "继续" 触发后, 模型连续 4 次 `finish_reason=stop, completion_tokens=43-145`, 全部为嘴炮型短文字, 0 个工具调用。员工累计点击"继续"按钮 5+ 次, 客户 demo 场景下无法接受。

---

## 三、不升级算力的工程层补救尝试 (今天做的)

为缓解上述症状, 我已在 gateway 层加入以下保护机制 (单元测试 200+ 全通过, 已上线):

1. **BL-COMPOUND-PLAN-EXECUTE**: 复合任务强制注入 plan-execute 铁律
2. **BL-MAX-TOKENS-DYNAMIC**: 输出 token 上限按 context window 动态调配
3. **BL-ARCHIVE-SKIP-INSTRUCTIONAL**: 指令型 skill 返回不被归档, 防 agent 死循环
4. **BL-TASK-ASSESS**: 嘴炮检测 UI badge + "催它继续"按钮 (3 次上限)
5. **BL-SKILL-METADATA-DYNAMIC**: skill 触发零硬编码
6. **BL-TOOL-CAP**: tool 数量 50 上限防上游撑爆
7. **BL-COMPRESS-BOUNDARY**: 长对话压缩不留 orphan tool message
8. **BL-GUIZANG-RELAY-CLARIFY**: skill 接力步骤明文铁律, 减歧义

**结果**: 单步任务 (例如"做一份 PPT") 现在能跑通, 但需要员工点 2-3 次"继续"。复合任务 (例如"分析数据然后做 PPT") **仍然不稳定**, 切 Gemini 一次过, Qwen 反复卡。

**结论**: 工程层已经做到能做的极限。再加补丁回报递减且引入维护债。**模型层是真瓶颈**。

---

## 四、升级方案 3 档

按"客户基建预算 vs 体验提升"列出 3 档, 按推荐度排序:

### 方案 A: 升级到 Qwen3-Max / Qwen 235B (推荐, ROI 最优)

- **模型**: Qwen3-Max (商业版) 或 Qwen 235B 开源
- **算力需求**: A100 80G × 2, 或 H100 80G × 1
- **预算参考**: 单卡 H100 约 30-50 万 RMB (硬件) + 1-2 万 RMB/月 (运维电费)
- **预期效果**: ReAct 能力跟 Gemini 2.5 Pro 同档, 嘴炮率 < 10%, 复合任务一次性完成率 70%+
- **兼容性**: catfish gateway 完全兼容, 改 `.env` 一行 model URL 即可切换
- **风险**: 235B 模型推理延迟比 122B 高 (TTFT ~ 3-5 秒), 但远好于现在 26 秒

### 方案 B: 切到国产开源更强模型私有部署

- **候选**: DeepSeek-V3 (671B MoE) / GLM-4.5 / Kimi K2
- **算力需求**: H100 80G × 8 (DeepSeek-V3 MoE 模型, 总参数大但实际激活少)
- **预算参考**: 200-500 万 RMB (8 卡集群) + 5-10 万 RMB/月 运维
- **预期效果**: 顶级开源模型, 全维度跟 Gemini Pro / Claude 3.5 持平甚至更强
- **兼容性**: 这些模型 OpenAI API 兼容, catfish gateway 都支持
- **风险**: 客户机房可能需要新机柜 + 散热改造, 部署周期 2-3 月

### 方案 C: Qwen 122B 微调

- **路径**: 用 catfish 这两周积累的实盘 trace, 给 Qwen 122B 做 ReAct LoRA 微调
- **算力需求**: 当前显卡够用 (训练比推理低)
- **预算参考**: 几乎 0 硬件成本, 1 名算法工程师 2-4 周工作量
- **预期效果**: 嘴炮率 ~15% (改善有限, Qwen 122B 参数量天花板摆在那)
- **兼容性**: catfish 不变
- **风险**: 微调周期长, 5 月 demo 等不到; 效果上限低 (122B 参数本身就是约束)

### 不推荐: 接受现状, 教用户"催继续"

- 客户 demo 时演员工"催 2-3 次"是不专业的。catfish 卖点是"AI 自动化", 演 demo 时让用户手动 babysit AI 等于自打脸。
- BL-TASK-ASSESS 的"催继续按钮"是兜底, 不是常态。

---

## 五、5 月 demo 的现实选择

5 月 demo (周末交付) 时间窗内, 算力升级来不及。**短期 3 条腿** :

1. **demo 任务选型**: 主推**单步任务** (做周报 / 立项报告 / 单一 skill 调用), 避开复合任务演示。catfish 单步任务 Qwen 122B 能跑通, 配上 BL-TASK-ASSESS UX 兜底, 体验是可以的
2. **跟客户讲清楚 SLA**: 在 SoW / demo 后跟进沟通时主动说明: "当前算力下 Qwen 122B 的复合任务能力有 35% 嘴炮率, 建议升级算力或拆任务"。**透明 > 假装能用**
3. **拿这份 memo 推算力扩容预算**: demo 演完, 客户问"再升级一下能做哪些更复杂的", 我们立刻拿这份 memo 谈算力升级 ROI

---

## 六、附录: 实盘 log 关键片段

**5/15 15:36-15:44 嘴炮高发段** (8 分钟内 11 次 chat completion):

```
15:36:58  tool_calls=4   finish_reason=tool_calls    ✅ 真调
15:37:32  tool_calls=0   finish_reason=stop          ❌ 嘴炮 (cum_content=775)
15:38:09  tool_calls=5   finish_reason=tool_calls    ✅
15:38:21  tool_calls=4   finish_reason=tool_calls    ✅
15:39:51  tool_calls=0   finish_reason=stop          ❌ 嘴炮
15:41:15  tool_calls=0   finish_reason=stop          ❌ 嘴炮
15:42:46  tool_calls=0   finish_reason=stop          ❌ 嘴炮
15:44:00  tool_calls=0   finish_reason=stop          ❌ 嘴炮
```

**5/15 16:48-16:51 复合任务单 turn 跑 181 秒最终空输出**:

```
prompt_tokens=72141
completion_tokens=935   # 935 token 全是 markdown code block, 0 tool_call
latency_ms=181228       # 跑了 3 分钟
finish_reason=stop      # 嘴炮 stop
```

**用户切 Gemini 2.5 Pro 后**: 同样的任务, 同一套 catfish 平台, **1 次发送, 完整执行, 出 PPT 文件**。

---

## 七、决策点

请客户 IT 决策层就以下 3 点回复:

1. **算力升级预算可行性**: 方案 A (单卡 H100, 30-50 万) 是否在 Q3-Q4 预算可容纳? 方案 B (集群 200-500 万) 是否要走专项立项?
2. **升级时间窗**: 5 月 demo 后多久能完成升级? (影响我们工程线 backlog 排期 — 模型升级前不再做更多 Qwen 122B 专用 patch)
3. **过渡期沟通策略**: demo 后跟客户讲算力升级 ROI 时, 是销售出面还是工程出面? 这份 memo 是否能改成销售话术版?

---

**联系**: 鸿波 (catfish 工程)
**附件**: 完整 log + BL-* 任务记录可按需提供
