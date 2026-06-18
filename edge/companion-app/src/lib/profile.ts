/** BL-ADVISOR-PROFILE (5/21 Phase 7 第 1 步): 员工职级 + 画像自动识别 — TS 端.
 *
 * 设计稿: docs/CATFISH-ADVISOR-DESIGN.md §2
 *
 * 分工:
 *   - Rust (commands/profile.rs): 读写 ~/.catfish/profile.json + 标错 + 过期判断
 *   - TS (本文件): 调 Tauri command + 调 gateway LLM 推断 + 写回
 *
 * 调 gateway 走第 4 步设计的 prompt (待 Phase 7 第 4 步 ship 后接入).
 * 当前版本: schema + Tauri wrapper + recomputeProfile() 占位 (实际推断逻辑待 §4 完成).
 */

import { invoke as rawInvoke } from "@tauri-apps/api/core";

import { config } from "./env";
import { fetchWithAuth } from "./me";
import {
  briefingContextFetch,
  calendarWeekFetch,
  emailListFetch,
  toolBridgeCallTool,
} from "./tauri";

// ── Schema (跟 Rust 端 profile.rs serde 严格对齐) ──────────────────

export type Tier = "frontline" | "mid" | "senior";

export type CentralStateSignal = "none" | "weak" | "strong";

export interface KeyPerson {
  name: string;
  relation: string;  // "上级" / "客户" / "下属" / "平级" / "同事" / "兄弟单位" 等
  project?: string;
  lastContact?: string;  // ISO-8601 日期
}

export interface KeyProject {
  name: string;
  status: string;  // "进行中" / "暂停" / "完成" / "待启动"
  client?: string;
  deadline?: string;  // ISO-8601 日期
}

export interface Profile {
  tier: Tier;
  centralState: CentralStateSignal;
  style: string;
  keyPeople: KeyPerson[];
  keyProjects: KeyProject[];
  confidence: number;  // 0.0 - 1.0
  evidence: string[];
  /** 5/21 cold start: 员工写作 / 沟通风格. None = 没 fingerprint (cold start) 或推断挂. */
  personality?: Personality;
  updatedAt: string;  // ISO-8601
  nextRecomputeAt: string;  // ISO-8601
}

export type Verbosity = "concise" | "balanced" | "verbose";
export type Structure = "list_heavy" | "balanced" | "prose_heavy";
export type Formality = "formal" | "balanced" | "casual";

export interface Personality {
  verbosity: Verbosity;
  structure: Structure;
  formality: Formality;
  /** 5-10 个高频实词 — LLM 起草时模仿用 */
  signatureWords: string[];
  /** 1-3 句员工历史样本 — LLM 直接模仿语气 */
  sampleSentences: string[];
  /** fingerprint 来源文档数. 0 = cold start */
  sourceCount: number;
}

// ── Tauri command wrappers ──────────────────────────────────────────

/** 读 ~/.catfish/profile.json. 不存在返 null. */
export const profileGet = () =>
  rawInvoke<Profile | null>("profile_get");

/** LLM 推断完, 写回 profile.json. 原子写. */
export const profileSave = (profile: Profile) =>
  rawInvoke<void>("profile_save", { profile });

/** 员工标"识别错了" → append profile_hints.md. */
export const profileMarkWrong = (reason: string) =>
  rawInvoke<void>("profile_mark_wrong", { reason });

/** 读 profile_hints.md (给 LLM 复算输入). */
export const profileHintsRead = () =>
  rawInvoke<string>("profile_hints_read");

/** 是否需要复算 (不存在 / 过期 / force=true). */
export const profileNeedsRecompute = (force = false) =>
  rawInvoke<boolean>("profile_needs_recompute", { force });

/** 算下次复算时间 (默认一周后). */
export const profileNextRecomputeAt = (days?: number) =>
  rawInvoke<string>("profile_next_recompute_at", { days });

