/** 提前准备 (11/09 小鲶秘书 第 3 层) 的公共部分: 几点跑、建 / 撤 / 清哪些、每天上限、任务名前缀。
 *
 * 四个来源各自挑事、写任务说明 (lib/meetingPrep.ts 日历的会和出差拜访, lib/prepSources.ts 待办和邮件),
 * 这里统一排期: 给 hermes 建一条一次性定时任务, 跑完由第 1 层 (useCronWatcher) 在提醒条上说「准备好了」。
 *
 *   · 明天及以后到期的 → 前一天 16:00 跑 (那时候相关邮件基本到齐了)
 *   · 今天的 / 前一天 16:00 已经过了 → 2 分钟后跑
 *   · 离到期不到 1 小时 → 来不及, 不建
 *   · 事情没了 (会取消 / 改期、待办完成、邮件回过了) → 撤掉还没跑的; 这个来源这一轮读不到就不撤
 *   · 一天最多新建 5 条, 防一堆事把模型额度吃光; 先建最急的
 *
 * 任务说明一律「只查阅和起草」: 不发送、不回复、不改日程和任务 (hermes 定时任务里删除类本来就会被拦, 见 P59)。
 */

const MIN = 60_000;
const HOUR = 60 * MIN;
const DAY = 24 * HOUR;

export const PREP_MAX_PER_DAY = 5;
/** 离到期不到这么久就不准备了 (跑一次要几分钟, 人也来不及看) */
export const PREP_MIN_LEAD_MS = HOUR;
/** 立刻跑的话, 排在几分钟后 (hermes 调度器每分钟看一次) */
export const PREP_SOON_MS = 2 * MIN;
/** 只看这么远以内到期的 */
export const PREP_HORIZON_MS = 4 * DAY;

export type PrepSource = "calendar" | "todo" | "email";

/** 准备任务的种类 —— 任务名前缀, 第 1 层按前缀换「准备好了」的说法 */
export const PREP_KINDS = {
  meeting: { prefix: "会前准备：", done: "会务安排准备好了", fail: "会前准备没做成", chat: "会务安排" },
  trip: { prefix: "出行准备：", done: "出行准备好了", fail: "出行准备没做成", chat: "出行准备" },
  todo: { prefix: "待办初稿：", done: "待办的初稿准备好了", fail: "待办初稿没做成", chat: "待办初稿" },
  emailDo: { prefix: "邮件要办：", done: "邮件里要办的事准备好了", fail: "邮件要办的事没准备成", chat: "邮件要办" },
  emailReply: { prefix: "邮件回复：", done: "回复草稿写好了", fail: "回复草稿没写成", chat: "回复草稿" },
  roomAsk: { prefix: "群里交代：", done: "群里交代的事做好了", fail: "群里交代的事没做成", chat: "群里交代" },
} as const;
export type PrepKind = keyof typeof PREP_KINDS;

/** 任务名 → 种类 + 名字后半截 (「会前准备：周例会（11/10 10:00）」→ meeting, 「周例会（11/10 10:00）」); 不是准备任务返 null */
export function prepLabel(jobName: string): { kind: PrepKind; label: string } | null {
  for (const [kind, k] of Object.entries(PREP_KINDS) as [PrepKind, (typeof PREP_KINDS)[PrepKind]][]) {
    if (jobName.startsWith(k.prefix)) return { kind, label: jobName.slice(k.prefix.length).trim() };
  }
  return null;
}

/** 一件要提前准备的事 */
export interface PrepItem {
  /** 同一件事只准备一次; 截止改了 key 就变, 会重新准备 */
  key: string;
  source: PrepSource;
  /** 到期 / 开始时刻 (ms) */
  dueMs: number;
  name: string;
  prompt: string;
}

export interface PrepRecord {
  jobId: string;
  /** 准备任务排在什么时候跑 (ms) */
  runAt: number;
  /** 事情几点到期 (ms) */
  start: number;
  /** 建任务那天 (本地 YYYY-MM-DD), 算每日上限 */
  day: string;
  /** 11/09 第二版加的; 第一版只有会议, 没有这个字段的按 calendar */
  source?: PrepSource;
  /** hermes 拒了 (400, 多半是外部文字被当成注入特征): 记下不再重试, jobId 为空 */
  rejected?: boolean;
}

function startOfLocalDay(ms: number): number {
  const d = new Date(ms);
  d.setHours(0, 0, 0, 0);
  return d.getTime();
}

