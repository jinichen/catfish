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

/** BL-ADVISOR-PROMPT-CONFORMANCE (6/1 鸿波): tone 严格 enum, parseMainTask
 *  把非 enum 归一到 'balanced' + warn. decisions.jsonl 留档一致性靠这个保证. */
export const VALID_TONES = [
  "strict",
  "balanced",
  "friendly",
  "formal",
  "urgent",
  "hold",
] as const;
export type AdvisorTone = typeof VALID_TONES[number];

export interface AdvisorOption {
  /** "A" / "B" / "C" */
  label: string;
  /** enum 严格 (parseMainTask 校验 + 非 enum 归一到 balanced). */
  tone: AdvisorTone;
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
  /** P3.3.9 (6/10): 跨 refresh 稳定 key, 6 字符 [a-z0-9]. LLM 生成 + 上次 cache 注入
   *  让 LLM 同业务复用旧 uid. task_chat / decisions 等持久化用此 key, 不用 title
   *  (title 每次 refresh LLM 会重写). */
  taskUid: string;
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

   **draftPath 硬约束** (5/22 鸿波撞 LLM 幻觉路径加):
   - draftPath 字段**只能**是真调 catfish_draft_email_reply / catfish_draft_meeting_brief
     后返回的 path 字段 (那个会落在 ~/.catfish/outputs/<today>/ 下).
   - **不允许编路径**. 没真调 tool 就**不填 draftPath**, 或填 null.
   - 不允许填 ~/Documents/, ~/Desktop/, 任何非 ~/.catfish/outputs/ 下的路径.
   - 不允许凭主菜标题脑补"应该叫什么名字" 再写进 draftPath. 必须 tool 返什么写什么.
   - 违反 → Rust 后端拒打开, 员工看到红条, catfish 失信.

4. **central_state=strong 时, 每件主菜额外跑扫描**:
   - 邮件 / 汇报草稿 → catfish_check_compliance
   - 涉及关键人物 → catfish_political_sensitivity_scan

5. **输出严格 JSON** (顶层不含 markdown 反引号, 不含前缀文字):
{
  "tier": "mid",
  "main_tasks": [
    {
      "id": 1,
      "task_uid": "li5d3k",
      "title": "老李催资质方案范围",
      "urgency": "high",
      "reason": "影响项目 A 客户关系",
      "options": [
        {"label": "A", "tone": "strict", "summary": "紧扣 5/18 班子会边界"},
        {"label": "B", "tone": "balanced", "summary": "微调保留余地", "aiLean": true, "draftPath": "<填真调 catfish_draft_email_reply 后返的 path; 没调就不填本字段>"},
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

# BL-ADVISOR-PROMPT-CONFORMANCE (6/1 鸿波, 5/22 实测 3 类 LLM 失误的修)

## 1) tone 严格 enum (不许编新词)
options[].tone **必须**是这 6 个之一: "strict" / "balanced" / "friendly" /
"formal" / "urgent" / "hold". 不允许出 "prepare" / "consider" / "neutral"
等. 客户端会归一不 enum 到 "balanced" + warn, 但靠你严格守约定才不浪费.

## 2) options 必须 2-3 条 (frontline / mid)
每个主菜 **2 或 3 个** options. 1 个不达标 — 失"建议选项"价值, 员工等于
没选择. 真没第 2 种合理口径 → 改主菜表达, 别勉强减 options.
**例外**: senior tier "异常例外型" 主菜可以 options=[] 只列风险 (上面已说).

## 4) task_uid 跨 refresh 复用 (P3.3.9, 6/10)

每个 main_task 必须有 task_uid (**6 字符**, 只能 [a-z0-9]).

user prompt 里如果给了 "# 上次 advisor 输出" section, 列出 12 小时内出现过的
task (含 uid + title + urgency), 你**必须**:
- 判定 "业务实质相同" 的 task → **复用旧 task_uid**, 不要新生成
- 判定标准 = 同项目 / 同人 / 同截止 / 同业务环节 / 同实质动作.
  title 表述差异不算新 task:
  · "CSMM-4 评估撰写" ≡ "CSMM-4 正式评估准备" → 复用同一 uid
  · "中电福富研发立项" ≡ "中电北京福富资质申报" → 复用同一 uid
- 真新业务 (上次没见过) → 自己生成 6 字符 [a-z0-9] uid, 例 "csmm4z" / "bjffr1"

task_uid 用作员工跟这条 task 的 task chat 文件名. 你重写 title 会导致旧
chat 找不到, 必须复用 uid 才能让员工跨 refresh 看到历史对话.

## 4.1) 看到 chat summary 时怎么办 (P3.3.12, 6/10)

user prompt 的 "# 上次 advisor 输出" section 里, 某些 prev task 后会跟一行
"└ 员工已跟 AI 聊过: <summary>". 这是员工在 detail pane 跟 AI 已经讨论过的
脉络 (LLM summary, 100-150 字).

看到 chat summary 时:
- **必须复用旧 uid** (跟 §4 一致)
- **reason 字段更新成 follow-up 风** — 不再是"这条 task 为啥重要", 而是
  "员工已经聊到 X 了, 下一步应该 Y" / "员工说先放一放, 等通知再说"
- options[] 提向"推一步" — 起草下一封 / 跑下个 tool / 跟某人确认细节,
  **不要重复早晨已建议过的选项** (员工已经看过 + 跟 AI 聊过了)
- 如果员工跟 AI 已经说"放一放" / "等通知" / "已完成" → urgency 降一档 +
  reason 解释为啥降. 别再当 high 推一遍.

不要忽略 chat summary — 它代表员工跟 task 的真实进度, 比 advisor 上次的
建议口径权威多了 (advisor 是猜的, summary 是员工真做过的).

## 3) draftPath 跟 tool call 绑定 (5/22 撞过的)
options[].draftPath 只能从你**真调** catfish_draft_email_reply /
catfish_draft_meeting_brief / catfish_compose_followup_list 后**返回的 path**
字段填. 没调 tool → **不填 draftPath**, 或填 null. 客户端会:
- 检测 draftPath 不在 ~/.catfish/outputs/ → 清掉 (5/22 BL-DRAFTPATH-WHITELIST)
- 后续 (待 ship) 检测 options 有 draftPath 但 chat 没 tool_call → 拒回复

**真路径**:
1. 先调 tool 起草 → 拿 returns.path
2. 再写 options[].draftPath = <path>
3. 不调 tool 就不写 draftPath, 让 UI 显"自己写"

不允许编路径绕过. 员工点开发现空草稿 = 鲶鱼失信.
`;

// ─── 拼 user prompt ──────────────────────────────────────────────

function buildUserPrompt(input: AdvisorInput): string {
  // 5/26: sessionGoal 字段删 — hermes 0.14 原生 /goal 替代, advisor 不再读 catfish 这套
  const { profile, emails, events, todos, ctx, urgencyMap, previousTasks } = input;
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

  // sessionGoal 段 5/26 删 (hermes 0.14 原生 /goal 替代)
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

  // P3.3.9 (6/10): 上次 advisor 输出 — 让 LLM 复用 task_uid (跨 refresh 稳定)
  // P3.3.12 (6/10): 加 chatSummary, 让 LLM 看到员工跟每条 task 已聊到哪
  if (previousTasks && previousTasks.length > 0) {
    const lines = previousTasks.map((t) => {
      const head = `- ${t.taskUid} | ${t.urgency} | ${t.title}`;
      if (t.chatSummary && t.chatSummary.trim().length > 0) {
        return `${head}\n  └ 员工已跟 AI 聊过: ${t.chatSummary.trim()}`;
      }
      return head;
    });
    parts.push(`# 上次 advisor 输出 (12 小时内)
**同业务必须复用 task_uid, 不要新生成**. 判定标准 = 同项目/同人/同截止/同业务环节.
title 表述差异不算新 task. 详见 SYSTEM_PROMPT § "task_uid 跨 refresh 复用".
**已聊过的 task (含 chat summary), 你这次应该 follow-up 进度 / 帮员工往前推, 不要重推同样建议**.

${lines.join("\n")}`);
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
  // sessionGoal 5/26 删 — hermes 0.14 原生 /goal 替代
  model: string;
  /** P3.3.9 (6/10): 上次 advisor 输出 (12h 内 cache), 让 LLM 复用 task_uid.
   *  P3.3.12 (6/10): 加 chatSummary — 员工跟这条 task 已聊过的 100 字 summary,
   *  让 advisor 看到员工已讨论过什么, 不再重推同样建议.
   *  fetchBriefingAdvisor 内部从 advisorCacheGet 拉, 不需要 caller 传. */
  previousTasks?: Array<{
    taskUid: string;
    title: string;
    urgency: string;
    /** P3.3.12: 跟 AI 已聊到哪 (100-150 字). 空字符串 = 没聊过 / summary 失败. */
    chatSummary?: string;
  }>;
}

/** 5/22 cold start 修锁: 同时只允许一个 advisor LLM call 跑.
 *  React StrictMode dev useEffect 双调 → fetchBriefingAdvisor 双跑 → LLM 调 2 次浪费 token + quota.
 *  跟 recomputeProfile 同款 in-flight 锁. */
let _advisorInFlight: Promise<AdvisorResult | null> | null = null;

/** 5/22 鸿波实盘: 公司内网 Qwen 拥堵, TTFT 44s, 多次撞 gateway 300s timeout, UI 干等 5min.
 *  客户端 60s timeout sentinel — fetch 不 abort (避 5/21 Tauri webview suspend bug,
 *  fetch 完成后 cache 还会写, 下次时段触发能用), 但 fetchBriefingAdvisor 早返
 *  TIMEOUT, 调用方走 stale cache fallback. */
export const ADVISOR_TIMEOUT = Symbol("advisor-timeout");
export type AdvisorFetchResult = AdvisorResult | null | typeof ADVISOR_TIMEOUT;
const CLIENT_TIMEOUT_MS = 60_000;

/** 主入口. 不挂 AbortSignal (Tauri webview suspend 经验, 5/21 学到). */
export async function fetchBriefingAdvisor(input: AdvisorInput): Promise<AdvisorFetchResult> {
  // in-flight 锁: 已在跑就复用 promise
  if (_advisorInFlight) {
    console.log("[advisor] 已在跑, 复用 in-flight promise (StrictMode 双调防御)");
    // 复用 in-flight 也加同款 timeout race — 让连续两次调都同等享受超时保护
    return raceWithTimeout(_advisorInFlight);
  }

  const myPromise = _fetchBriefingAdvisorImpl(input);
  _advisorInFlight = myPromise;
  // 注: 不 await 整个 promise (它要 5min), 用 race 让本次调用早返;
  // myPromise 后台跑完后:
  //   - 清锁
  //   - 如果 result 非空, 写 cache (避免 5min 后真返回的成果被丢弃)
  try {
    return await raceWithTimeout(myPromise);
  } finally {
    void myPromise
      .then(async (result) => {
        if (!result) return;
        try {
          const { advisorCacheGet, advisorCacheSave } = await import("./advisor_cache");
          // P3.3.12 (6/10): save 前先读老 cache 拿到 taskChatSummaries — 老 cache
          // 里有 _fetchBriefingAdvisorImpl 阶段刚 pre-write 的 summaries, 直接 build
          // 新 cache 会丢. merge 进去.
          const oldCache = await advisorCacheGet().catch(() => null);
          await advisorCacheSave({
            computedAt: new Date().toISOString(),
            result,
            model: input.model,
            taskChatSummaries: oldCache?.taskChatSummaries,
          });
          console.log("[advisor] 后台完成, 已写 cache (调用方可能已 TIMEOUT 走 stale)");
        } catch (e) {
          console.warn("[advisor] 后台写 cache 挂:", e);
        }
      })
      .catch(() => {})
      .finally(() => {
        if (_advisorInFlight === myPromise) {
          _advisorInFlight = null;
        }
      });
  }
}

async function raceWithTimeout(
  p: Promise<AdvisorResult | null>,
): Promise<AdvisorFetchResult> {
  return Promise.race<AdvisorFetchResult>([
    p,
    new Promise<typeof ADVISOR_TIMEOUT>((resolve) =>
      setTimeout(() => {
        console.warn(
          `[advisor] 客户端 ${CLIENT_TIMEOUT_MS / 1000}s 超时, 返 TIMEOUT (fetch 仍在后台跑, ` +
            "完成会写 cache, 下次时段触发能用).",
        );
        resolve(ADVISOR_TIMEOUT);
      }, CLIENT_TIMEOUT_MS),
    ),
  ]);
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

  // P3.3.9 (6/10): caller 没传 previousTasks 时, 内部从 advisorCacheGet 拉
  //   (含 stale, 让 LLM 复用 uid). 老 cache 没 taskUid 字段就跳过, 不出错.
  // P3.3.12.1 (6/10): summary 只从 cache 读 (不再现场跑 LLM). 拉 summary 是
  //   ensureTaskChatSummariesFresh 的活, 它由 AdvisorView mount 时后台调,
  //   跟 advisor 主 LLM call 解耦 — cache 命中场景也能更新. 这里只读最新 cache.
  let inputWithPrev = input;
  if (!input.previousTasks) {
    try {
      const { advisorCacheGet } = await import("./advisor_cache");
      const cached = await advisorCacheGet();
      if (cached && cached.result?.mainTasks?.length > 0) {
        const summaries = cached.taskChatSummaries ?? {};
        const enriched = cached.result.mainTasks
          .filter((t) => typeof t.taskUid === "string" && t.taskUid.length > 0)
          .map((t) => ({
            taskUid: t.taskUid,
            title: t.title,
            urgency: t.urgency,
            chatSummary: summaries[t.taskUid]?.summary ?? "",
          }));
        if (enriched.length > 0) {
          inputWithPrev = { ...input, previousTasks: enriched };
          const withSummary = enriched.filter((t) => t.chatSummary).length;
          console.log(
            `[advisor] 注入 ${enriched.length} 条 prev task ` +
              `(${withSummary} 含 chat summary, 全部来自 cache)`,
          );
        }
      }
    } catch (e) {
      console.warn("[advisor] 拉 advisor_cache 失败 (P3.3.9 uid 复用降级):", e);
    }
  }

  const userPrompt = buildUserPrompt(inputWithPrev);
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

    // BL-ADVISOR-UPSTREAM-ERROR-AS-CONTENT (6/1 鸿波): gateway 端已加 detect 转 502
    // (双层防御之一). 客户端兜底再判一次, 真 LiteLLM 私有 LLM (catfish-private-main)
    // streaming fail 时返 200 + content="API call failed after 3 retries..." 标准
    // pattern. 即使 gateway 端 detect 漏掉某变体, 这里仍能识别避免"JSON 解析失败"灰区.
    const upstreamErrorPattern = /API call failed|after \d+ retries|during streaming|retries exhausted/i;
    if (content.length < 500 && upstreamErrorPattern.test(content)) {
      console.warn(
        "[advisor] 上游 LLM 错误作 content 返 (HTTP 200 + 错误文本), 真因: 上游 LLM 服务挂. " +
        "原文:", content.slice(0, 200),
      );
      return null;
    }

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

/** P3.3.9 (6/10): 生成 6 字符 [a-z0-9] uid. LLM 没返 task_uid 时 fallback 用. */
function generateTaskUid(): string {
  return Math.random().toString(36).slice(2, 8).padEnd(6, "0");
}

/** P3.3.12.1 (6/10): ensure 每个 mainTask 的 chat summary 是最新的 (jsonl size 没变就跳).
 *
 *  独立于 advisor 主 LLM call — 这样 cache 命中场景 (主 advisor 没跑) 仍能更新
 *  summary, 下次 advisor 真要跑时直接读 cache 即可.
 *
 *  AdvisorView mount 后异步调一次 (cache 命中也调), 不阻塞 UI.
 *  refreshKey 变 (员工点刷新) 也调.
 *
 *  实测烧 token 风险: 每个 jsonl size 变 task 跑一次 LLM. 4 task 全变 ≈ 4 次 LLM call,
 *  跟主 advisor 一次 call 比是几分之一. 失败不阻塞, summary 留旧的就旧的.
 *
 *  in-flight 锁: 跟主 advisor 同款 — React StrictMode dev useEffect 双调会让本函数
 *  瞬间双跑, 两次都读老 cache 各跑 4 次 LLM, 浪费 8 次 token. 锁复用 promise. */
export async function ensureTaskChatSummariesFresh(model: string): Promise<void> {
  if (_summaryEnsureInFlight) {
    console.log("[advisor summary] ensure 已在跑, 复用 in-flight promise");
    return _summaryEnsureInFlight;
  }
  const p = _ensureTaskChatSummariesFreshImpl(model);
  _summaryEnsureInFlight = p;
  try {
    await p;
  } finally {
    if (_summaryEnsureInFlight === p) _summaryEnsureInFlight = null;
  }
}

let _summaryEnsureInFlight: Promise<void> | null = null;

async function _ensureTaskChatSummariesFreshImpl(model: string): Promise<void> {
  try {
    const { advisorCacheGet, advisorCacheSave } = await import("./advisor_cache");
    const { taskChatGet, taskChatSize } = await import("./task_chat");
    const { sessionGetByTaskUid, getSession } = await import("./tauri");
    const { loadSessionMessagesAsChat } = await import("./sessionMessages");
    const cached = await advisorCacheGet();
    if (!cached || !Array.isArray(cached.result?.mainTasks)) {
      console.log("[advisor summary] ensure 跳过 — 没 cache 或没 mainTasks");
      return;
    }
    const mainTasks = cached.result.mainTasks.filter(
      (t: { taskUid?: string }) =>
        typeof t.taskUid === "string" && t.taskUid.length > 0,
    );
    if (mainTasks.length === 0) {
      console.log("[advisor summary] ensure 跳过 — mainTasks 全没 taskUid");
      return;
    }

    const cachedSummaries = (cached.taskChatSummaries ?? {}) as Record<
      string,
      { summary: string; jsonlSize: number; messageCount?: number; computedAt: string }
    >;
    const newSummaries = { ...cachedSummaries };
    let llmCalls = 0;
    let cacheHits = 0;
    let emptyTasks = 0;
    let fromStateDb = 0;
    let fromJsonl = 0;

    await Promise.all(
      mainTasks.map(async (t: { taskUid: string; title: string }) => {
        try {
          // P3.3.19 C Phase 5 (6/11): 优先拉 state.db. fallback 老 jsonl.
          //   1. sessionGetByTaskUid(taskUid) → 有 sessionId 就用 state.db
          //   2. 没有 sessionId (advisor 早过 task chat 但还没 DetailPane mount 触发 sessionCreate)
          //      → 退回 jsonl (Phase 4 migration 不一定已跑完)
          const sid = await sessionGetByTaskUid(t.taskUid).catch(() => null);

          let messages: Array<{ role: string; content: string; ts: string }> = [];
          let currentCount = 0;

          if (sid) {
            // state.db 路径
            try {
              const detail = await getSession(sid);
              currentCount = detail.meta?.messageCount ?? detail.messages.length;
              if (currentCount === 0) {
                emptyTasks++;
                return;
              }
              // 转 ChatMessage 后再降级成 summarizeTaskChat 接受的 shape
              const chatMessages = loadSessionMessagesAsChat(detail);
              messages = chatMessages.map((m) => ({
                role: m.role,
                content: m.content,
                ts: m.ts,
              }));
              fromStateDb++;
            } catch (e) {
              console.warn(`[advisor summary] state.db 拉 ${sid} 失败, fallback jsonl:`, e);
              sid && (await null); // noop, fall through
            }
          }

          if (!sid || messages.length === 0) {
            // jsonl fallback (Phase 4 migration 未跑完 / 没 session 时)
            const size = await taskChatSize(t.taskUid).catch(() => 0);
            if (size === 0) {
              emptyTasks++;
              return;
            }
            const jsonlMsgs = await taskChatGet(t.taskUid).catch(() => []);
            if (jsonlMsgs.length === 0) {
              emptyTasks++;
              return;
            }
            messages = jsonlMsgs.map((m) => ({
              role: m.role,
              content: m.content,
              ts: m.ts,
            }));
            currentCount = size; // 老 hash 用 size
            fromJsonl++;
          }

          // cache 失效判断: 优先 messageCount 对比 (state.db), 没 messageCount 则 jsonlSize
          const hit = cachedSummaries[t.taskUid];
          const cacheValid = hit && hit.summary && (
            (typeof hit.messageCount === "number" && hit.messageCount === currentCount) ||
            (typeof hit.messageCount !== "number" && hit.jsonlSize === currentCount)
          );
          if (cacheValid) {
            cacheHits++;
            return;
          }

          llmCalls++;
          const summary = await summarizeTaskChat(t.title, t.taskUid, messages, model);
          if (summary) {
            newSummaries[t.taskUid] = {
              summary,
              jsonlSize: currentCount,  // backward compat 留同字段, value 取 messageCount/size
              messageCount: currentCount,
              computedAt: new Date().toISOString(),
            };
          }
        } catch (e) {
          console.warn(`[advisor summary] ensure ${t.taskUid} 失败:`, e);
        }
      }),
    );

    console.log(
      `[advisor summary] ensure 完成 — ${mainTasks.length} task ` +
        `(${emptyTasks} 没聊过, ${cacheHits} cache 命中, ${llmCalls} 调 LLM; ` +
        `源: ${fromStateDb} state.db / ${fromJsonl} jsonl)`,
    );

    if (llmCalls > 0) {
      await advisorCacheSave({
        ...cached,
        taskChatSummaries: newSummaries,
      });
      console.log("[advisor summary] 已写 cache (含新 summary)");
    }
  } catch (e) {
    console.warn("[advisor summary] ensure 顶层异常:", e);
  }
}

/** P3.3.12 (6/10): 调 LLM 出 task chat summary. 100-150 字, 客观描述员工
 *  已经决定/已经做/已经说要做什么, 不含 AI 建议内容.
 *
 *  截最后 30 条 + 每条限 300 字 → 控制 prompt 长度防 OOM.
 *  max_tokens 300 (~150 中文字), temperature 0.3 (准确为主).
 *  失败 (网络/LLM 非 2xx/parse 错) 返 "" (不阻塞 advisor 主流程). */
async function summarizeTaskChat(
  taskTitle: string,
  taskUid: string,
  messages: Array<{ role: string; content: string; ts: string }>,
  model: string,
): Promise<string> {
  if (messages.length === 0) return "";

  const lastN = messages.slice(-30);
  const dump = lastN
    .map((m) => {
      const role = m.role === "user" ? "员工" : m.role === "assistant" ? "AI" : m.role;
      const text = m.content.length > 300 ? m.content.slice(0, 300) + "…" : m.content;
      return `${role}: ${text}`;
    })
    .join("\n");

  const prompt = `以下是员工跟 catfish AI 在某条待办 "${taskTitle}" 上的最近对话.

用 100-150 字总结员工**已经决定/已经做/已经说要做什么**, 客观描述员工当前进度/状态/卡点/决策.
**不要**包括 AI 的建议或猜测, 只总结员工本人说过/做过/确认过的事.
输出纯文本一段, 不带 markdown.

${dump}

总结:`;

  try {
    const url = `${config.backendUrl}/v1/chat/completions${SERVICE_LLM_QUERY}`;
    const resp = await fetchWithAuth(url, {
      method: "POST",
      headers: SERVICE_LLM_HEADERS,
      body: JSON.stringify({
        model,
        messages: [{ role: "user", content: prompt }],
        max_tokens: 300,
        temperature: 0.3,
        stream: false,
      }),
    });
    if (!resp.ok) {
      console.warn(`[advisor summary] ${taskUid} 非 2xx:`, resp.status);
      return "";
    }
    const data = await resp.json();
    const content = data?.choices?.[0]?.message?.content;
    if (typeof content !== "string") return "";
    // 截 400 字防 LLM 不守 100-150 字约束
    return content.trim().slice(0, 400);
  } catch (e) {
    console.warn(`[advisor summary] ${taskUid} 异常:`, e);
    return "";
  }
}

function parseMainTask(t: Record<string, unknown>): MainTask | null {
  const title = t.title;
  if (typeof title !== "string" || !title.trim()) return null;
  const id = typeof t.id === "number" ? t.id : 0;

  // P3.3.9 (6/10): task_uid 读, 兼容 task_uid / taskUid; 没返 fallback 生成新.
  const rawUid =
    typeof t.task_uid === "string" && t.task_uid.length >= 4
      ? t.task_uid
      : typeof t.taskUid === "string" && t.taskUid.length >= 4
      ? t.taskUid
      : null;
  const taskUid = rawUid
    ? rawUid.toLowerCase().replace(/[^a-z0-9]/g, "").slice(0, 12) || generateTaskUid()
    : generateTaskUid();
  if (!rawUid) {
    console.warn(`[advisor] task '${title}' LLM 没返 task_uid, fallback 新生成 '${taskUid}' (跨 refresh chat 历史会丢)`);
  }

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
      const rawTone = typeof op.tone === "string" ? op.tone.toLowerCase() : null;
      const summary = typeof op.summary === "string" ? op.summary : "";
      if (!label || !rawTone) continue;
      // BL-ADVISOR-PROMPT-CONFORMANCE #6a (6/1): tone enum 归一化.
      // 5/22 实测 LLM 偶尔出 "prepare" / "consider" 等非 enum tone. UI 不依赖
      // tone 选颜色 (只当 string 传 decisionRecord), 但 decisions.jsonl 留档
      // 时统计困难 — 改归一到 'balanced' + warn. SYSTEM_PROMPT 已说 enum,
      // 这里是兜底 fallback (不影响主线).
      const tone: AdvisorTone = (VALID_TONES as readonly string[]).includes(rawTone)
        ? (rawTone as AdvisorTone)
        : "balanced";
      if (tone !== rawTone) {
        console.warn(
          `[advisor] LLM 返非 enum tone='${op.tone}' (task: ${title}), 归一到 'balanced'`,
        );
      }
      // 5/22 鸿波 BL-DRAFTPATH-WHITELIST: 防 LLM 幻觉路径.
      // LLM 偶尔不听 SYSTEM_PROMPT, 编一个 /Users/.../Documents/xxx.md 进来.
      // 客户端二次过滤: 只接 ~/.catfish/outputs/ 下的 path, 其它当 null.
      // 这样 UI 显"无草稿, 自己写" 而不是点了报"草稿打开失败".
      const rawDraft =
        typeof op.draftPath === "string"
          ? op.draftPath
          : typeof op.draft_path === "string"
          ? op.draft_path
          : undefined;
      const safeDraft =
        rawDraft && rawDraft.includes("/.catfish/outputs/") ? rawDraft : undefined;
      if (rawDraft && !safeDraft) {
        console.warn(
          "[advisor] LLM 幻觉 draftPath, 不在 ~/.catfish/outputs/ 下, 已清:",
          rawDraft,
        );
      }
      options.push({
        label,
        tone,
        summary,
        aiLean: op.aiLean === true || op.ai_lean === true,
        draftPath: safeDraft,
      });
    }
  }

  // BL-ADVISOR-PROMPT-CONFORMANCE #6b (6/1): options 数量校验 (warn only, 不
  // 拒). senior tier 允许 0 options ("异常例外型" 主菜只列风险, SYSTEM_PROMPT
  // 已说). 但 frontline/mid tier 出 1 options 是 LLM 没按 "2-3 个建议选项"
  // 出, 失"建议选项"价值. warn 不修, 真改靠 SYSTEM_PROMPT 强约束 (#6c).
  if (options.length === 1) {
    console.warn(
      `[advisor] task '${title}' 只 1 个 option, LLM 没按 SYSTEM_PROMPT 出 2-3 个`,
    );
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
    taskUid,
    title,
    urgency,
    reason,
    options,
    complianceFlags,
    politicalFlags,
    contextRefs,
  };
}