// ── High-level: recompute profile ────────────────────────────────────
//
// 5/21 Phase 7 第 1 步当前版本 = 占位.
//   - Rust schema + 读写 ✓ (commands/profile.rs)
//   - Tauri wrapper ✓ (本文件 wrappers 段)
//   - LLM 推断 prompt 待 Phase 7 第 4 步 ship 后接入
//
// 当前 recomputeProfile() 行为:
//   - 没有真 LLM 调用, 返一个保守的默认 profile (tier=mid, confidence=0.0)
//   - confidence=0.0 让 UI 知道这是占位, 不要展示给员工
//   - 真实推断接入后, confidence 才反映实际值
//
// 后续接入步骤 (Phase 7 第 4 步完成后):
//   1. 调 briefingContextFetch() 拿 7 数据源
//   2. 调 profileHintsRead() 拿员工纠错痕迹
//   3. 拼装设计稿 §2.2 的 prompt
//   4. 调 fetchWithAuth 调 gateway, model 用 chat 同款
//   5. 解析 JSON 输出, 用 profileNextRecomputeAt() 填 nextRecomputeAt
//   6. profileSave(profile)

// ─── Profile 识别 LLM Prompt (设计稿 §2.2) ──────────────────────────

const PROFILE_SYSTEM_PROMPT = `你是 catfish 员工画像分析师. 看以下数据,
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
const PROFILE_LLM_QUERY = "?catfish_source=companion-profile&catfish_skip_identity=1&catfish_internal=1&catfish_direct=1";

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
const PROFILE_JSON_SCHEMA = {
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

/** 调 LLM 真识别员工画像. 不挂 AbortSignal (Tauri suspend 经验).
 *  挂了返 null, caller 兜底用占位.
 *
 *  5/21 cold start 补丁: 并发拉 6 数据源 (3 catfish 自己的 + 3 外部, 后者解决 cold start):
 *    catfish 自己:
 *      - briefingContextFetch: distilled_facts / workplan / projects / sessions / weekly_reports
 *      - profileHintsRead: profile_hints.md (员工纠错痕迹)
 *    外部 (新员工也有):
 *      - emailListFetch: 邮件列表 (sender 频次 → 关键人脉, subject 词频 → 主管事务)
 *      - calendarWeekFetch: 过去 7 天日历 (参会人 → 团队 / 节奏)
 *      - catfish_style_fingerprint_get: 写作风格 (verbosity / structure / formality / signatureWords)
 */
async function inferProfileFromContext(model: string): Promise<Profile | null> {
  // 并发拉, 单点挂不影响其他
  const [ctxRes, hintsRes, emailRes, calRes, fpRes] = await Promise.allSettled([
    briefingContextFetch(),
    profileHintsRead(),
    emailListFetch(false, 100),    // 全部邮件, limit 100
    calendarWeekFetch(false),
    toolBridgeCallTool("catfish_style_fingerprint_get", {}),
  ]);

  const ctx = ctxRes.status === "fulfilled" ? ctxRes.value : null;
  const hints = hintsRes.status === "fulfilled" ? hintsRes.value : "";

  // 拼 user prompt 喂 LLM 全部画像相关数据
  const parts: string[] = [];

  // ─── 1) catfish 自己历史 (有就喂, 新员工大概率空) ───
  if (ctx) {
    if (ctx.distilledFacts.trim()) {
      parts.push(`# 长期记忆 distilled_facts\n${ctx.distilledFacts.trim()}`);
    }
    if (ctx.workplan.trim()) {
      parts.push(`# 员工自写工作计划 workplan.md\n${ctx.workplan.trim()}`);
    }
    if (ctx.projects.trim()) {
      parts.push(`# 项目跟踪 projects.md\n${ctx.projects.trim()}`);
    }
    if (ctx.recentSessionBriefs.length > 0) {
      const lines = ctx.recentSessionBriefs.slice(0, 10).map((s) => {
        const msg = (s.firstUserMessage || "").slice(0, 80);
        return `[${s.startedAt.slice(0, 10)}] ${s.title || "(无 title)"}: ${msg}`;
      });
      parts.push(`# 最近 7 天对话 sessions\n${lines.join("\n")}`);
    }
    if (ctx.weeklyReports.length > 0) {
      const lines = ctx.weeklyReports
        .slice(0, 5)
        .map((r) => `${r.modifiedAt.slice(0, 10)} ${r.filename}`);
      parts.push(`# 周报历史 outputs/weekly-*\n${lines.join("\n")}`);
    }
  }
  if (hints.trim()) {
    parts.push(`# 员工纠错痕迹 (profile_hints.md — 优先参考)\n${hints.trim()}`);
  }

  // ─── 2) 外部已有数据 (cold start 主源) ───

  // 2A. 邮件历史 (sender 频次 + subject 词频)
  if (emailRes.status === "fulfilled") {
    try {
      const arr = JSON.parse(emailRes.value);
      if (Array.isArray(arr) && arr.length > 0) {
        // 统计 sender top 15 + subject 前 50 列
        const senderCount: Record<string, number> = {};
        for (const m of arr) {
          const s = String(m.sender ?? "").trim();
          if (s) senderCount[s] = (senderCount[s] ?? 0) + 1;
        }
        const topSenders = Object.entries(senderCount)
          .sort((a, b) => b[1] - a[1])
          .slice(0, 15)
          .map(([s, c]) => `${s} × ${c}`);
        const subjectsList = arr
          .slice(0, 50)
          .map((m: { subject?: string }) => String(m.subject ?? "").slice(0, 60))
          .filter(Boolean);
        parts.push(
          `# 邮件来往人 top 15 (推关键人脉 / 上下级)\n${topSenders.join("\n")}\n\n` +
            `# 邮件主题 前 50 列 (推主管事务 / 项目)\n${subjectsList.join("\n")}`,
        );
      }
    } catch {
      /* ignore, JSON 解析挂 */
    }
  }

  // 2B. 日历过去 7 天 (参会人 + 主题)
  if (calRes.status === "fulfilled") {
    try {
      const arr = JSON.parse(calRes.value);
      if (Array.isArray(arr) && arr.length > 0) {
        const lines = arr.slice(0, 30).map((e: {
          start?: string;
          summary?: string;
          attendees?: string[];
        }) => {
          const date = String(e.start ?? "").slice(0, 10);
          const summary = String(e.summary ?? "").slice(0, 50);
          const att = Array.isArray(e.attendees) ? e.attendees.slice(0, 5).join(", ") : "";
          return `${date} ${summary}${att ? ` (与: ${att})` : ""}`;
        });
        parts.push(`# 日历过去 7 天事件 (推团队 / 节奏 / 项目)\n${lines.join("\n")}`);
      }
    } catch {
      /* ignore */
    }
  }

  // 2C. style_fingerprint (LLM 直接抽 personality)
  // tool_bridge_call_tool 返 {ok, tool, result, error}, style_fingerprint 内层 result 再含 {type, result}
  if (fpRes.status === "fulfilled") {
    const tcr = fpRes.value;
    const inner =
      tcr.ok && tcr.result && typeof tcr.result === "object"
        ? (tcr.result as { result?: { exists?: boolean } }).result
        : null;
    if (inner && inner.exists === true) {
      parts.push(
        `# style_fingerprint (员工写作风格, 5/6 BL-MM8 抽出)\n` +
          `${JSON.stringify(inner, null, 2)}`,
      );
    } else {
      parts.push(
        `# style_fingerprint: 不存在 (cold start). personality 字段从邮件 / 文档粗略推, sourceCount=0`,
      );
    }
  }

  if (parts.length === 0) {
    console.log("[profile] 全部 6 数据源空, 跳 LLM 推断");
    return null;
  }
  parts.push(
    "# 任务\n按 system prompt 出 JSON. 数据稀疏 → confidence 低. 不瞎填. " +
      "**含 personality 字段** (cold start 时 sourceCount=0 仍可推 verbosity/formality 粗略).",
  );

  // P3.4.9 (6/15 鸿波): user prompt 末尾 directive — 跟 PROFILE_SYSTEM_PROMPT 末尾
  //   BL-PROFILE-JSON-STRICT 双重保险. last word 帮 LLM 守住 JSON 约束.
  parts.push(
    "# 输出格式 (必读)\n\n" +
      "直接输出 JSON, 不要 \"Let me analyze\" / \"Key observations\" / \"让我分析\" 等 prefix.\n" +
      "第一个字符 = `{`, 最后一个字符 = `}`. 中间不要 markdown 反引号 / 加粗 / 列表标号.\n" +
      "见 SYSTEM_PROMPT § BL-PROFILE-JSON-STRICT.",
  );

  // P3.4.E (6/15 鸿波): URL 改 gatewayUrl (8999, LiteLLM passthrough) — bypass hermes
  //   8642 agent loop. hermes _handle_chat_completions (api_server.py:1820) 不读 client
  //   tools / tool_choice 字段, 走 8999 直连才能用 strict function calling.
  //   catfish_direct=1 query 让 me.ts isGatewayDirectPath 命中走 OAuth path.
  const url = `${config.gatewayUrl}/v1/chat/completions${PROFILE_LLM_QUERY}`;
  console.log("[profile] 调 LLM 推断, prompt 长度:", parts.join("\n\n").length);

  try {
    const resp = await fetchWithAuth(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        model,
        messages: [
          { role: "system", content: PROFILE_SYSTEM_PROMPT },
          { role: "user", content: parts.join("\n\n") },
        ],
        // P3.4.C (6/15 鸿波): 1000 → 2500.
        //   真因: profile keyPeople 10 人 + keyProjects 5 个 + evidence 3-5 条
        //   + personality 6 字段 = ~1500-2000 tokens 输出. max_tokens=1000 装不下,
        //   JSON 在 keyPeople 数组中间截断 (鸿波 6/15 console 验证). 2500 装下.
        max_tokens: 2500,
        temperature: 0.3,
        stream: false,
        // P3.4.10 (6/15 鸿波): 跟 briefing_advisor.ts 同款, OpenAI 协议强制 JSON.
        //   P3.4.E 加 tools + tool_choice 后这个 response_format 是 "双保险" — 即使
        //   LLM 不调 tool (边缘场景 fallback path), 仍走 json_object mode. 保留.
        response_format: { type: "json_object" },
        // P3.4.E (6/15 鸿波): OpenAI function calling 强制 LLM 必返 submit_profile tool_call.
        //   真因: P3.4.9 prompt + P3.4.10 response_format 都治标 — DeepSeek Flash reasoning
        //   倾向仍把 free-text reasoning 当 content 返 (鸿波 6/15 console 实测).
        //   单 tool_choice="required" 让 LLM 必返 tool_calls, 不允许 free-text content.
        //   schema 给字段级硬约束 (DeepSeek beta endpoint 拒不匹配 schema 的 args).
        //   走 8999 直连 LiteLLM 才能透传 (hermes 8642 agent loop 不读 client tools).
        tools: [
          {
            type: "function",
            function: {
              name: "submit_profile",
              description: "提交员工画像识别结果. 必须调这个 tool, 不允许 free-text content.",
              parameters: PROFILE_JSON_SCHEMA,
            },
          },
        ],
        tool_choice: { type: "function", function: { name: "submit_profile" } },
      }),
    });
    if (!resp.ok) {
      console.warn("[profile] LLM HTTP", resp.status);
      return null;
    }
    const data = await resp.json();
    const msg = data?.choices?.[0]?.message;

    // P3.4.E (6/15 鸿波) primary path: 拿 tool_calls[0].function.arguments (100% JSON Schema 校验过).
    const toolCalls = msg?.tool_calls;
    if (Array.isArray(toolCalls) && toolCalls.length > 0) {
      const args = toolCalls[0]?.function?.arguments;
      if (typeof args === "string" && args.trim()) {
        try {
          const parsed = JSON.parse(args);
          console.log("[profile] P3.4.E tool_call 路径成功, args 长度:", args.length);
          return parseProfileFromLLM(parsed);
        } catch (e) {
          console.warn("[profile] P3.4.E tool_call args JSON.parse 挂 (DeepSeek beta truncate?):", e);
          // 落到 robustJsonParse fallback
          const fallback = robustJsonParse(args);
          if (fallback) return parseProfileFromLLM(fallback);
        }
      }
    }

    // Fallback path: LLM 没遵守 tool_choice (DeepSeek 边缘场景), 回退 content + robustJsonParse.
    //   P3.4.10 response_format=json_object 仍生效, content 应该是 JSON-ish.
    const content = msg?.content;
    if (typeof content !== "string") {
      console.warn("[profile] LLM 返既没 tool_calls 也没 string content:", msg);
      return null;
    }
    console.log("[profile] P3.4.E tool_call 路径未命中, fallback content + robustJsonParse");
    const parsed = robustJsonParse(content);
    if (parsed === null) {
      console.warn("[profile] LLM 返非 JSON, 原文前 200:", content.slice(0, 200));
      return null;
    }
    return parseProfileFromLLM(parsed);
  } catch (e) {
    console.warn("[profile] LLM 调用挂:", e);
    return null;
  }
}

