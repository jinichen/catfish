/** BL-WORKPLAN (5/21 Phase 6): 周/日 × 内容/事件/建议 6 块矩阵, 综合 7 数据源.
 *
 * 鸿波 5/21 拍板: Phase 5 单点主线方向错, 央国企节奏是周/月, 不是日.
 * 设计稿: docs/BL-WORKPLAN-DESIGN.md
 *
 * 7 数据源:
 *   行事历 + TODO + 邮件 + 长期记忆 + 7天对话 + 周报历史 + 项目进度
 *
 * LLM 1 次调用 (C1), 出 JSON:
 *   {
 *     week:  { summary, events[], advice },
 *     today: { summary, events[], advice }
 *   }
 *
 * 失败 → 返 null, BriefingCard 不渲染 WorkplanView, 仍显下方 EventsDetail / TodosDetail.
 */

import { briefingContextFetch, type CalendarEvent, type EmailDigestItem, type JournalTodo } from "./tauri";
import { config } from "./env";
import { fetchWithAuth } from "./me";

// 跟 briefing.ts 同款 query param: 不带 X-Catfish-* header (CORS 友好)
const SERVICE_LLM_HEADERS = { "Content-Type": "application/json" };
const SERVICE_LLM_QUERY = "?catfish_source=companion-workplan&catfish_skip_identity=1&catfish_internal=1";

export interface WorkplanEvent {
  /** 时间标签, 例 "今天 11:30" / "5/22 周五" / "本周内" */
  when: string;
  /** 事件标题 */
  title: string;
  /** 简短上下文 (来源 / 紧急度等), 可选 */
  context?: string;
}

export interface WorkplanSection {
  /** 工作内容总结 (1-2 句, 回顾 + 计划) */
  summary: string;
  /** 关键事件 list (会议 / ddl / 邮件需回 / 项目节点) */
  events: WorkplanEvent[];
  /** 鲶鱼建议 (整体节奏 + 风险 / 具体步骤) */
  advice: string;
}

export interface Workplan {
  /** 本周 (周一-周日 或 滚动 7 天) */
  week: WorkplanSection;
  /** 今日 (具体落地) */
  today: WorkplanSection;
}

const SYSTEM_PROMPT = `你是中国央国企员工的资深 AI 副手. 综合 7 类数据 (行事历/TODO/邮件/长期记忆/7 天对话/周报历史/项目进度), 出**周/日双层工作总结**.

注意:
- 央国企节奏是周/月. 周维度是主, 日维度是落地
- 不是机械分桶或重述数据! 你要**关联推理**:
  - 邮件 + TODO 含相同关键词 → 同一件事
  - 日历截止 + 项目进度低 → "紧急做"
  - 周报历史 + 当前 TODO → "本周延续什么"
- 长期记忆 + 当前数据 → 识别员工真正在意的客户/项目

返回严格 JSON (不要 markdown 包裹, 不要前缀):
{
  "week": {
    "summary": "本周做什么/已做什么, 一句话 ≤ 80 字",
    "events": [
      {"when": "5/22 周五 14:00", "title": "班子会", "context": "季度汇报需提前发"},
      ...
    ],
    "advice": "本周整体节奏 + 风险一句话 ≤ 80 字"
  },
  "today": {
    "summary": "今天落地什么, 一句话 ≤ 60 字",
    "events": [
      {"when": "11:30", "title": "回复老李邮件", "context": "急, 锁资质方案范围"},
      ...
    ],
    "advice": "今天怎么干, 1-3 个具体步骤 ≤ 100 字"
  }
}

week.events 最多 5 条 (本周重要节点), today.events 最多 5 条 (今天具体事件).
数据全空 → mainLine 写 "今天比较松, 复盘本周计划".`;

