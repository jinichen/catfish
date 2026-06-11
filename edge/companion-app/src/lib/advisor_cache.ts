/** BL-ADVISOR-CACHE + CONFIG (5/22 Phase 7 cold start v3): TS wrapper.
 *
 * 5/22 鸿波: 每次切早安 tab 重算浪费 token. 改 = cache + 时段触发.
 *
 * 时段从 ~/.catfish/companion.yaml advisor.refresh_times 读 (yaml 改重启 Companion 生效).
 * 默认: ["08:30", "11:30", "14:00", "16:30"], cache_max_age_minutes: 90.
 */

import { invoke as rawInvoke } from "@tauri-apps/api/core";
import type { AdvisorResult } from "./briefing_advisor";

export interface AdvisorCache {
  computedAt: string;          // ISO-8601
  result: AdvisorResult;
  model?: string;
  promptTokens?: number;
  /** P3.3.12 (6/10): task chat summary cache, key=taskUid.
   *  jsonl size 没变就复用 — 没有新消息进 chat, summary 还是有效的. */
  taskChatSummaries?: Record<string, TaskChatSummary>;
}

/** P3.3.12: 单条 task chat 的 LLM summary cache 项. */
export interface TaskChatSummary {
  /** LLM 给的 100-150 字 summary, 说明员工跟这条 task 聊到哪. */
  summary: string;
  /** P3.3.12 老字段 — jsonl file size (byte). 跟当前 jsonlSize 对比一致复用.
   *  P3.3.19 C Phase 5: 改 state.db 后 jsonlSize 不再用. messageCount 是新 source of truth.
   *  保留字段名做 backward compat — 老 cache 没破坏. */
  jsonlSize: number;
  /** P3.3.19 C Phase 5 (6/11): state.db session.meta.messageCount. 优先用这个判断
   *  cache 失效 (jsonl 时代去了). 没 messageCount 字段时 fallback jsonlSize. */
  messageCount?: number;
  /** ISO-8601, summary 算出来的时刻. */
  computedAt: string;
}

export interface AdvisorConfig {
  refreshTimes: string[];      // ["HH:MM", ...]
  cacheMaxAgeMinutes: number;
}

export const advisorCacheGet = () =>
  rawInvoke<AdvisorCache | null>("advisor_cache_get");

export const advisorCacheSave = (cache: AdvisorCache) =>
  rawInvoke<void>("advisor_cache_save", { cache });

export const advisorCacheClear = () =>
  rawInvoke<void>("advisor_cache_clear");

export const advisorConfigGet = () =>
  rawInvoke<AdvisorConfig>("advisor_config_get");

// ── BL-ADVISOR-TASK-STATE (5/22 鸿波): done/snoozed/ignored ─────

export type TaskStatus = "done" | "ignored" | "snoozed";

export interface TaskStateEntry {
  status: TaskStatus;
  ts: string;
}

export interface TaskStateFetch {
  /** 今天 done/ignored/snoozed (key = task title) */
  today: Record<string, TaskStateEntry>;
  /** 昨天 snoozed 的 task title 列表 — 今天 advisor 出来时该标"昨天推的" */
  yesterdaySnoozed: string[];
}

export const advisorTaskStateGet = () =>
  rawInvoke<TaskStateFetch>("advisor_task_state_get");

export const advisorTaskStateSet = (taskTitle: string, status: TaskStatus) =>
  rawInvoke<void>("advisor_task_state_set", { taskTitle, status });

export const advisorTaskStateClear = (taskTitle: string) =>
  rawInvoke<void>("advisor_task_state_clear", { taskTitle });

export const advisorTaskStatePruneOld = () =>
  rawInvoke<number>("advisor_task_state_prune_old");

// ── 时段判断 helpers ──────────────────────────────────────────

/** 缓存是否还新鲜 (在 cache_max_age_minutes 内). */
export function isCacheFresh(cache: AdvisorCache, maxAgeMinutes: number): boolean {
  try {
    const computedAt = new Date(cache.computedAt).getTime();
    const ageMin = (Date.now() - computedAt) / 60000;
    return ageMin < maxAgeMinutes;
  } catch {
    return false;
  }
}

/** 算 cache 多老 (分钟). 失败返 Infinity. */
export function cacheAgeMinutes(cache: AdvisorCache): number {
  try {
    return (Date.now() - new Date(cache.computedAt).getTime()) / 60000;
  } catch {
    return Infinity;
  }
}

/** 把 HH:MM 转成今天的 Date 对象 (本机时区). */
function hhmmToTodayDate(hhmm: string): Date | null {
  const m = hhmm.match(/^(\d{2}):(\d{2})$/);
  if (!m) return null;
  const h = parseInt(m[1], 10);
  const min = parseInt(m[2], 10);
  if (h >= 24 || min >= 60) return null;
  const d = new Date();
  d.setHours(h, min, 0, 0);
  return d;
}

/** 今天下一个未到的时段 Date. 所有时段都过了返 null (今天没了). */
export function nextRefreshAfter(now: Date, refreshTimes: string[]): Date | null {
  const today: Date[] = refreshTimes
    .map(hhmmToTodayDate)
    .filter((d): d is Date => d !== null);
  today.sort((a, b) => a.getTime() - b.getTime());
  for (const t of today) {
    if (t.getTime() > now.getTime()) return t;
  }
  return null;
}

/** 判断 prevTick → now 之间是否跨过某个 refresh_time.
 *  setInterval 用: 每分钟检查, 跨过就触发后台 refresh. */
export function didCrossRefreshTime(
  prevTick: Date,
  now: Date,
  refreshTimes: string[],
): boolean {
  return refreshTimes.some((hhmm) => {
    const t = hhmmToTodayDate(hhmm);
    if (!t) return false;
    return t.getTime() > prevTick.getTime() && t.getTime() <= now.getTime();
  });
}