/** 鲁棒 JSON 解析 — LLM 输出常含前后解释文字 (e.g. "现在我已经分析完..."),
 *  剥 markdown 反引号 + brace-balanced match 找第一个完整 `{...}` 块.
 *  失败返 null, 不抛.
 *
 *  P3.4.C (6/15 鸿波): 改算法 indexOf+lastIndexOf → brace counting balanced.
 *  跟 briefing_advisor.ts robustJsonParse 同款修, 救 LLM truncated JSON.
 *  老算法在 LLM 输出 truncated 时挂 (lastIndexOf("}") 命中 reasoning 引用里的
 *  `}` 不是 JSON 闭合, 截出来不合法).
 */
function robustJsonParse(content: string): unknown | null {
  if (typeof content !== "string") return null;
  const s = content
    .trim()
    .replace(/^```(?:json)?\s*/i, "")
    .replace(/```$/, "")
    .trim();
  // 1. 直接 parse 试一次
  try {
    return JSON.parse(s);
  } catch {
    /* 落空走 brace counting */
  }
  // 2. brace counting — 找第一个 balanced `{...}` 跳过 reasoning 里的 `{}` 引用
  const first = s.indexOf("{");
  if (first < 0) return null;
  let depth = 0;
  let inString = false;
  let escape = false;
  for (let i = first; i < s.length; i++) {
    const c = s[i];
    if (escape) {
      escape = false;
      continue;
    }
    if (c === "\\" && inString) {
      escape = true;
      continue;
    }
    if (c === '"') {
      inString = !inString;
      continue;
    }
    if (inString) continue;
    if (c === "{") depth++;
    else if (c === "}") {
      depth--;
      if (depth === 0) {
        try {
          return JSON.parse(s.slice(first, i + 1));
        } catch {
          return null;
        }
      }
    }
  }
  return null;  // LLM 输出真截断, JSON 未闭合
}