function buildUserPrompt(
  emails: EmailDigestItem[],
  events: CalendarEvent[],
  todos: JournalTodo[],
  distilledFacts: string,
  recentSessionBriefs: Array<{ title: string; firstUserMessage: string; startedAt: string }>,
  weeklyReports: Array<{ filename: string; modifiedAt: string }>,
  workplan: string,
  projects: string,
  sessionGoal: string,
  urgencyMap: Record<string, string>,
): string {
  const parts: string[] = [];
  const today = new Date();
  const weekStart = new Date(today);
  weekStart.setDate(today.getDate() - today.getDay() + 1);  // 周一

  parts.push(`# 时间锚点
今天: ${today.toLocaleDateString("zh-CN", { month: "long", day: "numeric", weekday: "long" })}
本周: ${weekStart.toLocaleDateString("zh-CN", { month: "long", day: "numeric" })} 起
`);

  if (sessionGoal.trim()) {
    parts.push(`# 员工今日重点 (自己设的)\n${sessionGoal.trim()}\n`);
  }

  if (workplan.trim()) {
    parts.push(`# 员工本周计划 (自己写的)\n${workplan.trim()}\n`);
  }

  if (projects.trim()) {
    parts.push(`# 项目进度跟踪\n${projects.trim()}\n`);
  }

  if (distilledFacts.trim()) {
    parts.push(`# 关于这个员工 (长期画像)\n${distilledFacts.trim()}\n`);
  }

  // 周报历史 (文件名 + 时间, 不含内容)
  if (weeklyReports.length > 0) {
    const lines = weeklyReports.slice(0, 5).map((r) => `${r.modifiedAt.slice(0, 10)} ${r.filename}`);
    parts.push(`# 周报历史 (员工已生成过的, 文件名 + 时间)\n${lines.join("\n")}\n`);
  }

  // 邮件
  if (emails.length > 0) {
    const lines = emails.slice(0, 15).map((m) => {
      const u = urgencyMap[m.id] ?? "未评";
      return `[${u}] ${m.sender}: ${m.subject}`;
    });
    parts.push(`# 今日邮件 (${emails.length} 封, 前 15 列)\n${lines.join("\n")}\n`);
  }

  // 日历
  if (events.length > 0) {
    const lines = events.map((e) => {
      const time = e.all_day ? "全天" : `${e.start} - ${e.end}`;
      return `${time} ${e.summary}${e.location ? ` @ ${e.location}` : ""}`;
    });
    parts.push(`# 今日日程 (${events.length} 件)\n${lines.join("\n")}\n`);
  }

  // TODO
  if (todos.length > 0) {
    const lines = todos.map((t) => {
      const star = t.is_priority ? "⭐ " : "";
      return `${star}${t.text} (${t.source})`;
    });
    parts.push(`# 工作计划 TODO (${todos.length} 件)\n${lines.join("\n")}\n`);
  }

  // 7 天对话
  if (recentSessionBriefs.length > 0) {
    const lines = recentSessionBriefs.slice(0, 8).map((s) => {
      const msg = s.firstUserMessage.slice(0, 80);
      return `[${s.startedAt.slice(0, 10)}] ${s.title || "(无 title)"}: ${msg}`;
    });
    parts.push(`# 最近 7 天对话 (你跟员工聊过的)\n${lines.join("\n")}\n`);
  }

  parts.push("# 任务\n按 system prompt 出 JSON. 周/日双层, 各 3 段 (内容/事件/建议).");

  return parts.join("\n");
}

