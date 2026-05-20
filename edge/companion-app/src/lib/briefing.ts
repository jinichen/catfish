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
import { fetchWithAuth } from "./me";
import { journalReadRecent, type CalendarEvent, type JournalTodo } from "./tauri";

/** BL-BRIEFING-LLM-PERSONALITY (5/20): 把员工选的桌宠人格 (gentle/direct/roast)
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
const SERVICE_LLM_HEADERS = {
  "content-type": "application/json",
  "X-Catfish-Source": "companion-briefing-card",
  "X-Catfish-Skip-Identity": "true",
};

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

/** 拼 LLM prompt — 把三源数据浓缩成一段 user message. */
function _buildUserPrompt(
  unread: number,
  events: CalendarEvent[],
  todos: JournalTodo[],
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
    lines.push("日历: 今天没排事.");
  } else {
    const summaries = events.slice(0, 5).map((e) => {
      if (e.all_day) return `全天 ${e.summary}`;
      const startTime = new Date(e.start).toLocaleTimeString("zh-CN", {
        hour: "2-digit",
        minute: "2-digit",
        hour12: false,
      });
      return `${startTime} ${e.summary}`;
    });
    lines.push(`日历今天 ${events.length} 件: ${summaries.join(", ")}.`);
  }

  // 邮件
  if (unread === 0) {
    lines.push("邮件: 收件箱已清空.");
  } else {
    lines.push(`邮件: 未读 ${unread} 封.`);
  }

  // journal TODO
  if (todos.length === 0) {
    lines.push("journal: 没未完成待办.");
  } else {
    const top3 = todos.slice(-3).map((t) => t.text); // 最后 3 条 = 最新
    lines.push(`journal 未完成 ${todos.length} 件 (最新: ${top3.join(", ")}).`);
  }

  return lines.join("\n");
}

/** 调 LLM 拿建议. 失败返 null (调用方自己 fallback).
 *
 * timeout 5s — 比 fetchProactiveStarter 的隐式 timeout 严, 因为这是 dashboard
 * 顶 widget, 卡 > 3s 员工会切走.
 */