/** 5/22 cold start 修锁: 同时只允许一个 recompute 跑.
 *  React StrictMode dev 模式 useEffect 双调 → ensureProfileFresh 双跑 → LLM 调 2 次浪费 token. */
let _recomputingProfile: Promise<Profile | null> | null = null;

function parseProfileFromLLM(raw: unknown): Profile | null {
  if (!raw || typeof raw !== "object") return null;
  const obj = raw as Record<string, unknown>;

  const tier = obj.tier;
  if (tier !== "frontline" && tier !== "mid" && tier !== "senior") {
    console.warn("[profile] tier 非法:", tier);
    return null;
  }
  const centralStateRaw = obj.centralState ?? obj.central_state;
  const centralState: "none" | "weak" | "strong" =
    centralStateRaw === "strong" ? "strong" : centralStateRaw === "weak" ? "weak" : "none";

  const style = typeof obj.style === "string" ? obj.style : "未识别";
  const confidence =
    typeof obj.confidence === "number" ? Math.max(0, Math.min(1, obj.confidence)) : 0.5;

  const keyPeople: KeyPerson[] = [];
  const rawPpl = obj.keyPeople ?? obj.key_people;
  if (Array.isArray(rawPpl)) {
    for (const p of rawPpl.slice(0, 10)) {
      if (!p || typeof p !== "object") continue;
      const pp = p as Record<string, unknown>;
      const name = typeof pp.name === "string" ? pp.name : null;
      const relation = typeof pp.relation === "string" ? pp.relation : null;
      if (!name || !relation) continue;
      keyPeople.push({
        name,
        relation,
        project: typeof pp.project === "string" ? pp.project : undefined,
        lastContact:
          typeof pp.lastContact === "string"
            ? pp.lastContact
            : typeof pp.last_contact === "string"
            ? pp.last_contact
            : undefined,
      });
    }
  }

  const keyProjects: KeyProject[] = [];
  const rawProj = obj.keyProjects ?? obj.key_projects;
  if (Array.isArray(rawProj)) {
    for (const p of rawProj.slice(0, 5)) {
      if (!p || typeof p !== "object") continue;
      const pp = p as Record<string, unknown>;
      const name = typeof pp.name === "string" ? pp.name : null;
      const status = typeof pp.status === "string" ? pp.status : null;
      if (!name || !status) continue;
      keyProjects.push({
        name,
        status,
        client: typeof pp.client === "string" ? pp.client : undefined,
        deadline: typeof pp.deadline === "string" ? pp.deadline : undefined,
      });
    }
  }

  const evidence: string[] = [];
  if (Array.isArray(obj.evidence)) {
    for (const e of obj.evidence) {
      if (typeof e === "string") evidence.push(e);
    }
  }

  // 5/21 cold start: personality 字段 (Optional)
  let personality: Personality | undefined;
  const rawPers = obj.personality;
  if (rawPers && typeof rawPers === "object") {
    const pp = rawPers as Record<string, unknown>;
    const vRaw = String(pp.verbosity ?? "balanced");
    const sRaw = String(pp.structure ?? "balanced");
    const fRaw = String(pp.formality ?? "balanced");
    const verbosity: Verbosity =
      vRaw === "concise" ? "concise" : vRaw === "verbose" ? "verbose" : "balanced";
    const structure: Structure =
      sRaw === "list_heavy" ? "list_heavy" : sRaw === "prose_heavy" ? "prose_heavy" : "balanced";
    const formality: Formality =
      fRaw === "formal" ? "formal" : fRaw === "casual" ? "casual" : "balanced";
    const signatureWords: string[] = [];
    if (Array.isArray(pp.signatureWords ?? pp.signature_words)) {
      for (const w of (pp.signatureWords ?? pp.signature_words) as unknown[]) {
        if (typeof w === "string") signatureWords.push(w);
      }
    }
    const sampleSentences: string[] = [];
    if (Array.isArray(pp.sampleSentences ?? pp.sample_sentences)) {
      for (const s of (pp.sampleSentences ?? pp.sample_sentences) as unknown[]) {
        if (typeof s === "string") sampleSentences.push(s);
      }
    }
    const sourceCount =
      typeof pp.sourceCount === "number"
        ? pp.sourceCount
        : typeof pp.source_count === "number"
        ? pp.source_count
        : 0;
    personality = {
      verbosity,
      structure,
      formality,
      signatureWords: signatureWords.slice(0, 10),
      sampleSentences: sampleSentences.slice(0, 3),
      sourceCount,
    };
  }

  return {
    tier,
    centralState,
    style,
    keyPeople,
    keyProjects,
    confidence,
    evidence: evidence.length > 0 ? evidence : ["LLM 推断, 无显式 evidence 字段"],
    personality,
    updatedAt: new Date().toISOString(),
    nextRecomputeAt: "",  // caller 填
  };
}

