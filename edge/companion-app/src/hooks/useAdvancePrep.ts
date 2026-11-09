/** 提前准备 (11/09 小鲶秘书 第 3 层): 会议、出差拜访、快到期的待办、邮件里要办 / 要回的事。
 *
 * 每 15 分钟看一次四个来源 (日历接下来 4 天 / 任务库 / 邮件分诊), 挑出要提前准备的事 →
 * 给 hermes 建一条一次性准备任务; 事情没了而准备任务还没跑 → 撤掉; 到期过了的记录 → 清掉。
 * 准备任务跑完, 第 1 层 (useCronWatcher) 在提醒条上说「会务安排准备好了」「回复草稿写好了」…
 *
 * 挑事和写任务说明: lib/meetingPrep.ts (日历), lib/prepSources.ts (待办、邮件)。排期: lib/advancePrep.ts。
 */
import { useEffect } from "react";

import {
  localDayKey,
  localIso,
  planPreps,
  type PrepItem,
  type PrepRecord,
  type PrepSource,
} from "../lib/advancePrep";
import { calendarItems } from "../lib/meetingPrep";
import { emailItems, todoItems } from "../lib/prepSources";
import { isTauriRuntime } from "../lib/runtime";
import { MEETING_PREP_KEY, prefEnabled } from "../lib/secretaryPrefs";
import {
  calendarUpcomingFetch,
  cronJobCreate,
  cronJobDelete,
  emailClassifyNow,
  emailListFetch,
  remindersWeekFetch,
  type CalendarEvent,
  type EmailDigestItem,
} from "../lib/tauri";
import type { DueTask } from "../lib/triggers";

/** 第一版 (只有会议) 就用这个 key, 不改: 已经建好的准备任务记录接着用 */
const RECORDS_KEY = "catfish:meeting_prep:v1";
const TICK_MS = 15 * 60_000;
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
export async function advancePrepTick(now = new Date()): Promise<{ created: number; cancelled: number }> {
  if (!prefEnabled(MEETING_PREP_KEY)) return { created: 0, cancelled: 0 };
  // 记不住建过哪些, 每 15 分钟会重复建 —— 宁可不建
  if (!storageWritable()) return { created: 0, cancelled: 0 };
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
  if (created || cancelled) console.log(`[advance-prep] 建了 ${created} 条准备任务, 撤了 ${cancelled} 条`);
  return { created, cancelled };
}

export function useAdvancePrep(): void {
  useEffect(() => {
    if (!isTauriRuntime()) return;
    let running = false;
    const tick = () => {
      if (running) return;
      running = true;
      advancePrepTick()
        .catch((e) => console.warn("[advance-prep] 这一轮没跑成:", e))
        .finally(() => {
          running = false;
        });
    };
    const first = window.setTimeout(tick, 90_000);
    const t = window.setInterval(tick, TICK_MS);
    return () => {
      window.clearTimeout(first);
      window.clearInterval(t);
    };
  }, []);
}
