/** BL-ADVISOR-BRIEFING (5/21 Phase 7 第 4+5 步): 智能参谋早安 LLM 调用层.
 *
 * 设计稿: docs/CATFISH-ADVISOR-DESIGN.md §5 (SYSTEM_PROMPT) + §7 (UI 渲染输入)
 *
 * 替代 Phase 6 的 briefing_workplan.ts (已砍). 核心差异:
 *   - prompt 让 LLM 当"参谋"不是"作家" — 不出总结, 出主菜 + 选项 + 草稿调用
 *   - 按 profile.tier 出不同粒度的主菜数 (一线 5-8 / 中层 3-4 / 高层 1-2 + 异常)
 *   - 央国企信号 strong 时主动调 check_compliance + political_sensitivity_scan
 *   - 严格 JSON 输出 (UI 渲染成 ActionCard list)
 *
 * 调用模式 (跟 5/21 学到的 Tauri webview suspend 经验):
 *   - **不挂 AbortSignal** — Tauri 失焦会 abort in-flight fetch
 *   - 走 backendUrl (8642 hermes proxy) + ?catfish_skip_identity=1 query (5/21 CORS 修)
 *   - 调 fetchMergedBriefing 同款 fetchWithAuth
 *
 * 注: 这里只管 LLM 调用 + 解析. 数据采集 (emails / events / todos / profile / ctx)
 * 由 caller (AdvisorView component) 拉好传进来. 单一职责.
 */

import { config } from "./env";
import { fetchWithAuth } from "./me";
import type { Profile } from "./profile";
import type {
  BriefingContext,
  CalendarEvent,
  EmailDigestItem,
  JournalTodo,
} from "./tauri";

// 跟 briefing_workplan / briefing 同款 — 不带 X-Catfish-* header, 走 query param (5/21 CORS 修)
const SERVICE_LLM_HEADERS = { "Content-Type": "application/json" };
const SERVICE_LLM_QUERY = "?catfish_source=companion-advisor&catfish_skip_identity=1&catfish_internal=1";

// ─── 输出 schema (UI ActionCard 渲染输入) ───────────────────────

export interface AdvisorOption {
  /** "A" / "B" / "C" */
  label: string;
  /** "strict" / "balanced" / "friendly" / "formal" / "urgent" / "hold" 等 */
  tone: string;
  /** 这个选项的一句话总结 (给员工选时用) */
  summary: string;
  /** LLM 倾向哪个 — 唯一一条 true. UI 角标"我倾向" */
  aiLean?: boolean;
  /** 已存草稿绝对路径 (调 draft_email_reply / draft_meeting_brief 后填) */
  draftPath?: string;
}

export interface ComplianceFlag {
  type: string;       // "iso_audit_relevant" 等
  severity: "high" | "medium" | "low";
  reason: string;
  matchedKeyword?: string;
  suggestion?: string;
}

export interface PoliticalFlag {
  type: string;
  severity: "high" | "medium" | "low";
  person?: string;
  reason: string;
  matchedKeyword?: string;
  suggestedPhrasings?: string[];
  advisoryOnly?: boolean;  // senior tier + high 时 true, UI 渲染"提醒"非"建议改"
}

export interface MainTask {
  /** 当天主菜 id (LLM 自己排 1, 2, 3...) */
  id: number;
  /** 一句话标题 */
  title: string;
  /** "high" / "medium" / "low" — UI 边框颜色 */
  urgency: "high" | "medium" | "low";
  /** 为什么这件事在主菜 (一句话) */
  reason?: string;
  /** 2-3 个口径选项 (高层异常型主菜可以 0 选项, 只列例外) */
  options: AdvisorOption[];
  /** check_compliance 跑的结果 */
  complianceFlags: ComplianceFlag[];
  /** political_sensitivity_scan 跑的结果 */
  politicalFlags: PoliticalFlag[];
  /** 上下文引用 — UI 用来显示 "🔗 历史: ..." */
  contextRefs: string[];
}

export interface HandledSilentlyItem {
  /** "email_archive" / "calendar_accept" / "todo_dedup" */
  type: string;
  /** 多少件 */
  count: number;
  /** 一句话理由 */
  category: string;
}

export interface AdvisorResult {
  /** 跟 profile.tier 一致 — LLM 输出时回显, 让 UI 知道是按哪个 tier 渲染的 */
  tier: "frontline" | "mid" | "senior";
  /** 主菜 list, 按 tier 决定数量 */
  mainTasks: MainTask[];
  /** 已默认处理的事 (UI 折叠区) */
  handledSilently: HandledSilentlyItem[];
}