/** Phase 7 第 4 步真接入: 调 LLM 出 profile JSON, 写回 profile.json.
 *  LLM 挂了 / 数据全空 → 写一个 confidence=0 占位 (UI 显"识别中" 引导员工多用 catfish).
 *
 *  5/22 cold start 修: in-flight 锁防 React StrictMode dev 双调浪费 token.
 *
 *  model: 必传, 跟员工 chat model 同款 (5/17 BL-INTERNAL-MODEL-FOLLOW-USER 原则).
 *         caller (AdvisorView) 从 useChatStore.model 取. 没传或空 → 拒跑返 null.
 */
export async function recomputeProfile(model: string): Promise<Profile | null> {
  // 已经在跑 → 复用同一个 promise
  if (_recomputingProfile) {
    console.log("[profile] recompute 已在跑, 复用 in-flight promise");
    return _recomputingProfile;
  }
  if (!model || typeof model !== "string" || !model.trim()) {
    console.warn("[profile] recompute 拒跑: model 空");
    return null;
  }

  const m = model;

  _recomputingProfile = (async () => {
    const nextRecomputeAt = await profileNextRecomputeAt(7);

    // P3.3.28 (6/11): 加 race timeout, 跟 P3.3.25 advisor 同款思路.
    //   早安卡在"员工画像识别中" 真因: catfish-private-main TTFT 86s 极慢
    //   (gateway log 自己 warning "上游可能拥堵"), 老代码同步 await 永远挂.
    //   timeout 超时返 placeholder 或 saved profile (见 fix 段) → AdvisorView 走 no_profile.
    //   后台 inferProfileFromContext 跑完仍会 profileSave (覆盖 placeholder),
    //   下次 ensureRecomputed 命中 saved profile, 不用再调 LLM.
    // 6/11 follow-up: 60_000 → 180_000 跟 advisor 一致. 60s 私有模型撑挂概率太
    //   大每次都 race timeout, 真值进不来. 180s 给私有模型足够时间.
    // P3.4.8 (6/15 鸿波): 180_000 → 300_000 跟 advisor 一致. 同样背景:
    //   profile 推断也走 hermes agent loop, 不止单次 LLM call. 180s 撑不下来.
    // P3.5.32.6 (6/18 鸿波 catch '完全卡死'): 300_000 → 600_000.
    // 真因 + 同步 fix 详见 briefing_advisor.ts:731 注释. 鸿波本机 catfish-private-main
    // profile 单 LLM call 116s, 300s 不够大 buffer.
    const LLM_TIMEOUT_MS = 600_000;
    const llmPromise = inferProfileFromContext(m);

    // 后台 LLM 跑完总是写 saved (race 输了也写 — 下次 mount 命中)
    void llmPromise
      .then(async (llm) => {
        if (!llm) return;
        llm.nextRecomputeAt = nextRecomputeAt;
        await profileSave(llm).catch((e) =>
          console.warn("[profile] 后台 save 挂:", e),
        );
        console.log("[profile] LLM 推断 OK (后台写)", {
          tier: llm.tier,
          centralState: llm.centralState,
          confidence: llm.confidence,
        });
      })
      .catch((e) => console.warn("[profile] 后台 LLM 挂:", e));

    // P3.4.3 (6/15 鸿波): timer id 拿出来 race resolve 后 clearTimeout. 跟
    //   briefing_advisor.ts:raceWithTimeout 同款修. 老 setTimeout 没 clear —
    //   race 已 resolve (后台 LLM 早返, 或者 inferProfileFromContext 命中已
    //   cache 占位走快路径) 后, 180s 那个 setTimeout 仍 spew console.warn,
    //   误导 debug. 6/15 鸿波早安卡 console 一起被两条误报警告 (advisor +
    //   profile) 带偏方向.
    //
    //   修法: setTimeout 回调里只 resolve, console.warn 移到 race 后看
    //   winner === "TIMEOUT" 才打. finally clearTimeout 防 race 赢后 timer
    //   残留 fire.
    let timerId: ReturnType<typeof setTimeout> | undefined;
    const timeoutPromise = new Promise<"TIMEOUT">((resolve) => {
      timerId = setTimeout(() => resolve("TIMEOUT"), LLM_TIMEOUT_MS);
    });

    let winner: Profile | null | "TIMEOUT";
    try {
      winner = await Promise.race<Profile | null | "TIMEOUT">([
        llmPromise,
        timeoutPromise,
      ]);
      if (winner === "TIMEOUT") {
        console.warn(
          `[profile] LLM 客户端 ${LLM_TIMEOUT_MS / 1000}s 超时, 返占位 ` +
            "(后台 fetch 仍在跑, 完成会写 cache, 下次 mount 命中)",
        );
      }
    } finally {
      if (timerId !== undefined) clearTimeout(timerId);
    }

    if (winner !== "TIMEOUT" && winner) {
      // LLM 在 60s 内完成 — 直接返 (后台 then 块也会再写一遍, save 幂等)
      winner.nextRecomputeAt = nextRecomputeAt;
      await profileSave(winner);
      console.log("[profile] LLM 推断 OK", {
        tier: winner.tier,
        centralState: winner.centralState,
        confidence: winner.confidence,
      });
      return winner;
    }

    // P3.3.28 fix (6/11): race timeout 时**不要覆盖已有 saved profile**.
    //   之前 bug: race timeout 写 placeholder confidence=0 → 覆盖了之前 0.88
    //   的好 profile → AdvisorView ConfidenceHint 显"🪴刚认识你"黄条假性提示.
    //   修: timeout 时先看有没 saved, 有就返 saved (保留 0.88), 没才写 placeholder.
    //   后台 LLM 完成仍会 profileSave 真值 (覆盖 placeholder 或更新 saved).
    //
    // P3.3.56 fix (6/12 鸿波撞 confidence=0 占位回潮): P3.3.28 只保护 TIMEOUT 路径,
    //   没保护 inferProfileFromContext 内 9 个 return null 路径 (LLM 调用挂 /
    //   JSON parse 失败 / schema 错). winner=null 仍走 placeholder 覆盖 saved
    //   0.88. 把 existing check 提到 fallback 统一入口, TIMEOUT 跟 null 两路径
    //   都先看 existing.
    const existing = await profileGet().catch(() => null);
    if (existing && existing.confidence > 0) {
      const reason = winner === "TIMEOUT" ? "race timeout" : "LLM 返 null";
      console.log(
        `[profile] ${reason}, 但 existing.confidence=${existing.confidence} > 0, ` +
          "不覆盖 saved (后台 LLM 完成仍会更新)",
      );
      return existing;
    }

    // 真没 existing (新员工首启) 或 existing 已经是 placeholder (confidence=0) → 写 placeholder
    const placeholder: Profile = {
      tier: "mid",
      centralState: "weak",
      style: "未识别",
      keyPeople: [],
      keyProjects: [],
      confidence: 0.0,
      evidence:
        winner === "TIMEOUT"
          // P3.4.8 (6/15 鸿波): 文案 60s → 5min, 跟 LLM_TIMEOUT_MS=300_000 同步.
          //   原因: profile 推断也走 hermes agent loop, 不止单次 LLM call.
          ? ["LLM 推断 >5min 超时 (agent loop 跑不完), 占位中. 下次 mount 后台完成会自动更新"]
          : ["LLM 推断挂 / 数据稀疏, 占位中. 用 catfish 几天后自动校准"],
      updatedAt: new Date().toISOString(),
      nextRecomputeAt,
    };
    await profileSave(placeholder);
    return placeholder;
  })();

  try {
    return await _recomputingProfile;
  } finally {
    _recomputingProfile = null;  // 释放锁, 下次允许新一轮
  }
}