/** 什么时候跑准备任务; 来不及 / 太远返 null */
export function prepRunAt(dueMs: number, nowMs: number): number | null {
  if (Number.isNaN(dueMs)) return null;
  if (dueMs - nowMs < PREP_MIN_LEAD_MS) return null;
  if (dueMs - nowMs > PREP_HORIZON_MS) return null;
  const soon = nowMs + PREP_SOON_MS;
  const dueDay = startOfLocalDay(dueMs);
  if (dueDay <= startOfLocalDay(nowMs)) return soon; // 今天到期
  const dayBefore16 = dueDay - DAY + 16 * HOUR;
  // 前一天 16:00 已经过了 → 现在就准备; 但不能晚于到期前 1 小时
  return Math.min(Math.max(dayBefore16, soon), dueMs - PREP_MIN_LEAD_MS);
}

function pad(n: number): string {
  return String(n).padStart(2, "0");
}

/** hermes 认的本地时间 ISO (不带时区 → 按 hermes 配置的时区, 跟本机一致) */
export function localIso(ms: number): string {
  const d = new Date(ms);
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export function localDayKey(ms: number): string {
  const d = new Date(ms);
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

/** 「11/10 10:00」; 0 点 (只有日期) 只说「11/10」 */
export function whenLabel(ms: number): string {
  const d = new Date(ms);
  const md = `${d.getMonth() + 1}/${d.getDate()}`;
  return d.getHours() || d.getMinutes() ? `${md} ${d.getHours()}:${pad(d.getMinutes())}` : md;
}

/** hermes 建任务时把这些当注入特征直接拒 (tools/threat_patterns.py INVISIBLE_CHARS);
 *  Outlook / Teams 邀请、复制来的文字里常带零宽空格。外部来的字一律先去掉。 */
const INVISIBLE = /[\u200b\u200c\u200d\u2060\u2062\u2063\u2064\ufeff\u202a-\u202e\u2066-\u2069]/g;

/** 压成一行、截断、去掉隐形字符 (外部来的字放进任务说明前都过这里) */
export function clip(s: string, n: number): string {
  const t = s.replace(INVISIBLE, "").replace(/\s+/g, " ").trim();
  return t.length > n ? `${t.slice(0, n)}…` : t;
}

/** 所有准备任务说明末尾都接这段 */
export const PREP_RULES =
  "只查阅和起草：不要发送邮件或消息、不要回复、不要改日程和任务、不要删任何东西。"
  + "要发出去的东西写成草稿放在最后给我看。找不到的就写「没找到」，不要编。";

export interface PrepPlan {
  create: { item: PrepItem; runAt: number }[];
  /** 事情没了、准备任务还没跑 → 撤掉 */
  cancel: { key: string; jobId: string }[];
  /** 到期已过的记录 → 清掉 */
  forget: string[];
}

/** 比这一轮挑出的事和已建的准备任务, 算出要建 / 撤 / 清哪些。
 *  readOk: 这一轮读成功、而且读到了东西的来源 —— 只有这些来源的记录才会因为「事情没了」被撤
 *  (日历没授权 / 邮件没配 / 任务库读空, 分不清「没了」和「读不到」)。 */
export function planPreps(
  items: PrepItem[],
  readOk: Set<PrepSource>,
  records: Record<string, PrepRecord>,
  nowMs: number,
): PrepPlan {
  const keysNow = new Map(items.map((it) => [it.key, it]));
  const plan: PrepPlan = { create: [], cancel: [], forget: [] };
  for (const [key, rec] of Object.entries(records)) {
    if (rec.start < nowMs) {
      plan.forget.push(key);
    } else if (readOk.has(rec.source ?? "calendar") && !keysNow.has(key) && rec.runAt > nowMs) {
      plan.cancel.push({ key, jobId: rec.jobId });   // 被拒的 jobId 为空: 调用方只清记录
    }
  }
  const today = localDayKey(nowMs);
  let budget = PREP_MAX_PER_DAY - Object.values(records).filter((r) => r.day === today && !r.rejected).length;
  const pending = items
    .filter((it) => !records[it.key])
    .map((item) => ({ item, runAt: prepRunAt(item.dueMs, nowMs) }))
    .filter((x): x is { item: PrepItem; runAt: number } => x.runAt !== null)
    .sort((a, b) => a.item.dueMs - b.item.dueMs);
  for (const p of pending) {
    if (budget <= 0) break;
    plan.create.push(p);
    budget--;
  }
  return plan;
}