export async function fetchBriefingSuggestion(
  unread: number,
  events: CalendarEvent[],
  todos: JournalTodo[],
  model: string,
  personality?: Personality,
): Promise<string | null> {
  // 三源都空 → 没必要调 LLM
  if (unread === 0 && events.length === 0 && todos.length === 0) {
    return "今天比较松, 喝杯水?";
  }

  const userPrompt = _buildUserPrompt(unread, events, todos);
  const url = `${config.backendUrl}/v1/chat/completions`;

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
// 老路径 (BriefingCard 当前): fetchBriefingSuggestion + fetchLlmJournalTodos 并发,
// 卡慢的那个 (~6s). 合并后 1 个 prompt 同时返 JSON `{todos, suggestion}`, 减一半
// 延迟 + 减一半 token. JSON 解析失败 → 调用方 fallback 到两个独立调用.

const MERGED_SYSTEM_PROMPT = `你是用户的鲶鱼数字员工 (Catfish), 帮员工同时干两件事:

1. **抽 journal 里"没标 TODO 但显然是待办"的事** (LLM 推断 TODO)
   - 只抽未完成的事 (员工说"明天要 X" / "还得 Z")
   - 不抽已发生 / 已完成 / 已有显式 \`- [ ]\` 或 \`TODO:\` 标记的
   - 不要 hallucinate, 不要把"想法"当待办

2. **写一行优先建议** (Daily Briefing 的 💡 行)
   - 一行字 30 字内, 按时间紧急度
   - 用"→"连接 2-3 件事
   - 口语化, 不要"为您" / 不要"祝您工作顺利"

返回严格 JSON, 不要 markdown 反引号包裹, 不要前缀:
\`\`\`
{"todos": ["待办描述 1", "待办描述 2"], "suggestion": "一会儿 14:00 跟老李会议 → 处理 5 封邮件"}
\`\`\`

todos 数组可为空 \`[]\` (没抽到自然语言 TODO). suggestion 不能为空字符串 (数据三源都空时返 "今天比较松, 喝杯水?").`;

export interface MergedBriefing {
  todos: JournalTodo[];
  suggestion: string;
}

/** BL-COMPANION-EMAIL-DIGEST-STEP2 (5/20): 喂 LLM merged prompt 的急邮件提示
 * (subject + sender), 让 LLM 优先建议更准 (能写"先回老李那封 P0 的"). */
export interface UrgentEmailHint {
  subject: string;
  sender: string;
}

/** 合并 prompt 一次 LLM 调用拿 TODO + 优先建议.
 *
 * 成功 → 返 MergedBriefing. JSON 解析失败 / LLM 挂 → 返 null (调用方 fallback 两次独立调用).
 *
 * timeout 8s (比单调用稍宽, 因为 LLM 要 reason 两件事 + 输出 JSON 更长).
 */
export async function fetchMergedBriefing(
  unread: number,
  events: CalendarEvent[],
  knownTodos: JournalTodo[],
  model: string,
  urgentEmails: UrgentEmailHint[] = [],
  personality?: Personality,
): Promise<MergedBriefing | null> {
  // 三源都空 → LLM 也写不出, 直接默认
  if (unread === 0 && events.length === 0 && knownTodos.length === 0) {
    return { todos: [], suggestion: "今天比较松, 喝杯水?" };
  }

  let journalText: string;
  try {
    journalText = await journalReadRecent();
  } catch {
    journalText = "";
  }

  const userPrompt = _buildMergedUserPrompt(unread, events, knownTodos, journalText, urgentEmails);
  const url = `${config.backendUrl}/v1/chat/completions`;

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
    const obj = parsed as { todos?: unknown; suggestion?: unknown };

    // todos 解析 — 同 fetchLlmJournalTodos 套路
    const todosArr: JournalTodo[] = Array.isArray(obj.todos)
      ? obj.todos
          .filter((x): x is string => typeof x === "string" && x.trim().length > 0)
          .slice(0, 10)
          .map<JournalTodo>((text) => ({
            text: text.trim(),
            line: 0,
            source: "inline",
            section: "(LLM 推断)",
          }))
      : [];

    // suggestion 解析
    let suggestion = typeof obj.suggestion === "string" ? obj.suggestion.trim() : "";
    suggestion = suggestion
      .replace(/^(建议|今日|早安|提示)[:：]\s*/, "")
      .replace(/^["「『]/, "")
      .replace(/["」』]$/, "")
      .trim();
    if (!suggestion) suggestion = "今天比较松, 喝杯水?";

    return { todos: todosArr, suggestion };
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
  knownTodos: JournalTodo[],
  journalText: string,
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
    lines.push("日历: 今天没排事.");
  } else {
    const summaries = events.slice(0, 5).map((e) => {
      if (e.all_day) return `全天 ${e.summary}`;
      const startTime = new Date(e.start).toLocaleTimeString("zh-CN", {
        hour: "2-digit",
        minute: "2-digit",
        hour12: false,
      });
      return `${startTime} ${e.summary}`;
    });
    lines.push(`日历今天 ${events.length} 件: ${summaries.join(", ")}.`);
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

  // 已显式抽到的 TODO
  if (knownTodos.length > 0) {
    const list = knownTodos.map((t) => `- ${t.text}`).join("\n");
    lines.push(`\nregex 已抽到的显式 TODO (跳过别重复):\n${list}`);
  }

  // journal 全文 (给 LLM 抽自然语言 TODO 用)
  if (journalText.trim().length > 0) {
    lines.push(`\njournal 最近内容 (抽未标 TODO 的自然语言待办):\n${journalText}`);
  }

  return lines.join("\n");
}

// ── BL-EMAIL-URGENT-LLM-PUSH (5/20): 急邮件桌宠播报 LLM 化 ──────
//
// 5/18 scheduler 拼的 hard-coded starter ("X 那封紧的来了 — Y 帮你看?") 太机械.
// 这里调 LLM 写一句更自然的提醒, 跟早安播报同风格. LLM 挂了 → 调用方用 fallback.

const URGENT_EMAIL_SYSTEM_PROMPT = `你是用户的鲶鱼数字员工 (Catfish). 用户刚收到一批 LLM 评为"急"的邮件,
你的任务: 写一句给桌宠的提醒 (员工抬头看到桌宠头顶气泡).

风格:
- 一句话, 25 字内
- 不要"为您" / "祝您工作顺利" 客套
- 口语化, 像同事拍一下肩膀 ("张三那封紧的来了 — P0 bug, 帮你过一遍?")
- 多封时用数量 + 第一封发件人 ("来了 3 封紧的, 最紧那封是老李的合同问题")

只返一句话, 不要任何前缀 (不要"提醒:" / "急邮件:") / 解释 / markdown.`;

/** 调 LLM 写一句给桌宠的急邮件提醒. 失败 → 返 null (调用方用 scheduler 给的 fallback).
 *
 * timeout 4s — 桌宠播报体验 critical, 不能卡员工.
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
  const userPrompt = `刚到 ${urgentEmails.length} 封急邮件:\n${list}\n\n写一句桌宠提醒.`;

  const url = `${config.backendUrl}/v1/chat/completions`;
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

// ── BL-JOURNAL-TODO-EXTRACT step2: LLM 抽自然语言 TODO ────────

const TODO_EXTRACT_SYSTEM_PROMPT = `你是用户的鲶鱼数字员工 (Catfish), 帮员工从 journal 文字里抽"显然是待办但没标 TODO 的事".

规则:
- 只抽**未完成**的事 (员工说"明天要 X" / "下周要 Y" / "还得 Z" 这种)
- 不抽**已发生**的事 (员工说"今天搞定了 X" / "刚才跟老李谈了" 不抽)
- 不抽**已有显式标记**的事 (如果员工写了 \`- [ ] X\` 或 \`TODO: X\`, 跳过 — 这些 regex 已抽过)
- 不要 hallucinate / 不要把"想法"当待办 (员工说"觉得应该改架构"不是待办, 除非他后面写"下周要试")

返回严格 JSON array, 每项是字符串 (一句话描述). 没找到返 \`[]\`. 不要任何 markdown / 解释 / 前缀.

示例:
- 输入: "今天跟老李会谈了 PPT 风格. 明天要去给领导汇报年度规划."
- 输出: \`["给领导汇报年度规划"]\`
- 输入: "- [x] 写完周报\\n- [ ] 改 P0 bug"
- 输出: \`[]\`  (一个已完成, 一个 regex 已抽, 都跳过)`;

/** LLM 抽 journal 自然语言 TODO. 跟 regex 已抽的合并去重.
 *
 * 失败返 [] (调用方按"没找到"处理, 不掉 regex 已抽的).
 */
export async function fetchLlmJournalTodos(
  knownTodos: JournalTodo[],
  model: string,
  // TODO 抽取是分析任务, personality 影响小, 但保签名一致方便调用方
  personality?: Personality,
): Promise<JournalTodo[]> {
  let journalText: string;
  try {
    journalText = await journalReadRecent();
  } catch {
    return [];
  }
  if (journalText.trim().length === 0) return [];

  // 喂 LLM "已抽的别再重" 提示, 减幻觉
  const knownList = knownTodos.map((t) => `- ${t.text}`).join("\n");
  const userPrompt =
    `已经抽到的 TODO (跳过这些, 别重复):\n${knownList || "(无)"}\n\n` +
    `journal 内容:\n${journalText}\n\n` +
    `请返自然语言 TODO 的 JSON array.`;

  const url = `${config.backendUrl}/v1/chat/completions`;
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), 6000);

  try {
    const resp = await fetchWithAuth(url, {
      method: "POST",
      headers: SERVICE_LLM_HEADERS,
      body: JSON.stringify({
        model,
        messages: [
          // TODO 抽取 personality 影响小, 仍注入 (返 JSON 时影响不大, 保一致)
          { role: "system", content: TODO_EXTRACT_SYSTEM_PROMPT + _personalityHint(personality) },
          { role: "user", content: userPrompt },
        ],
        max_tokens: 300,
        temperature: 0.2,  // 抽取任务用低温度, 减幻觉
        stream: false,
      }),
      signal: controller.signal,
    });
    clearTimeout(timeoutId);
    if (!resp.ok) return [];

    const data = await resp.json();
    const content = data?.choices?.[0]?.message?.content;
    if (typeof content !== "string") return [];

    // LLM 偶尔包 markdown ```json ... ``` 反引号, 剥一层
    const cleaned = content
      .trim()
      .replace(/^```(?:json)?\s*/i, "")
      .replace(/```$/, "")
      .trim();

    let arr: unknown;
    try {
      arr = JSON.parse(cleaned);
    } catch {
      return [];
    }
    if (!Array.isArray(arr)) return [];

    return arr
      .filter((x): x is string => typeof x === "string" && x.trim().length > 0)
      .slice(0, 10)  // 最多 10 条防 LLM 爆量
      .map<JournalTodo>((text) => ({
        text: text.trim(),
        line: 0,             // LLM 抽的没行号
        source: "inline",    // 当 inline 同等优先级
        section: "(LLM 推断)",
      }));
  } catch (e) {
    clearTimeout(timeoutId);
    // eslint-disable-next-line no-console
    console.warn("[briefing] LLM 抽 TODO 挂", e);
    return [];
  }
}
