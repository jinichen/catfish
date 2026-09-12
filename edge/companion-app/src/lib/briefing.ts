/** 早安播报 LLM 优先建议 — BL-BRIEFING-LLM-RANK step2 (5/20 鸿波).
 *
 * step1 (今天上半场) rule-based 拼接字符串, 直白但生硬.
 * step2 (本提交) 调 catfish-gateway /v1/chat/completions 让 LLM 写一行更自然的建议.
 *
 * 设计:
 *   - non-streaming (一行字 streaming 体验加分有限, 简化 client)
 *   - 调 companion 当前选的 model (跟 5/20 鸿波早上"模型不是全部按照 companion 的选择吗"反馈一致)
 *   - 默认 catfish-private-main (charter "数据不出 Mac" — 邮件/日历/journal 三源都敏感)
 *   - LLM 挂了 / timeout → 返 null, 调用方 fallback 到 rule-based
 *   - max_tokens=80 (一行字够), temperature=0.7 (留点自然口语感)
 *
 * 鸿波偏好 (从 ~/.catfish/employee_journal.md sample 看):
 *   - 短句, 不啰嗦, 不要"为您"这种客套
 *   - 顺序按时间紧急度
 *   - 结尾不要"祝您工作顺利"这种
 */

import { type Personality } from "./agent";
import { config } from "./env";
import { warnIfUpstreamError } from "./upstreamErrorGuard";
import { fetchWithAuth } from "./me";
import { type CalendarEvent, type ReminderTodo } from "./tauri";

/** BL-BRIEFING-LLM-PERSONALITY (5/20): 把员工选的小鲶人格 (gentle/direct/roast)
 * 注入到 LLM system prompt. 让早安播报 / 急邮件提醒 / TODO 抽取 4 个 LLM 调用
 * 都按选的人格说话.
 *
 * roast 涉及"健康 / 家庭 / 收入" 自动切温柔 — agent.ts PERSONALITY_LABELS 已写明,
 * LLM 端 prompt 里也提示一下, 让 LLM 自我审查不要冒犯敏感话题.
 */
function _personalityHint(personality: Personality | undefined): string {
  switch (personality) {
    case "direct":
      return `
人格设定: 你是"直爽老李"型同事 — 说话短, 不绕弯, 不堆套话.
- 能 5 个字说完别用 10 个
- 直接说要做啥, 不解释为啥
- 不要"哦" "嗯" "稍等一下" 这种 filler
`;
    case "roast":
      return `
人格设定: 你是"毒舌小赵"型同事 — 敢吐槽, 看到员工拖延 / 写不好可以损一句再给建议.
- 损要有梗有礼, 不带攻击 ("还在拖周报呢, 老李都问 3 次了")
- 但涉及健康 / 家庭 / 收入 / 病假 / 家人 这种话题立刻切回温柔
- 拒绝平庸不要太刻薄, 留点同事温度
`;
    case "gentle":
    default:
      return `
人格设定: 你是"温柔同事"型 — 贴心, 主动确认细节, 不催不烦.
- 用关心的语气, 不命令
- 多用"看起来" "也许" 这种柔和措辞
- 不要假惺惺, 保持自然
`;
  }
}

/** BL-BRIEFING-SESSION-LEAK (5/20): 服务式 LLM 调用 marker, 跟 5/18 email_scheduler 同套.
 *
 * gateway 看到 X-Catfish-Source 非员工默认值 → 跳 sessions table 持久化 +
 * 跳 SOUL identity inject (这是服务后台调, 不是员工对话).
 *
 * 没加之前的 bug: BriefingCard 加载时 4 个 LLM 调用全被写进 ~/.hermes/state.db
 * sessions 表, 工作台左侧 session 列表混入"现在 14:58. 日历今天 1 件..." 这种
 * prompt 文字.
 */
// 5/21 BL-CORS-DEBT-FIX: 删 X-Catfish-* header, 改 URL query param 传 (hermes proxy
// 8642 CORS allow-list 没配这些 header, preflight 拒). gateway 端 5/21 已经支持
// ?catfish_source=&catfish_skip_identity=1 query param fallback.
const SERVICE_LLM_HEADERS = {
  "content-type": "application/json",
};

const SERVICE_LLM_QUERY = "?catfish_source=companion-briefing-card&catfish_skip_identity=1";

const SYSTEM_PROMPT = `你是用户的鲶鱼数字员工 (Catfish), 帮员工写早安播报的 💡 优先建议行.
风格:
- 一行字, 30 字以内, 不要换行
- 不要"为您" / "祝您工作顺利" / "希望对您有帮助" 这种客套
- 按时间紧急度排, 用"→"连接 2-3 件事
- 口语化, 像同事提醒, 不像 AI 助手

示例:
- "一会儿 14:00 跟老李会议, 提前 10 分钟准备 PPT → 处理 5 封邮件 → 补写周报"
- "先回那 3 封邮件 → 整理上周复盘"
- "今天比较松, 喝杯水?"

只返一行建议, 不要任何前缀 (不要"建议:" / "今日:") / 解释 / markdown.`;

