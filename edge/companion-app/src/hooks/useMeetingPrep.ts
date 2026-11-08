/** 会前自动准备会务安排 (11/09 小鲶秘书 第 3 层)。规则在 lib/meetingPrep.ts。
 *
 * 每 15 分钟看一次接下来 4 天的日历: 新的会 → 给 hermes 建一条一次性准备任务;
 * 日历上没了 (取消 / 改期) 而准备任务还没跑 → 撤掉; 开过的会 → 记录清掉。
 * 准备任务跑完, 第 1 层 (useCronWatcher) 在提醒条上说「会务安排准备好了」。
 */
import { useEffect } from "react";

import {
  localDayKey,
  localIso,
  planPreps,
  prepJobName,
  prepJobPrompt,
  type PrepRecord,
} from "../lib/meetingPrep";
import { isTauriRuntime } from "../lib/runtime";
import { MEETING_PREP_KEY, prefEnabled } from "../lib/secretaryPrefs";
import { calendarUpcomingFetch, cronJobCreate, cronJobDelete, type CalendarEvent } from "../lib/tauri";

const RECORDS_KEY = "catfish:meeting_prep:v1";
const TICK_MS = 15 * 60_000;

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

/** 一轮。export 给测试。 */
export async function meetingPrepTick(now = new Date()): Promise<{ created: number; cancelled: number }> {
  if (!prefEnabled(MEETING_PREP_KEY)) return { created: 0, cancelled: 0 };
  // 记不住建过哪些, 每 15 分钟会重复建 —— 宁可不建
  if (!storageWritable()) return { created: 0, cancelled: 0 };
  let events: CalendarEvent[];
  try {
    const parsed = JSON.parse(await calendarUpcomingFetch());
    if (!Array.isArray(parsed)) return { created: 0, cancelled: 0 };
    events = parsed as CalendarEvent[];
  } catch (e) {
    // 读不到日历 (没授权 / Outlook 没开): 这一轮什么都不动, 尤其不能当成「会都取消了」
    console.warn("[meeting-prep] 日历读不到, 跳过:", e);
    return { created: 0, cancelled: 0 };
  }
  const nowMs = now.getTime();
  const records = loadRecords();
  const plan = planPreps(events, records, nowMs);
  for (const key of plan.forget) delete records[key];

  let cancelled = 0;
  for (const c of plan.cancel) {
    try {
      await cronJobDelete(c.jobId);
      cancelled++;
    } catch (e) {
      // 任务已经不在 (员工在仪表盘删了 / hermes 清掉了) 也算撤了
      console.warn(`[meeting-prep] 撤准备任务 ${c.jobId}:`, e);
    }
    delete records[c.key];
  }

  let created = 0;
  for (const p of plan.create) {
    try {
      const job = await cronJobCreate({
        name: prepJobName(p.ev),
        schedule: localIso(p.runAt),
        prompt: prepJobPrompt(p.ev),
        repeat: 1,
      });
      records[p.key] = { jobId: job.id, runAt: p.runAt, start: Date.parse(p.ev.start), day: localDayKey(nowMs) };
      created++;
    } catch (e) {
      // hermes 没起来 / 拒了: 不记, 下一轮再试
      console.warn(`[meeting-prep] 建准备任务失败 (${p.ev.summary}):`, e);
    }
  }
  saveRecords(records);
  if (created || cancelled) console.log(`[meeting-prep] 建了 ${created} 条准备任务, 撤了 ${cancelled} 条`);
  return { created, cancelled };
}

export function useMeetingPrep(): void {
  useEffect(() => {
    if (!isTauriRuntime()) return;
    let running = false;
    const tick = () => {
      if (running) return;
      running = true;
      meetingPrepTick()
        .catch((e) => console.warn("[meeting-prep] 这一轮没跑成:", e))
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