// ─── SYSTEM_PROMPT (设计稿 §5) ────────────────────────────────────

const SYSTEM_PROMPT = `你是 catfish — 中国央国企员工的智能参谋. 严格按以下规则工作:

# 角色边界 (不能违反)
1. 你是参谋, **不是代理**. 任何级别都不替员工拍板.
2. 你**不替员工**发邮件 / 接受会议 / 签字 / 拍板任何事.
3. 你**只**起草到 outputs/ 让员工自己看/改/发.
4. 你的输出是: 主菜识别 + 已准备好的材料 + 建议选项 + 风险提示.

# 工作步骤

1. **看完全部信息**, 内部关联推理. 不要分块看, 把人/项目/历史/事件横向连起来.

2. **按 profile.tier 识别主菜**:
   - frontline: 5-8 件具体 TODO, 按时间排
   - mid: 3-4 件项目级主菜 (团队进度 + 风险 + 汇报)
   - senior: 1-2 件战略级 + 异常例外 + 关键关系节点

3. **每件主菜调对应 tool**:
   - 涉及邮件回复 → catfish_draft_email_reply (起 2-3 个口径, 不同 tone 各调一次)
   - 涉及会议汇报 → catfish_draft_meeting_brief (起 brief)
   - 涉及催办 → catfish_compose_followup_list
   - 涉及决策 → catfish_recall_decision_history (拉历史口径, 不背离)

4. **central_state=strong 时, 每件主菜额外跑扫描**:
   - 邮件 / 汇报草稿 → catfish_check_compliance
   - 涉及关键人物 → catfish_political_sensitivity_scan

5. **输出严格 JSON** (顶层不含 markdown 反引号, 不含前缀文字):
{
  "tier": "mid",
  "main_tasks": [
    {
      "id": 1,
      "title": "老李催资质方案范围",
      "urgency": "high",
      "reason": "影响项目 A 客户关系",
      "options": [
        {"label": "A", "tone": "strict", "summary": "紧扣 5/18 班子会边界"},
        {"label": "B", "tone": "balanced", "summary": "微调保留余地", "aiLean": true, "draftPath": "/Users/.../reply-laoli-balanced.md"},
        {"label": "C", "tone": "hold", "summary": "暂缓回复, 周一面谈"}
      ],
      "complianceFlags": [
        {"type": "iso_audit_relevant", "severity": "medium", "reason": "类似回复去年被 ISO 审计追问", "matchedKeyword": "资质方案", "suggestion": "B 口径稳, 留档备查"}
      ],
      "politicalFlags": [],
      "contextRefs": ["5/14 你跟老李电话定的口径"]
    }
  ],
  "handled_silently": [
    {"type": "email_archive", "count": 47, "category": "低优先归档"},
    {"type": "calendar_accept", "count": 2, "category": "非关键会议 tentative"}
  ]
}

# 强约束 (重申)
- 不允许"建议你 X" 这种被动建议. 改为"我起草了 A/B 两个口径, 你点这里看".
- 不允许"出总结". 总结是 Phase 6 的错路.
- 必须结构化 JSON, 不允许返一段散文.
- options 里 aiLean=true 的最多 1 条 (倾向只一个).
- 高层 tier (senior) 可以出现"异常例外型" 主菜 — options 可以为空 [], 只列风险.
`;

// ─── 拼 user prompt ──────────────────────────────────────────────