function _formatUpcomingEvent(event: CalendarEvent): string {
  const start = new Date(event.start);
  const day = start.toLocaleDateString("zh-CN", {
    month: "numeric",
    day: "numeric",
    weekday: "short",
  });
  if (event.all_day) return `${day} 全天 ${event.summary}`;
  const time = start.toLocaleTimeString("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
  return `${day} ${time} ${event.summary}`;
}

/** 拼 LLM prompt — 把三源数据浓缩成一段 user message. */
function _buildUserPrompt(
  unread: number,
  events: CalendarEvent[],
  todos: ReminderTodo[],
): string {
  const lines: string[] = [];

  // 当前时间 (LLM 自己判断"一会儿" = 1 小时内)
  const now = new Date();
  const timeStr = now.toLocaleTimeString("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
  lines.push(`现在 ${timeStr}.`);

  // 日历
  if (events.length === 0) {
    lines.push("日历: 本周没排事.");
  } else {
    const summaries = events.slice(0, 5).map(_formatUpcomingEvent);
    lines.push(`日历本周 ${events.length} 件: ${summaries.join(", ")}.`);
  }

  // 邮件
  if (unread === 0) {
    lines.push("邮件: 收件箱已清空.");
  } else {
    lines.push(`邮件: 未读 ${unread} 封.`);
  }

  // 当前未完成任务
  if (todos.length === 0) {
    lines.push("任务库: 当前没有未完成待办.");
  } else {
    const top3 = todos.slice(0, 3).map((t) => t.text);
    lines.push(`当前任务库未完成 ${todos.length} 件 (优先关注: ${top3.join(", ")}).`);
  }

  return lines.join("\n");
}

/** 调 LLM 拿建议. 失败返 null (调用方自己 fallback).
 *
 * timeout 5s — 早安 tab default 展示, 卡 > 3s 员工会切走.
 * (7/24: 原 comment 提 fetchProactiveStarter 已随 BL-PROACTIVE-STARTER-KILL 砍.)
 */
export async function fetchBriefingSuggestion(
  unread: number,
  events: CalendarEvent[],
  todos: ReminderTodo[],
  model: string,
  personality?: Personality,
): Promise<string | null> {
  // 三源都空 → 没必要调 LLM
  if (unread === 0 && events.length === 0 && todos.length === 0) {
    return "今天比较松, 喝杯水?";
  }

  const userPrompt = _buildUserPrompt(unread, events, todos);
  const url = `${config.backendUrl}/v1/chat/completions${SERVICE_LLM_QUERY}`;

  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), 5000);

  try {
    const resp = await fetchWithAuth(url, {
      method: "POST",
      headers: SERVICE_LLM_HEADERS,
      body: JSON.stringify({
        model,
        messages: [
          { role: "system", content: SYSTEM_PROMPT + _personalityHint(personality) },
          { role: "user", content: userPrompt },
        ],
        max_tokens: 80,
        temperature: 0.7,
        stream: false,
      }),
      signal: controller.signal,
    });
    clearTimeout(timeoutId);

    if (!resp.ok) {
      // eslint-disable-next-line no-console
      console.warn(`[briefing] LLM 调用失败 ${resp.status}`);
      return null;
    }
    const data = await resp.json();
    const content = data?.choices?.[0]?.message?.content;
    if (typeof content !== "string") return null;
    // 8/8: 上游把错误当正文返 —— 不拦的话早安卡片上会显示一句英文报错
    // ("API call failed after 3 retries: ...")。详见 upstreamErrorGuard.ts。
    if (warnIfUpstreamError("briefing", content)) return null;

    // 清理 LLM 偶尔返的前缀 (即使 system prompt 要求不要)
    const cleaned = content
      .trim()
      .replace(/^(建议|今日|早安|提示)[:：]\s*/, "")
      .replace(/^["「『]/, "")
      .replace(/["」』]$/, "")
      .trim();

    return cleaned.length > 0 ? cleaned : null;
  } catch (e) {
    clearTimeout(timeoutId);
    // AbortError (timeout) / 网络挂 / JSON 解析失败 都按 null 处理
    // eslint-disable-next-line no-console
    console.warn("[briefing] LLM 调用异常", e);
    return null;
  }
}

// ── BL-BRIEFING-LLM-MERGE (5/20): 合并 2 个 LLM 调用为 1 个 ──────
//
// 9/8: 用户待办统一到本机任务库后，不再从 journal 猜测新 TODO；此调用只负责
// 基于邮件、日历和当前任务写一行主动提醒。

const MERGED_SYSTEM_PROMPT = `你是用户的鲶鱼数字员工 (Catfish)。根据今天的邮件、本周日历和当前未完成任务写一行优先建议:

   - 一行字 30 字内, 按时间紧急度
   - 用"→"连接 2-3 件事
   - 口语化, 不要"为您" / 不要"祝您工作顺利"
   - 只使用输入里已有的事项，不新增或猜测待办
   // P3.5.212 (7/10 鸿波 校正): 走 Companion → Hermes → Gateway, hermes
   // catfish-memory prefetch (P3.5.211) 已注入'事实为准'军规到 system,
   // 这里再加一份重复. 撤回, 军规单点在 hermes 保生效.

返回严格 JSON, 不要 markdown 反引号包裹, 不要前缀:
\`\`\`
{"suggestion": "一会儿 14:00 跟老李会议 → 处理 5 封邮件"}
\`\`\`

suggestion 不能为空字符串 (数据三源都空时返 "今天比较松, 喝杯水?").`;

export interface MergedBriefing {
  suggestion: string;
}

/** BL-COMPANION-EMAIL-DIGEST-STEP2 (5/20): 喂 LLM merged prompt 的急邮件提示
 * (subject + sender), 让 LLM 优先建议更准 (能写"先回老李那封 P0 的"). */
export interface UrgentEmailHint {
  subject: string;
  sender: string;
}

/** 合并 prompt 一次 LLM 调用生成优先建议。
 *
 * 成功 → 返 MergedBriefing。JSON 解析失败 / LLM 挂 → 返 null。
 *
 * timeout 8s，给内网模型留出综合三类输入的时间。
 */
export async function fetchMergedBriefing(
  unread: number,
  events: CalendarEvent[],
  knownTodos: ReminderTodo[],
  model: string,
  urgentEmails: UrgentEmailHint[] = [],
  personality?: Personality,
): Promise<MergedBriefing | null> {
  // 三源都空 → LLM 也写不出, 直接默认
  if (unread === 0 && events.length === 0 && knownTodos.length === 0) {
    return { suggestion: "今天比较松, 喝杯水?" };
  }

  const userPrompt = _buildMergedUserPrompt(unread, events, knownTodos, urgentEmails);
  const url = `${config.backendUrl}/v1/chat/completions${SERVICE_LLM_QUERY}`;

  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), 8000);

  try {
    const resp = await fetchWithAuth(url, {
      method: "POST",
      headers: SERVICE_LLM_HEADERS,
      body: JSON.stringify({
        model,
        messages: [
          { role: "system", content: MERGED_SYSTEM_PROMPT + _personalityHint(personality) },
          { role: "user", content: userPrompt },
        ],
        max_tokens: 400,
        temperature: 0.4,  // 居中: 建议要点自然 (高), TODO 抽取要稳 (低)
        stream: false,
      }),
      signal: controller.signal,
    });
    clearTimeout(timeoutId);
    if (!resp.ok) return null;

    const data = await resp.json();
    const content = data?.choices?.[0]?.message?.content;
    if (typeof content !== "string") return null;
    // 8/8: 上游把错误当正文返 —— 不拦的话早安卡片上会显示一句英文报错
    // ("API call failed after 3 retries: ...")。详见 upstreamErrorGuard.ts。
    if (warnIfUpstreamError("briefing", content)) return null;

    // 剥 markdown ``` 反引号
    const cleaned = content
      .trim()
      .replace(/^```(?:json)?\s*/i, "")
      .replace(/```$/, "")
      .trim();

    let parsed: unknown;
    try {
      parsed = JSON.parse(cleaned);
    } catch {
      // eslint-disable-next-line no-console
      console.warn("[briefing] merged LLM 返了非 JSON, fallback 两次独立调用", cleaned.slice(0, 200));
      return null;
    }

    if (!parsed || typeof parsed !== "object") return null;
    const obj = parsed as { suggestion?: unknown };

    // suggestion 解析
    let suggestion = typeof obj.suggestion === "string" ? obj.suggestion.trim() : "";
    suggestion = suggestion
      .replace(/^(建议|今日|早安|提示)[:：]\s*/, "")
      .replace(/^["「『]/, "")
      .replace(/["」』]$/, "")
      .trim();
    if (!suggestion) suggestion = "今天比较松, 喝杯水?";

    return { suggestion };
  } catch (e) {
    clearTimeout(timeoutId);
    // eslint-disable-next-line no-console
    console.warn("[briefing] merged LLM 调用挂", e);
    return null;
  }
}

function _buildMergedUserPrompt(
  unread: number,
  events: CalendarEvent[],
  knownTodos: ReminderTodo[],
  urgentEmails: UrgentEmailHint[] = [],
): string {
  const lines: string[] = [];

  const now = new Date();
  const timeStr = now.toLocaleTimeString("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
  lines.push(`现在 ${timeStr}.\n`);

  // 日历
  if (events.length === 0) {
    lines.push("日历: 本周没排事.");
  } else {
    const summaries = events.slice(0, 5).map(_formatUpcomingEvent);
    lines.push(`日历本周 ${events.length} 件: ${summaries.join(", ")}.`);
  }

  // 邮件 — 急邮件主题透出 (BL-COMPANION-EMAIL-DIGEST-STEP2 5/20)
  if (unread === 0) {
    lines.push("邮件: 收件箱已清空.");
  } else if (urgentEmails.length > 0) {
    const urgentList = urgentEmails
      .slice(0, 3)
      .map((e) => `"${e.subject}" (来自 ${e.sender})`)
      .join(", ");
    lines.push(`邮件: 未读 ${unread} 封, 其中 ${urgentEmails.length} 封 LLM 评"急": ${urgentList}.`);
  } else {
    lines.push(`邮件: 未读 ${unread} 封 (没"急"邮件).`);
  }

  // 当前未完成任务
  if (knownTodos.length > 0) {
    const list = knownTodos.map((t) => `- ${t.text}`).join("\n");
    lines.push(`\n当前未完成任务:\n${list}`);
  }

  return lines.join("\n");
}

// ── BL-EMAIL-URGENT-LLM-PUSH (5/20): 急邮件提醒 LLM 化 ──────
//
// 5/18 scheduler 拼的 hard-coded starter ("X 那封紧的来了 — Y 帮你看?") 太机械.
// 这里调 LLM 写一句更自然的提醒, 跟早安播报同风格. LLM 挂了 → 调用方用 fallback.

const URGENT_EMAIL_SYSTEM_PROMPT = `你是用户的鲶鱼数字员工 (Catfish). 用户刚收到一批 LLM 评为"急"的邮件,
你的任务: 写一句提醒 (会以小鲶的口吻直接出现在员工的工作台对话里).

风格:
- 一句话, 25 字内
- 不要"为您" / "祝您工作顺利" 客套
- 口语化, 像同事拍一下肩膀 ("张三那封紧的来了 — P0 bug, 帮你过一遍?")
- 多封时用数量 + 第一封发件人 ("来了 3 封紧的, 最紧那封是老李的合同问题")

只返一句话, 不要任何前缀 (不要"提醒:" / "急邮件:") / 解释 / markdown.`;

/** 调 LLM 写一句急邮件提醒. 失败 → 返 null (调用方用 scheduler 给的 fallback).
 *
 * timeout 4s — 提醒是即时体验, 不能卡员工.
 */
export async function fetchUrgentEmailStarter(
  urgentEmails: UrgentEmailHint[],
  model: string,
  personality?: Personality,
): Promise<string | null> {
  if (urgentEmails.length === 0) return null;

  const list = urgentEmails
    .slice(0, 5)
    .map((e, i) => `${i + 1}. ${e.sender} - ${e.subject}`)
    .join("\n");
  const userPrompt = `刚到 ${urgentEmails.length} 封急邮件:\n${list}\n\n写一句提醒.`;

  const url = `${config.backendUrl}/v1/chat/completions${SERVICE_LLM_QUERY}`;
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), 4000);

  try {
    const resp = await fetchWithAuth(url, {
      method: "POST",
      headers: SERVICE_LLM_HEADERS,
      body: JSON.stringify({
        model,
        messages: [
          { role: "system", content: URGENT_EMAIL_SYSTEM_PROMPT + _personalityHint(personality) },
          { role: "user", content: userPrompt },
        ],
        max_tokens: 60,
        temperature: 0.7,
        stream: false,
      }),
      signal: controller.signal,
    });
    clearTimeout(timeoutId);
    if (!resp.ok) return null;

    const data = await resp.json();
    const content = data?.choices?.[0]?.message?.content;
    if (typeof content !== "string") return null;
    // 8/8: 上游把错误当正文返 —— 不拦的话早安卡片上会显示一句英文报错
    // ("API call failed after 3 retries: ...")。详见 upstreamErrorGuard.ts。
    if (warnIfUpstreamError("briefing", content)) return null;

    const cleaned = content
      .trim()
      .replace(/^(提醒|急邮件|急)[:：]\s*/, "")
      .replace(/^["「『]/, "")
      .replace(/["」』]$/, "")
      .trim();

    return cleaned.length > 0 ? cleaned : null;
  } catch (e) {
    clearTimeout(timeoutId);
    // eslint-disable-next-line no-console
    console.warn("[briefing] 急邮件 LLM 调用挂", e);
    return null;
  }
}
