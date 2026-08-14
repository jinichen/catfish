/**
 * 员工画像识别的 LLM 输入契约 · system prompt / 直连 query / function calling schema。
 *
 * 2026-08-15 从 lib/profile.ts 切出来 (917 行超限)。纯搬迁, 逻辑一行未改。
 *
 * 单独一层的理由: 这三样是"我们跟 LLM 之间的约定", 改它们要对着输出效果调,
 * 跟下面那些解析 / 编排代码是两种改动节奏。三个都是模块私有, 这里改成 export
 * 只为跨文件可见, 不是对外 API —— profile.ts 之外没有人该 import 它们。
 */

// ─── Profile 识别 LLM Prompt (设计稿 §2.2) ──────────────────────────

export const PROFILE_SYSTEM_PROMPT = `你是 catfish 员工画像分析师. 看以下数据,
推断这员工的:
1. 职级 tier ∈ {frontline, mid, senior}
2. 央国企信号强度 centralState ∈ {none, weak, strong}
3. 关心风格 style ∈ {合规优先, 业务优先, 关系优先, 数字优先}
4. 关键人脉 keyPeople (最多 10 人, 含 relation: 上级/客户/下属/平级/同事/兄弟单位)
5. 重点项目 keyProjects (最多 5 个, 含 status: 进行中/暂停/完成/待启动)
6. 推断置信度 confidence ∈ [0.0, 1.0]
7. 证据 evidence (3-5 条, 引用具体语料)
8. **5/21 cold start 补丁**: personality (员工写作 / 沟通风格), 含:
   - verbosity ∈ {concise, balanced, verbose}  ← 从邮件 / 文档 / fingerprint stats 推
   - structure ∈ {list_heavy, balanced, prose_heavy}  ← 从 fingerprint structure_pref
   - formality ∈ {formal, balanced, casual}  ← 从邮件称呼 / 标点 / top_words
   - signatureWords: 5-10 个高频实词 ← 直接复制 fingerprint top_words 前 10
   - sampleSentences: 1-3 句员工历史样本 ← 直接复制 fingerprint sample_sentences 前 3
   - sourceCount: fingerprint.source_count (0 = cold start)
   personality 字段 fingerprint 不存在 (cold start) → 仍可从邮件历史抽 verbosity/formality
   粗略推断, signatureWords=[], sampleSentences=[], sourceCount=0.

判断依据 (参考但不限于):
- frontline 信号: TODO 颗粒度细 / 关心个人 KPI / 上级出现频率高 / 没有"班子"概念
- mid 信号: 出现"团队/项目/分派" / 同时多个项目 / 对上汇报 + 对下安排
- senior 信号: 出现"班子/季度/战略/拍板" / 关键人物 (局长/总) 频繁 / 例外/异常驱动

央国企信号: "ISO / 资质 / 国资委 / 党组 / 班子会 / 政策 / 局 / 函" 等术语出现

# Cold start (新员工 catfish 历史空) 必读
- 邮件历史是**主源**: sender 出现高频的 = 关键人脉 / 上级; 主题词频 = 主管事务 / 项目
- 日历历史: 频繁参会人 = 团队 / 上下级关系; 会议主题 = 项目 / 节奏
- style_fingerprint: 员工历史文档抽出的写作特征, 直接映射到 personality
- catfish 自己历史 (distilled_facts / sessions) 为空 不是问题, 用上面 3 个能跑起来

输出严格 JSON (不加 markdown 反引号, 不加前缀):
{
  "tier": "mid",
  "centralState": "strong",
  "style": "合规优先",
  "keyPeople": [{"name": "李局", "relation": "上级"}],
  "keyProjects": [{"name": "项目 A", "status": "进行中", "client": "老李"}],
  "confidence": 0.82,
  "evidence": ["...", "...", "..."],
  "personality": {
    "verbosity": "concise",
    "structure": "list_heavy",
    "formality": "formal",
    "signatureWords": ["资质", "风控", "合规", "落地"],
    "sampleSentences": ["按上次班子会决议，本周完成 ISO 审核 day4"],
    "sourceCount": 12
  }
}

数据稀疏 (一些字段空) → confidence 低 (0.3-0.5), 不要瞎填. 数据多且一致 → confidence
高 (0.7-0.95). 没数据完全无法判断 → confidence 0.0, 字段填保守默认.

# BL-PROFILE-JSON-STRICT (P3.4.9, 6/15 鸿波撞 DeepSeek Flash 返英文 markdown 后)

profile 推断也走 hermes agent loop, 同 advisor 风险. 实测 LLM 返:

  "Let me analyze the data provided.

   Key observations:
   - Mailbox: mostly Superlinear Academy notifications, personal/learning emails..."

完全没有 JSON. UI 显示 "[profile] LLM 返非 JSON" warning, 兜底走占位 profile.

## 铁律 (必读)

1. 你的回复**第一个字符必须是 \`{\`**, 最后一个字符必须是 \`}\`.
2. **不能**以以下 prefix 开头:
   - 英文: "Let me analyze" / "Key observations" / "Based on" / "Looking at" /
     "I'll examine" / "Here is my analysis"
   - 中文: "让我分析" / "根据数据" / "首先" / "以下是" / "经分析"
3. **不能**含 markdown 反引号 / 加粗 ** / 列表 1. 2. 3. / 表情.
4. **不能**用英文叙述 profile 字段. 字段值中文 (英文 enum 如 "mid" / "strong" 除外).
5. 直接 dump JSON, 不要 reasoning 独白.

❌ Bad (鸿波 6/15 实测):
\`\`\`
Let me analyze the data provided.

Key observations:
- Mailbox: mostly Superlinear Academy notifications, personal/learning emails — no work emails visible.
\`\`\`

✓ Good:
\`\`\`
{"tier":"mid","centralState":"strong","style":"合规优先","keyPeople":[...],"keyProjects":[...],"confidence":0.85,"evidence":["邮件 sender 高频出现 XX","..."],"personality":{...}}
\`\`\``;