function buildUserPrompt(input: AdvisorInput): string {
  const { profile, emails, events, todos, ctx, urgencyMap, sessionGoal } = input;
  const parts: string[] = [];

  const today = new Date();
  parts.push(`# 时间锚点
今天: ${today.toLocaleDateString("zh-CN", { month: "long", day: "numeric", weekday: "long" })}
本周一起算 (中层 + 高层视野).`);

  // ─── 员工画像 ───
  parts.push(`# 员工画像 (catfish 自动识别)
- tier: ${profile.tier}
- central_state: ${profile.centralState}
- style: ${profile.style}
- confidence: ${profile.confidence.toFixed(2)}
${
  profile.keyPeople.length > 0
    ? `- 关键人脉:\n${profile.keyPeople
        .slice(0, 10)
        .map((p) => `  - ${p.name} (${p.relation}${p.project ? `, 项目 ${p.project}` : ""})`)
        .join("\n")}`
    : "- 关键人脉: (未识别)"
}
${
  profile.keyProjects.length > 0
    ? `- 重点项目:\n${profile.keyProjects
        .slice(0, 5)
        .map((p) => `  - ${p.name} (${p.status}${p.client ? `, 客户 ${p.client}` : ""})`)
        .join("\n")}`
    : "- 重点项目: (未识别)"
}`);

  if (sessionGoal.trim()) {
    parts.push(`# 员工今日重点 (自己设的)\n${sessionGoal.trim()}`);
  }
  if (ctx.workplan.trim()) {
    parts.push(`# 员工本周计划 (自己写的)\n${ctx.workplan.trim()}`);
  }
  if (ctx.projects.trim()) {
    parts.push(`# 项目跟踪 (员工自维护)\n${ctx.projects.trim()}`);
  }
  if (ctx.distilledFacts.trim()) {
    parts.push(`# 关于这个员工 (长期画像)\n${ctx.distilledFacts.trim()}`);
  }

  // 周报历史 (文件名 + 时间, 不读内容)
  if (ctx.weeklyReports.length > 0) {
    const lines = ctx.weeklyReports
      .slice(0, 5)
      .map((r) => `${r.modifiedAt.slice(0, 10)} ${r.filename}`);
    parts.push(`# 周报历史 (员工已生成过的)\n${lines.join("\n")}`);
  }

  // 7 天 session
  if (ctx.recentSessionBriefs.length > 0) {
    const lines = ctx.recentSessionBriefs.slice(0, 10).map((s) => {
      const msg = (s.firstUserMessage || "").slice(0, 80);
      return `[${s.startedAt.slice(0, 10)}] ${s.title || "(无 title)"}: ${msg}`;
    });
    parts.push(`# 最近 7 天对话\n${lines.join("\n")}`);
  }

  // 邮件 + 评级
  if (emails.length > 0) {
    const lines = emails.slice(0, 20).map((m) => {
      const u = urgencyMap[m.id] ?? "未评";
      return `[${u}] ${m.sender}: ${m.subject}`;
    });
    parts.push(`# 今日邮件 (${emails.length} 封, 前 20 列, 含 LLM 评级)\n${lines.join("\n")}`);
  }

  // 日历
  if (events.length > 0) {
    const lines = events.map((e) => {
      const time = e.all_day ? "全天" : `${e.start} - ${e.end}`;
      return `${time} ${e.summary}${e.location ? ` @ ${e.location}` : ""}`;
    });
    parts.push(`# 今日日程 (${events.length} 件)\n${lines.join("\n")}`);
  }

  // TODO
  if (todos.length > 0) {
    const lines = todos.map((t) => {
      const star = t.is_priority ? "⭐ " : "";
      return `${star}${t.text} (${t.source})`;
    });
    parts.push(`# 工作计划 TODO (${todos.length} 件)\n${lines.join("\n")}`);
  }

  parts.push(`# 任务
按 system prompt 指示, 出 JSON. 严格按 tier=${profile.tier} 的粒度:
${
  profile.tier === "frontline"
    ? "5-8 件具体 TODO 按时间排, 简单口径建议."
    : profile.tier === "mid"
    ? "3-4 件项目级主菜, 含团队进度风险 + 汇报草稿."
    : "1-2 件战略级 + 异常例外 + 关键关系节点."
}
${
  profile.centralState === "strong"
    ? "央国企信号强 — 每件主菜必须跑 catfish_check_compliance + (涉及关键人时) catfish_political_sensitivity_scan."
    : "央国企信号弱 — 跳过合规/政治扫描."
}`);

  return parts.join("\n\n");
}

// ─── 入口 fetchBriefingAdvisor ──────────────────────────────────

export interface AdvisorInput {
  profile: Profile;
  emails: EmailDigestItem[];
  events: CalendarEvent[];
  todos: JournalTodo[];
  ctx: BriefingContext;
  urgencyMap: Record<string, string>;
  sessionGoal: string;
  model: string;
}

/** 5/22 cold start 修锁: 同时只允许一个 advisor LLM call 跑.
 *  React StrictMode dev useEffect 双调 → fetchBriefingAdvisor 双跑 → LLM 调 2 次浪费 token + quota.
 *  跟 recomputeProfile 同款 in-flight 锁. */
let _advisorInFlight: Promise<AdvisorResult | null> | null = null;

/** 主入口. 不挂 AbortSignal (Tauri webview suspend 经验, 5/21 学到). */
export async function fetchBriefingAdvisor(input: AdvisorInput): Promise<AdvisorResult | null> {
  // in-flight 锁: 已在跑就复用 promise
  if (_advisorInFlight) {
    console.log("[advisor] 已在跑, 复用 in-flight promise (StrictMode 双调防御)");
    return _advisorInFlight;
  }

  _advisorInFlight = _fetchBriefingAdvisorImpl(input);
  try {
    return await _advisorInFlight;
  } finally {
    _advisorInFlight = null;
  }
}

