/** 提前准备 (11/09 小鲶秘书 第 3 层): 会议、出差拜访、快到期的待办、邮件里要办 / 要回的事。
 *
 * 每 15 分钟看一次四个来源 (日历接下来 4 天 / 任务库 / 邮件分诊), 挑出要提前准备的事 →
 * 给 hermes 建一条一次性准备任务; 事情没了而准备任务还没跑 → 撤掉; 到期过了的记录 → 清掉;
 * 到点时电脑睡着、hermes 把它丢了 → 补建一次 (11/14)。
 * 准备任务跑完, 第 1 层 (useCronWatcher) 在提醒条上说「会务安排准备好了」「回复草稿写好了」…
 *
 * 挑事和写任务说明: lib/meetingPrep.ts (日历), lib/prepSources.ts (待办、邮件)。排期: lib/advancePrep.ts。
 */
import {
  catchupCandidates,
  isMissedTrace,
  localDayKey,
  localIso,
  planPreps,
  PREP_SOON_MS,
  type PrepItem,
  type PrepRecord,
  type PrepSource,
} from "../lib/advancePrep";
import { calendarItems } from "../lib/meetingPrep";
import { emailItems, todoItems } from "../lib/prepSources";
import { MEETING_PREP_KEY, prefEnabled } from "../lib/secretaryPrefs";
import {
  calendarUpcomingFetch,
  cronJobCreate,
  cronJobDelete,
  cronJobOutputs,
  emailClassifyNow,
  emailListFetch,
  remindersWeekFetch,
  type CalendarEvent,
  type EmailDigestItem,
} from "../lib/tauri";
import type { DueTask } from "../lib/triggers";

/** 第一版 (只有会议) 就用这个 key, 不改: 已经建好的准备任务记录接着用 */
const RECORDS_KEY = "catfish:meeting_prep:v1";
/** 邮件只看最近这么多封 (跟个人画像同一个量; 分诊缓存也只覆盖列表里的) */
const EMAIL_LIMIT = 100;

function loadRecords(): Record<string, PrepRecord> {
  try {
    const raw = localStorage.getItem(RECORDS_KEY);
    const v = raw ? JSON.parse(raw) : {};
    return v && typeof v === "object" ? v : {};
  } catch {
    return {};
  }
}

function saveRecords(r: Record<string, PrepRecord>): void {
  try {
    localStorage.setItem(RECORDS_KEY, JSON.stringify(r));
  } catch {
    /* 写不进: 下一轮会再建一次 —— 所以写不进时这一轮不建 (见 tick) */
  }
}

function storageWritable(): boolean {
  try {
    localStorage.setItem(`${RECORDS_KEY}:probe`, "1");
    localStorage.removeItem(`${RECORDS_KEY}:probe`);
    return true;
  } catch {
    return false;
  }
}

function parseArray<T>(raw: string): T[] {
  const v = JSON.parse(raw);
  if (!Array.isArray(v)) throw new Error("不是数组");
  return v as T[];
}

/** 读一个来源。读不到 (没授权 / 没配邮箱 / tool-bridge 没起来) —— 这一轮这个来源什么都不动,
 *  尤其不能当成「事情都没了」去撤准备任务。读到空列表也不算 readOk (分不清「真没有」和「读空」)。 */
async function gather(nowMs: number): Promise<{ items: PrepItem[]; readOk: Set<PrepSource> }> {
  const items: PrepItem[] = [];
  const readOk = new Set<PrepSource>();
  const [cal, todos, mails, triage] = await Promise.allSettled([
    calendarUpcomingFetch().then((r) => parseArray<CalendarEvent>(r)),
    remindersWeekFetch().then((r) => parseArray<DueTask>(r)),
    // 收件箱全量 (已读的也要: 读完不等于办完) + 发件箱 (判断回过没有, 读不到就当没回)
    Promise.all([
      emailListFetch(false, EMAIL_LIMIT).then((r) => parseArray<EmailDigestItem>(r)),
      emailListFetch(false, EMAIL_LIMIT, "Sent").then((r) => parseArray<EmailDigestItem>(r)).catch(() => [] as EmailDigestItem[]),
    ]),
    emailClassifyNow([]), // 不带邮件 = 只取分诊缓存, 不调模型
  ]);
  if (cal.status === "fulfilled") {
    items.push(...calendarItems(cal.value));
    if (cal.value.length) readOk.add("calendar");
  } else console.warn("[advance-prep] 日历读不到, 这一轮跳过日历:", cal.reason);
  if (todos.status === "fulfilled") {
    items.push(...todoItems(todos.value));
    if (todos.value.length) readOk.add("todo");
  } else console.warn("[advance-prep] 任务库读不到, 这一轮跳过待办:", todos.reason);
  if (mails.status === "fulfilled" && triage.status === "fulfilled") {
    const [inbox, sent] = mails.value;
    items.push(...emailItems(triage.value.actions, inbox, nowMs, sent));
    if (inbox.length) readOk.add("email");
  } else console.warn("[advance-prep] 邮件读不到, 这一轮跳过邮件");
  return { items, readOk };
}