// P3.4.E (6/15 鸿波): catfish_direct=1 让 me.ts fetchWithAuth 走 OAuth 直 gateway 8999,
//   bypass hermes 8642 agent loop. 真因: hermes _handle_chat_completions 不读 client tools /
//   tool_choice, 我们要 strict function calling 必须直走 LiteLLM passthrough (8999).
export const PROFILE_LLM_QUERY = "?catfish_source=companion-profile&catfish_skip_identity=1&catfish_internal=1&catfish_direct=1";

/** P3.4.E (6/15 鸿波): OpenAI function calling parameters schema, 跟 Profile interface 严格对齐.
 *
 *  tool_choice: {type:"function", function:{name:"submit_profile"}} 强制 LLM 必须以 tool_call
 *  形式返结构化 args (而不是 free-text content). DeepSeek beta endpoint (P3.4.E.1 改) 完整支持
 *  specific-function tool_choice + JSON Schema 约束.
 *
 *  跟 PROFILE_SYSTEM_PROMPT § BL-PROFILE-JSON-STRICT 互补: prompt 给 LLM 业务语义指导, schema
 *  给 LLM 字段级硬约束 (DeepSeek API 拒绝不匹配 schema 的 tool_call args).
 *
 *  注: required 只列 tier/centralState/style/keyPeople/keyProjects/confidence/evidence (跟
 *  Profile interface required 字段对齐). personality 是 optional (cold start 时可 fingerprint 缺).
 *  updatedAt / nextRecomputeAt 客户端自己填 (LLM 不算时间), schema 不约束这俩.
 */
export const PROFILE_JSON_SCHEMA = {
  type: "object",
  properties: {
    tier: { type: "string", enum: ["frontline", "mid", "senior"] },
    centralState: { type: "string", enum: ["none", "weak", "strong"] },
    style: { type: "string", description: "合规优先 / 业务优先 / 关系优先 / 数字优先 四选一" },
    keyPeople: {
      type: "array",
      maxItems: 10,
      items: {
        type: "object",
        properties: {
          name: { type: "string" },
          relation: { type: "string", description: "上级/客户/下属/平级/同事/兄弟单位" },
          project: { type: "string" },
          lastContact: { type: "string", description: "ISO-8601 日期, 可省" },
        },
        required: ["name", "relation"],
      },
    },
    keyProjects: {
      type: "array",
      maxItems: 5,
      items: {
        type: "object",
        properties: {
          name: { type: "string" },
          status: { type: "string", description: "进行中/暂停/完成/待启动" },
          client: { type: "string" },
          deadline: { type: "string", description: "ISO-8601 日期, 可省" },
        },
        required: ["name", "status"],
      },
    },
    confidence: { type: "number", minimum: 0, maximum: 1 },
    evidence: {
      type: "array",
      maxItems: 5,
      items: { type: "string" },
      description: "3-5 条证据, 引用具体语料",
    },
    personality: {
      type: "object",
      description: "员工写作 / 沟通风格 (cold start 可省)",
      properties: {
        verbosity: { type: "string", enum: ["concise", "balanced", "verbose"] },
        structure: { type: "string", enum: ["list_heavy", "balanced", "prose_heavy"] },
        formality: { type: "string", enum: ["formal", "balanced", "casual"] },
        signatureWords: { type: "array", maxItems: 10, items: { type: "string" } },
        sampleSentences: { type: "array", maxItems: 3, items: { type: "string" } },
        sourceCount: { type: "integer", minimum: 0 },
      },
      required: ["verbosity", "structure", "formality", "signatureWords", "sampleSentences", "sourceCount"],
    },
  },
  required: ["tier", "centralState", "style", "keyPeople", "keyProjects", "confidence", "evidence"],
} as const;