async function _fetchBriefingAdvisorImpl(input: AdvisorInput): Promise<AdvisorResult | null> {
  console.log("[advisor] 调用开始", {
    tier: input.profile.tier,
    centralState: input.profile.centralState,
    confidence: input.profile.confidence,
    emails: input.emails.length,
    events: input.events.length,
    todos: input.todos.length,
    model: input.model,
  });

  // profile 占位 (confidence=0) 跳过 — Phase 7 第 1 步占位时不调 LLM
  if (input.profile.confidence === 0) {
    console.log("[advisor] profile confidence=0 (占位), 不调 LLM");
    return null;
  }

  // 数据全空保护
  if (input.emails.length === 0 && input.events.length === 0 && input.todos.length === 0) {
    console.log("[advisor] 数据全空, 不调 LLM");
    return null;
  }

  const userPrompt = buildUserPrompt(input);
  const url = `${config.backendUrl}/v1/chat/completions${SERVICE_LLM_QUERY}`;
  console.log("[advisor] 发 fetch:", url, "prompt 长度:", userPrompt.length);

  // 不挂 AbortSignal — Tauri webview 失焦会 suspend, 让 fetch 自己生命周期
  try {
    const resp = await fetchWithAuth(url, {
      method: "POST",
      headers: SERVICE_LLM_HEADERS,
      body: JSON.stringify({
        model: input.model,
        messages: [
          { role: "system", content: SYSTEM_PROMPT },
          { role: "user", content: userPrompt },
        ],
        max_tokens: 3000,
        temperature: 0.4,
        stream: false,
      }),
    });

    console.log("[advisor] HTTP status =", resp.status);
    if (!resp.ok) {
      const text = await resp.text().catch(() => "");
      console.warn("[advisor] LLM 非 2xx:", resp.status, text.slice(0, 200));
      return null;
    }

    const data = await resp.json();
    const content = data?.choices?.[0]?.message?.content;
    if (typeof content !== "string") {
      console.warn("[advisor] LLM 返非字符串 content:", content);
      return null;
    }
    console.log("[advisor] raw content (前 400):", content.slice(0, 400));

    // 5/22 cold start 修: 鲁棒 JSON 解析 — LLM 输出常含前后解释文字
    // (e.g. "现在我已经分析完..."), 不只 strip markdown 反引号.
    const parsed = robustJsonParse(content);
    if (parsed === null) {
      console.warn("[advisor] JSON 解析失败 (LLM 返非 JSON), 原文前 200:",
        content.slice(0, 200));
      return null;
    }

    return parseAdvisorResult(parsed);
  } catch (e) {
    console.warn("[advisor] LLM 调用挂:", e);
    return null;
  }
}

// ─── 鲁棒 JSON 解析 (5/22 cold start 修) ──────────────────────────

/** LLM 输出常含前后解释文字, 剥 markdown 反引号 + 截 `{...}` 之间.
 *  失败返 null, 不抛. */
function robustJsonParse(content: string): unknown | null {
  if (typeof content !== "string") return null;
  let s = content
    .trim()
    .replace(/^```(?:json)?\s*/i, "")
    .replace(/```$/, "")
    .trim();
  try {
    return JSON.parse(s);
  } catch {
    /* 落空走截取 */
  }
  const first = s.indexOf("{");
  const last = s.lastIndexOf("}");
  if (first >= 0 && last > first) {
    s = s.slice(first, last + 1);
    try {
      return JSON.parse(s);
    } catch {
      return null;
    }
  }
  return null;
}

// ─── 解析 LLM JSON 输出 ────────────────────────────────────────

function parseAdvisorResult(raw: unknown): AdvisorResult | null {
  if (!raw || typeof raw !== "object") return null;
  const obj = raw as Record<string, unknown>;

  const tier = obj.tier;
  if (tier !== "frontline" && tier !== "mid" && tier !== "senior") {
    console.warn("[advisor] tier 非法:", tier);
    return null;
  }

  const rawTasks = obj.main_tasks ?? obj.mainTasks;
  if (!Array.isArray(rawTasks)) {
    console.warn("[advisor] main_tasks 不是数组:", rawTasks);
    return null;
  }

  const mainTasks: MainTask[] = [];
  for (const t of rawTasks) {
    if (!t || typeof t !== "object") continue;
    const task = parseMainTask(t as Record<string, unknown>);
    if (task) mainTasks.push(task);
  }

  // handledSilently / handled_silently 兼容 snake/camel
  const rawHandled = obj.handled_silently ?? obj.handledSilently ?? [];
  const handledSilently: HandledSilentlyItem[] = [];
  if (Array.isArray(rawHandled)) {
    for (const h of rawHandled) {
      if (!h || typeof h !== "object") continue;
      const it = h as Record<string, unknown>;
      const type = typeof it.type === "string" ? it.type : null;
      const count = typeof it.count === "number" ? it.count : null;
      if (!type || count === null) continue;
      handledSilently.push({
        type,
        count,
        category: typeof it.category === "string" ? it.category : "",
      });
    }
  }

  return { tier, mainTasks, handledSilently };
}