/** 5/22 cold start 改: 同步版 — 等 recompute 完返回 profile.
 *
 *  逻辑:
 *    - force=true: 不管现状, 强制重算并等结果
 *    - 否则: profileNeedsRecompute → 若 true 等 recompute, 若 false 直接返 profileGet
 *
 *  model: 跟员工 chat model 同款 (5/17 BL-INTERNAL-MODEL-FOLLOW-USER).
 *         Caller (AdvisorView) 从 useChatStore.model 取. 空时拒跑返 null.
 */
export async function ensureRecomputed(
  model: string,
  force = false,
): Promise<Profile | null> {
  if (!force) {
    try {
      const needs = await profileNeedsRecompute(false);
      if (!needs) {
        return await profileGet();
      }
    } catch (e) {
      console.warn("[profile] needs_recompute 检查失败, 仍尝试 recompute:", e);
    }
  }
  // 等 recompute (in-flight 锁防 StrictMode 双调)
  return await recomputeProfile(model);
}


/** App 启动时调一次. 后台 fire-and-forget. 不阻塞 UI.
 *
 *  逻辑:
 *    - profile 不存在 → 后台跑 recompute, 占位先用上
 *    - profile 存在但过期 / confidence < 0.3 (占位) → 后台跑 recompute (5/22 修)
 *    - profile 存在且未过期 + confidence >= 0.3 → 跳过
 *
 *  5/22 cold start 修: 加 onDone 回调, recompute 完通知 UI re-load (防 LLM 第一次挂
 *  写占位后 UI 永远显"识别中"). force=true 时跳过 needs check 直接重算 (刷新按钮用).
 */
export function ensureProfileFresh(
  model: string,
  force = false,
  onDone?: (p: Profile | null) => void,
): void {
  void (async () => {
    try {
      const needs = force || (await profileNeedsRecompute(false));
      if (!needs) return;
      // 后台异步
      const p = await recomputeProfile(model).catch((e) => {
        console.warn("[profile] recompute 失败:", e);
        return null;
      });
      onDone?.(p);
    } catch (e) {
      console.warn("[profile] needs_recompute 检查失败:", e);
      onDone?.(null);
    }
  })();
}
