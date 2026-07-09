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

/** P3.5.202 (C 方案 7/9 鸿波): LLM 判定员工在这条 task 的最新状态.
 *  用于 filterResolvedTasks 语义判 drop 而非 hardcode regex 关键字.
 *   - resolved: 员工说已办完 / 已交付 / 已确认误报 / 已撤销
 *   - paused: 员工说暂时关闭 / 暂缓 / 先放放 / 等通知
 *   - pending: 球在员工手里, 继续跟进
 */
export type TaskChatStatus = "resolved" | "paused" | "pending";

/** P3.3.12: 单条 task chat 的 LLM summary cache 项. */
export interface TaskChatSummary {
  /** LLM 给的 100-150 字 summary, 说明员工跟这条 task 聊到哪. */
  summary: string;
  /** P3.5.202 (C 方案): LLM 输出的语义 status. 老 cache 无此字段 → undefined,
   *  filter 回退 regex 兜底 (backward compat). 新 summary 都会有. */
  status?: TaskChatStatus;
  /** P3.3.12 老字段 — jsonl file size (byte). 跟当前 jsonlSize 对比一致复用.
   *  P3.3.19 C Phase 5: 改 state.db 后 jsonlSize 不再用. messageCount 是新 source of truth.
   *  保留字段名做 backward compat — 老 cache 没破坏. */
  jsonlSize: number;
  /** P3.3.19 C Phase 5 (6/11): state.db session.meta.messageCount. 优先用这个判断
   *  cache 失效 (jsonl 时代去了). 没 messageCount 字段时 fallback jsonlSize. */
  messageCount?: number;
  /** ISO-8601, summary 算出来的时刻. */
  computedAt: string;
  /** P3.5.208-A (7/9 鸿波 catch 'view-side merge 不是真 SSOT'): 员工卡片按钮
   *  显式点的状态 (done/snoozed/ignored). 之前存独立 taskState.json (key=title),
   *  跟 chatStatus 各存各的, 违 SSOT. 合到这里 (key=taskUid) 作**同一份存储**,
   *  跟 status (LLM 推断) 各占一字段, 读取时 mergeTaskStatus 判 effective.
   *  老 taskState.json 保留作 legacy fallback + migration source, 双写过渡期. */
  manualStatus?: TaskStatus;
  /** ISO-8601, manualStatus 设置的时刻. */
  manualStatusTs?: string;
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

// ── P3.5.207 (7/9 鸿波 catch "早安卡片修改跟 chat 对话框修改的待办无法同步") ─
// 两套 state 各存各的 (taskState 从 Rust backend, chatStatus 从 summarizeTaskChat
// LLM 判定), 员工两边任一改都要影响另一边. 这个 helper 把两个信号合并成一个
// **有效状态**, 供 briefing filter + sidebar 徽章统一用. 规则:
//   taskState=done|ignored  → resolved (员工显式说"办完/不做")
//   taskState=snoozed        → paused   (员工显式说"推迟")
//   chatStatus=resolved/paused (LLM 从 chat 里读出的语义)
//   都无 → pending
// 冲突时 taskState 优先 (员工显式 action 比 LLM 推断强).
export type EffectiveTaskStatus = "resolved" | "paused" | "pending";

export function mergeTaskStatus(
  taskState: TaskStatus | undefined,
  chatStatus: TaskChatStatus | undefined,
): EffectiveTaskStatus {
  // taskState 显式优先
  if (taskState === "done" || taskState === "ignored") return "resolved";
  if (taskState === "snoozed") return "paused";
  // 再看 chatStatus
  if (chatStatus === "resolved") return "resolved";
  if (chatStatus === "paused") return "paused";
  return "pending";
}

// ── P3.5.208-A (7/9 鸿波): 存储层 SSOT 合并到 taskChatSummaries ──
// 之前 (P3.5.207) 只做 view-side merge, 存储仍双源. 现在 taskState 迁到
// taskChatSummaries[uid].manualStatus, 跟 chatStatus (LLM 推断) 同一存储.
// 老 taskState.json 双写保留过渡期, 后续 (P3.5.209) 删.

/** P3.5.208-A: 存储读取的**唯一**入口. 遍历 taskChatSummaries 返 uid →
 *  effective 的 map. 所有 UI / filter 都该走这个 selector, 不许 raw 读
 *  cache.taskChatSummaries[uid].manualStatus 或 chatStatusByUid.get(uid).
 *  这个 helper 保证读取语义单调. */
export function getEffectiveStatusByUid(
  cache: AdvisorCache | null,
): Map<string, EffectiveTaskStatus> {
  const m = new Map<string, EffectiveTaskStatus>();
  const sums = cache?.taskChatSummaries;
  if (!sums) return m;
  for (const [uid, s] of Object.entries(sums)) {
    const eff = mergeTaskStatus(s?.manualStatus, s?.status);
    if (eff !== "pending") m.set(uid, eff);
  }
  return m;
}

/** P3.5.208-A: 员工卡片按钮点 → 写 manualStatus 到 taskChatSummaries.
 *  内部 load-modify-save, race 概率极低 (员工点按钮频率 < 1/s + P3.4.C
 *  atomic write). null = 清除 manualStatus (撤销).
 *
 *  P3.5.208-A 过渡期兼容: 同时 call advisorTaskStateSet/Clear 双写老
 *  taskState.json, 让所有老 code path 仍能读到. P3.5.209 (后续) 彻底切换
 *  时删双写 + 迁移最后一批老数据. */
export async function setTaskManualStatus(
  taskUid: string,
  taskTitle: string,
  status: TaskStatus | null,
): Promise<void> {
  // 1) 双写老 taskState.json (兼容期). 失败不阻断新写.
  try {
    if (status === null) await advisorTaskStateClear(taskTitle);
    else await advisorTaskStateSet(taskTitle, status);
  } catch (e) {
    console.warn(
      "[P3.5.208-A setTaskManualStatus] 双写老 taskState 失败 (不阻断新写):",
      e,
    );
  }

  // 2) 主写新 taskChatSummaries[uid].manualStatus
  const cache = await advisorCacheGet();
  if (!cache) {
    console.warn(
      "[P3.5.208-A setTaskManualStatus] 无 advisorCache, manualStatus 只存到 " +
        "老 taskState. 下次 advisor refresh 后 cache 会补上.",
    );
    return;
  }
  const sums: Record<string, TaskChatSummary> = cache.taskChatSummaries ?? {};
  const entry = sums[taskUid] ?? {
    summary: "",
    jsonlSize: 0,
    computedAt: new Date().toISOString(),
  };
  if (status === null) {
    delete entry.manualStatus;
    delete entry.manualStatusTs;
  } else {
    entry.manualStatus = status;
    entry.manualStatusTs = new Date().toISOString();
  }
  sums[taskUid] = entry;
  cache.taskChatSummaries = sums;
  await advisorCacheSave(cache);
}

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