function parseMainTask(t: Record<string, unknown>): MainTask | null {
  const title = t.title;
  if (typeof title !== "string" || !title.trim()) return null;
  const id = typeof t.id === "number" ? t.id : 0;

  const urgencyRaw = typeof t.urgency === "string" ? t.urgency.toLowerCase() : "medium";
  const urgency: "high" | "medium" | "low" =
    urgencyRaw === "high" ? "high" : urgencyRaw === "low" ? "low" : "medium";

  const reason = typeof t.reason === "string" ? t.reason : undefined;

  const options: AdvisorOption[] = [];
  if (Array.isArray(t.options)) {
    for (const o of t.options) {
      if (!o || typeof o !== "object") continue;
      const op = o as Record<string, unknown>;
      const label = typeof op.label === "string" ? op.label : null;
      const tone = typeof op.tone === "string" ? op.tone : null;
      const summary = typeof op.summary === "string" ? op.summary : "";
      if (!label || !tone) continue;
      options.push({
        label,
        tone,
        summary,
        aiLean: op.aiLean === true || op.ai_lean === true,
        draftPath:
          typeof op.draftPath === "string"
            ? op.draftPath
            : typeof op.draft_path === "string"
            ? op.draft_path
            : undefined,
      });
    }
  }

  const complianceFlags: ComplianceFlag[] = [];
  const rawCompFlags = t.complianceFlags ?? t.compliance_flags;
  if (Array.isArray(rawCompFlags)) {
    for (const f of rawCompFlags) {
      if (!f || typeof f !== "object") continue;
      const fl = f as Record<string, unknown>;
      const type = typeof fl.type === "string" ? fl.type : null;
      const severityRaw = typeof fl.severity === "string" ? fl.severity : "medium";
      const reason_ = typeof fl.reason === "string" ? fl.reason : "";
      if (!type) continue;
      complianceFlags.push({
        type,
        severity: severityRaw === "high" ? "high" : severityRaw === "low" ? "low" : "medium",
        reason: reason_,
        matchedKeyword:
          typeof fl.matchedKeyword === "string"
            ? fl.matchedKeyword
            : typeof fl.matched_keyword === "string"
            ? fl.matched_keyword
            : undefined,
        suggestion: typeof fl.suggestion === "string" ? fl.suggestion : undefined,
      });
    }
  }

  const politicalFlags: PoliticalFlag[] = [];
  const rawPoliFlags = t.politicalFlags ?? t.political_flags;
  if (Array.isArray(rawPoliFlags)) {
    for (const f of rawPoliFlags) {
      if (!f || typeof f !== "object") continue;
      const fl = f as Record<string, unknown>;
      const type = typeof fl.type === "string" ? fl.type : null;
      const severityRaw = typeof fl.severity === "string" ? fl.severity : "medium";
      const reason_ = typeof fl.reason === "string" ? fl.reason : "";
      if (!type) continue;
      const suggestedPhrasings = Array.isArray(
        fl.suggestedPhrasings ?? fl.suggested_phrasings,
      )
        ? ((fl.suggestedPhrasings ?? fl.suggested_phrasings) as unknown[]).filter(
            (s): s is string => typeof s === "string",
          )
        : undefined;
      politicalFlags.push({
        type,
        severity: severityRaw === "high" ? "high" : severityRaw === "low" ? "low" : "medium",
        person: typeof fl.person === "string" ? fl.person : undefined,
        reason: reason_,
        matchedKeyword:
          typeof fl.matchedKeyword === "string"
            ? fl.matchedKeyword
            : typeof fl.matched_keyword === "string"
            ? fl.matched_keyword
            : undefined,
        suggestedPhrasings,
        advisoryOnly: fl.advisoryOnly === true || fl.advisory_only === true,
      });
    }
  }

  const contextRefs: string[] = [];
  const rawRefs = t.contextRefs ?? t.context_refs;
  if (Array.isArray(rawRefs)) {
    for (const r of rawRefs) {
      if (typeof r === "string") contextRefs.push(r);
    }
  }

  return {
    id,
    title,
    urgency,
    reason,
    options,
    complianceFlags,
    politicalFlags,
    contextRefs,
  };
}