export async function fetchWorkplan(
  emails: EmailDigestItem[],
  events: CalendarEvent[],
  todos: JournalTodo[],
  urgencyMap: Record<string, string>,
  model: string,
): Promise<Workplan | null> {
  console.log("[workplan] 调用开始", { emails: emails.length, events: events.length, todos: todos.length, model });

  // 数据全空 → 不调
  if (emails.length === 0 && events.length === 0 && todos.length === 0) {
    return null;
  }

  // 拉 context (7 数据源)
  let ctx;
  try {
    ctx = await briefingContextFetch();
    console.log("[workplan] context 拉到:", {
      distilled: ctx.distilledFacts.length,
      sessions: ctx.recentSessionBriefs.length,
      workplan: ctx.workplan.length,
      projects: ctx.projects.length,
      weeklyReports: ctx.weeklyReports.length,
    });
  } catch (e) {
    console.warn("[workplan] context 拉失败, 空兜底:", e);
    ctx = { distilledFacts: "", recentSessionBriefs: [], sessionGoal: "", workplan: "", projects: "", weeklyReports: [] };
  }

  const userPrompt = buildUserPrompt(
    emails, events, todos,
    ctx.distilledFacts,
    ctx.recentSessionBriefs.map((s) => ({ title: s.title, firstUserMessage: s.firstUserMessage, startedAt: s.startedAt })),
    ctx.weeklyReports,
    ctx.workplan,
    ctx.projects,
    ctx.sessionGoal,
    urgencyMap,
  );

  const url = `${config.backendUrl}/v1/chat/completions${SERVICE_LLM_QUERY}`;
  console.log("[workplan] 发 fetch:", url, "prompt 长度:", userPrompt.length);

  // 5/21 修: 不挂 AbortSignal. Tauri webview 在 window 失焦时会 suspend 网络请求,
  // 自己挂 timeout/abort 会让 LLM (~15-20s) 永远完不成 (focus 一切就 8s 内 abort).
  // 让 fetch 自由活. 失败由 fetch 本身的 reject 抛出, 下次 loadAll 自动重试.

  try {
    const resp = await fetchWithAuth(url, {
      method: "POST",
      headers: SERVICE_LLM_HEADERS,
      body: JSON.stringify({
        model,
        messages: [
          { role: "system", content: SYSTEM_PROMPT },
          { role: "user", content: userPrompt },
        ],
        max_tokens: 1500,
        temperature: 0.5,
        stream: false,
      }),
    });

    console.log("[workplan] HTTP status =", resp.status);
    if (!resp.ok) {
      console.warn("[workplan] LLM 非 2xx:", resp.status);
      try { console.warn("[workplan] error body:", (await resp.text()).slice(0, 300)); } catch {/**/}
      return null;
    }

    const data = await resp.json();
    const content = data?.choices?.[0]?.message?.content;
    console.log("[workplan] raw content:", typeof content === "string" ? content.slice(0, 500) : content);
    if (typeof content !== "string") return null;

    const cleaned = content.trim().replace(/^```(?:json)?\s*/i, "").replace(/```$/, "").trim();
    let parsed: unknown;
    try {
      parsed = JSON.parse(cleaned);
    } catch (e) {
      console.warn("[workplan] JSON 解析失败:", cleaned.slice(0, 200), e);
      return null;
    }

    if (!parsed || typeof parsed !== "object") return null;
    const obj = parsed as { week?: unknown; today?: unknown };

    const parseSection = (s: unknown): WorkplanSection | null => {
      if (!s || typeof s !== "object") return null;
      const sec = s as { summary?: unknown; events?: unknown; advice?: unknown };
      const summary = typeof sec.summary === "string" ? sec.summary.trim() : "";
      const advice = typeof sec.advice === "string" ? sec.advice.trim() : "";
      const eventsArr: WorkplanEvent[] = Array.isArray(sec.events)
        ? sec.events
            .filter((e): e is WorkplanEvent =>
              typeof e === "object" && e !== null &&
              typeof (e as WorkplanEvent).when === "string" &&
              typeof (e as WorkplanEvent).title === "string",
            )
            .slice(0, 5)
        : [];
      if (!summary && eventsArr.length === 0 && !advice) return null;
      return { summary, events: eventsArr, advice };
    };

    const week = parseSection(obj.week);
    const today = parseSection(obj.today);
    if (!week || !today) {
      console.warn("[workplan] week/today 段缺失:", obj);
      return null;
    }

    return { week, today };
  } catch (e) {
    console.warn("[workplan] LLM 调用失败:", e);
    return null;
  }
}
