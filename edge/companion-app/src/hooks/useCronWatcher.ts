/** 定时任务做完就报 (11/09 小鲶秘书 第 1 层) —— 每分钟比一次 jobs.json, 新的运行挂到提醒条。
 *
 * 规则 (报什么 / 不报什么) 全在 lib/cronWatch.ts。这里只管: 读任务、读结果存档、挂提醒、发系统通知。
 *
 * 系统通知: 窗口不在前台、又不在夜间免打扰 (22–7 点) 时发一条; 同一轮有几条就合成一条通知。
 * 回到 Companion 时落到工作台 (store/reminders noteNotified → useProactiveTriggers 的获焦监听)。
 */
import { useEffect } from "react";

import {
  cronReminder,
  diffCronRuns,
  outputBody,
  type CronRunEvent,
  type CronSeen,
} from "../lib/cronWatch";
import type { Reminder } from "../lib/reminders";
import { isTauriRuntime } from "../lib/runtime";
import { cronJobOutputRead, cronJobOutputs, cronJobsList, sendNotification } from "../lib/tauri";
import { QUIET_HOURS_END, QUIET_HOURS_START } from "../lib/triggers";
import { useAgentStore } from "../store/agent";
import { useRemindersStore } from "../store/reminders";

const SEEN_KEY = "catfish:cron_seen:v1";
const TICK_MS = 60_000;

function loadSeen(): CronSeen | null {
  try {
    const raw = localStorage.getItem(SEEN_KEY);
    if (!raw) return null;
    const v = JSON.parse(raw);
    return v && typeof v === "object" ? (v as CronSeen) : null;
  } catch {
    return null;
  }
}

function saveSeen(s: CronSeen): void {
  try {
    localStorage.setItem(SEEN_KEY, JSON.stringify(s));
  } catch {
    /* 写不进: 下次启动当第一次, 只记现状不报 —— 少报不多报 */
  }
}

/** 存档文件名 (hermes 写的是本地时间 2026-11-09_07-00-34) → ms */
export function outputTsMs(ts: string): number {
  const m = /^(\d{4})-(\d{2})-(\d{2})_(\d{2})-(\d{2})-(\d{2})$/.exec(ts);
  if (!m) return NaN;
  return new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +m[6]).getTime();
}

/** 这次运行的结果: 找最新那份存档, 而且时间要对得上 (存档在运行结束前后几分钟内写) */
async function loadRunOutput(ev: CronRunEvent): Promise<{ ts?: string; body: string | null; silent: boolean }> {
  try {
    const outs = await cronJobOutputs(ev.job.id, 1);
    const latest = outs[0];
    if (!latest) return { body: null, silent: false };
    const t = outputTsMs(latest.timestamp);
    const runMs = Date.parse(ev.runAt);
    // 存档时刻 = 运行开始 (文件名) ≤ last_run_at (结束); 一次运行最长按 3 小时算
    if (Number.isNaN(t) || t > runMs + 5 * 60_000 || runMs - t > 3 * 3600_000) return { body: null, silent: false };
    const body = outputBody(await cronJobOutputRead(ev.job.id, latest.timestamp));
    return { ts: latest.timestamp, body, silent: body === null };
  } catch (e) {
    console.warn("[cron-watch] 读结果存档失败:", e);
    return { body: null, silent: false };
  }
}

function inQuietHours(d: Date): boolean {
  const h = d.getHours();
  return h >= QUIET_HOURS_START || h < QUIET_HOURS_END;
}

function notificationFor(added: Reminder[]): { title: string; body: string } {
  if (added.length === 1) {
    const r = added[0];
    const title = r.cron?.status === "error" ? "⚠️ 定时任务没做成" : r.cron?.prep ? "📋 会务安排准备好了" : "✅ 定时任务做完了";
    return { title, body: r.meta ? `${r.text} · ${r.meta}` : r.text };
  }
  const failed = added.filter((r) => r.cron?.status === "error").length;
  return {
    title: failed ? `定时任务：${added.length - failed} 条做完、${failed} 条没做成` : `✅ ${added.length} 条定时任务做完了`,
    body: added.map((r) => r.text).join("\n"),
  };
}

/** 一轮。export 给测试。 */
export async function cronWatchTick(now = new Date()): Promise<Reminder[]> {
  const jobs = await cronJobsList();
  const { events, next } = diffCronRuns(jobs, loadSeen(), now.getTime());
  saveSeen(next);
  if (events.length === 0) return [];
  const agentName = useAgentStore.getState().name || "小鲶";
  const store = useRemindersStore.getState();
  const added: Reminder[] = [];
  for (const ev of events) {
    let ts: string | undefined;
    let body: string | null = null;
    if (ev.status !== "error") {
      const out = await loadRunOutput(ev);
      if (out.silent) continue; // 任务自己说没新东西 ([SILENT]) —— 不打扰
      ts = out.ts;
      body = out.body;
    }
    const r = cronReminder(ev, body, ts, now, agentName);
    if (store.add(r, now.getTime())) added.push(r);
  }
  if (added.length > 0 && typeof document !== "undefined" && !document.hasFocus() && !inQuietHours(now)) {
    const { title, body } = notificationFor(added);
    try {
      await sendNotification(title, body);
      useRemindersStore.getState().noteNotified(now.getTime());
    } catch (e) {
      console.warn("[cron-watch] 系统通知发不出去:", e);
    }
  }
  return added;
}

export function useCronWatcher(): void {
  useEffect(() => {
    if (!isTauriRuntime()) return;
    let running = false;
    const tick = () => {
      if (running) return; // 读存档慢的时候别叠两轮
      running = true;
      cronWatchTick()
        .catch((e) => console.warn("[cron-watch] 这一轮没跑成 (hermes 没起来?):", e))
        .finally(() => {
          running = false;
        });
    };
    tick();
    const t = window.setInterval(tick, TICK_MS);
    return () => window.clearInterval(t);
  }, []);
}

/** 测试用 */
export const CRON_SEEN_STORAGE_KEY = SEEN_KEY;