/** 一轮。export 给测试。 */
export async function advancePrepTick(now = new Date()): Promise<{ created: number; cancelled: number; caughtUp: number }> {
  if (!prefEnabled(MEETING_PREP_KEY)) return { created: 0, cancelled: 0, caughtUp: 0 };
  // 记不住建过哪些, 每 15 分钟会重复建 —— 宁可不建
  if (!storageWritable()) return { created: 0, cancelled: 0, caughtUp: 0 };
  const nowMs = now.getTime();
  const { items, readOk } = await gather(nowMs);
  const records = loadRecords();
  const plan = planPreps(items, readOk, records, nowMs);
  for (const key of plan.forget) delete records[key];

  let cancelled = 0;
  for (const c of plan.cancel) {
    if (!c.jobId) {   // hermes 当初就拒了, 没有任务可撤
      delete records[c.key];
      continue;
    }
    try {
      await cronJobDelete(c.jobId);
      cancelled++;
    } catch (e) {
      // 任务已经不在 (员工在仪表盘删了 / hermes 清掉了) 也算撤了
      console.warn(`[advance-prep] 撤准备任务 ${c.jobId}:`, e);
    }
    delete records[c.key];
  }

  const caughtUp = await catchUpMissed(items, records, nowMs);

  let created = 0;
  for (const p of plan.create) {
    try {
      const job = await cronJobCreate({ name: p.item.name, schedule: localIso(p.runAt), prompt: p.item.prompt, repeat: 1 });
      records[p.item.key] = { jobId: job.id, runAt: p.runAt, start: p.item.dueMs, day: localDayKey(nowMs), source: p.item.source };
      created++;
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      if (/^HTTP 400 /.test(msg)) {
        // hermes 明确拒了 (多半是邮件 / 日程里的字被当成注入特征): 记下不再每 15 分钟重试
        records[p.item.key] = { jobId: "", runAt: p.runAt, start: p.item.dueMs, day: localDayKey(nowMs), source: p.item.source, rejected: true };
        console.warn(`[advance-prep] hermes 拒了准备任务 (${p.item.name}), 不再重试:`, msg);
      } else {
        // hermes 没起来 / 连不上: 不记, 下一轮再试
        console.warn(`[advance-prep] 建准备任务失败 (${p.item.name}):`, msg);
      }
    }
  }
  saveRecords(records);
  if (created || cancelled || caughtUp) {
    console.log(`[advance-prep] 建了 ${created} 条准备任务, 撤了 ${cancelled} 条, 补了 ${caughtUp} 条错过的`);
  }
  return { created, cancelled, caughtUp };
}

/** 到点时电脑睡着 / 关着, hermes 过点 2 分钟就把一次性任务丢了 (不跑, 只留一份存档)。
 *  看到那份存档、事情这一轮还在、离到期还来得及 → 补建一条 2 分钟后跑的, 只补一次。
 *  事情这一轮没读到 (来源读不成) 就等下一轮: 记录里没存任务说明, 要用这一轮重新写的。 */
async function catchUpMissed(items: PrepItem[], records: Record<string, PrepRecord>, nowMs: number): Promise<number> {
  const byKey = new Map(items.map((it) => [it.key, it]));
  let n = 0;
  for (const key of catchupCandidates(records, nowMs)) {
    const rec = records[key];
    const item = byKey.get(key);
    if (!item) continue;
    try {
      const outs = await cronJobOutputs(rec.jobId, 3);
      if (!outs.some((o) => isMissedTrace(o.snippet))) continue;   // 跑过了 / 员工自己删的 / 还没轮到 hermes 处理
      const runAt = nowMs + PREP_SOON_MS;
      const job = await cronJobCreate({ name: item.name, schedule: localIso(runAt), prompt: item.prompt, repeat: 1 });
      records[key] = { ...rec, jobId: job.id, runAt, caughtUp: true };
      n++;
      console.log(`[advance-prep] 「${item.name}」到点时电脑没开, hermes 没跑; 补建了一条 2 分钟后跑`);
    } catch (e) {
      console.warn(`[advance-prep] 补建错过的准备任务失败 (${item.name}), 下一轮再试:`, e);
    }
  }
  return n;
}

/** 11/09 关掉「提前准备」: 已经排上、还没到点的准备任务撤掉 (以前关了照样会跑)。返回撤了几条。 */
export async function cancelPendingPreps(now = Date.now()): Promise<number> {
  const records = loadRecords();
  let cancelled = 0;
  for (const [key, rec] of Object.entries(records)) {
    if (rec.jobId && rec.runAt > now) {
      try {
        await cronJobDelete(rec.jobId);
        cancelled++;
      } catch (e) {
        console.warn(`[advance-prep] 关掉时撤准备任务 ${rec.jobId}:`, e);   // 已经不在也算撤了
      }
    }
    delete records[key];
  }
  saveRecords(records);
  return cancelled;
}
