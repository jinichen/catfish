/** BL-ADVISOR-CACHE + CONFIG (5/22 Phase 7 cold start v3): TS wrapper.
 *
 * 5/22 鸿波: 每次切早安 tab 重算浪费 token. 改 = cache + 时段触发.
 *
 * 时段从 ~/.catfish/companion.yaml advisor.refresh_times 读 (yaml 改重启 Companion 生效).
 * 默认: ["08:30", "11:30", "14:00", "16:30"], cache_max_age_minutes: 90.
 */

import { invoke as rawInvoke } from "@tauri-apps/api/core";
import type { AdvisorResult } from "./briefing_advisor";
import type { AdvisorInput } from "./briefing_advisor_common";

/** 缓存格式版本。旧缓存没有来源指纹，不能再作为当前任务依据。 */
export const ADVISOR_CACHE_SCHEMA_VERSION = 2;
/** 提示词/来源边界变更时递增，避免旧模型输出跨规则复用。 */
export const ADVISOR_CACHE_INPUT_VERSION = "current-week-sources-v2";

export interface AdvisorCacheSourceMeta {
  schemaVersion: number;
  inputVersion: string;
  inputFingerprint: string;
  windowStart: string;
  windowEnd: string;
  sourceCounts: {
    emails: number;
    events: number;
    todos: number;
  };
}

export interface AdvisorCache {
  computedAt: string;          // ISO-8601
  result: AdvisorResult;
  model?: string;
  promptTokens?: number;
  /** 当前输入快照。没有这些字段的旧 cache 只允许被清理，不允许复用。 */
  sourceMeta?: AdvisorCacheSourceMeta;
  /** P3.3.12 (6/10): task chat summary cache, key=taskUid.
   *  jsonl size 没变就复用 — 没有新消息进 chat, summary 还是有效的. */
  taskChatSummaries?: Record<string, TaskChatSummary>;
}

// ── BL-ADVISOR-TASK-STATE (5/22 鸿波): done/snoozed/ignored ─────

export type TaskStatus = "done" | "ignored" | "snoozed";

/** P3.5.202 (C 方案 7/9 鸿波): LLM 判定员工在这条 task 的最新状态.
 *  用于 filterResolvedTasks 语义判 drop 而非 hardcode regex 关键字.
 *   - resolved: 员工说已办完 / 已交付 / 已确认误报 / 已撤销
 *   - paused: 员工说暂时关闭 / 暂缓 / 先放放 / 等通知
 *   - pending: 球在员工手里, 继续跟进
 */
export type TaskChatStatus = "resolved" | "paused" | "pending";

/** 员工显式重新打开任务时的手工状态。
 *
 * 仅用于 advisor cache，不是旧 advisor_task_state 后端命令支持的状态。
 * 它让“撤销完成”可以覆盖仍然保留在聊天摘要里的 resolved 判断。
 */
export type ManualTaskStatus = TaskStatus | "pending";

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
  manualStatus?: ManualTaskStatus;
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

function normalizeForFingerprint(value: unknown): string {
  return String(value ?? "").trim().normalize("NFKC").toLowerCase();
}

function naturalWeekWindow(now = new Date()): { start: string; end: string } {
  const start = new Date(now);
  const day = start.getDay();
  const daysFromMonday = day === 0 ? 6 : day - 1;
  start.setDate(start.getDate() - daysFromMonday);
  start.setHours(0, 0, 0, 0);
  const end = new Date(start);
  end.setDate(end.getDate() + 7);
  return { start: start.toISOString(), end: end.toISOString() };
}

function hashFingerprint(text: string): string {
  // FNV-1a: 足够用于本地缓存失效判断，不承担密码学用途。
  let hash = 2166136261;
  for (let i = 0; i < text.length; i += 1) {
    hash ^= text.charCodeAt(i);
    hash = Math.imul(hash, 16777619);
  }
  return (hash >>> 0).toString(16).padStart(8, "0");
}

/** 构造与数组顺序无关的当前输入指纹。 */
export function advisorCacheSourceMeta(input: AdvisorInput, now = new Date()): AdvisorCacheSourceMeta {
  const window = naturalWeekWindow(now);
  const emails = input.emails
    .map((email) => [email.id, email.date, email.subject, email.sender, email.is_read])
    .sort((a, b) => JSON.stringify(a).localeCompare(JSON.stringify(b)));
  const events = input.events
    .map((event) => [event.start, event.end, event.summary, event.calendar, event.location ?? ""])
    .sort((a, b) => JSON.stringify(a).localeCompare(JSON.stringify(b)));
  const todos = input.todos
    .map((todo) => [todo.reminder_id ?? "", todo.due_date_iso ?? "", todo.text, todo.section, todo.priority ?? 0])
    .sort((a, b) => JSON.stringify(a).localeCompare(JSON.stringify(b)));
  const activeContext = {
    workplan: normalizeForFingerprint(input.ctx.workplan),
    projects: normalizeForFingerprint(input.ctx.projects),
    wikiRelevant: normalizeForFingerprint(input.wikiRelevant),
  };
  const payload = JSON.stringify({
    version: ADVISOR_CACHE_INPUT_VERSION,
    model: normalizeForFingerprint(input.model),
    window,
    emails,
    events,
    todos,
    activeContext,
  });
  return {
    schemaVersion: ADVISOR_CACHE_SCHEMA_VERSION,
    inputVersion: ADVISOR_CACHE_INPUT_VERSION,
    inputFingerprint: hashFingerprint(payload),
    windowStart: window.start,
    windowEnd: window.end,
    sourceCounts: {
      emails: input.emails.length,
      events: input.events.length,
      todos: input.todos.length,
    },
  };
}

/** 缓存是否对应当前数据快照；旧 cache 明确判无效。 */
export function advisorCacheMatchesInput(
  cache: AdvisorCache | null,
  input: AdvisorInput,
): boolean {
  if (!cache?.sourceMeta) return false;
  const expected = advisorCacheSourceMeta(input);
  return cache.sourceMeta.schemaVersion === expected.schemaVersion
    && cache.sourceMeta.inputVersion === expected.inputVersion
    && cache.sourceMeta.inputFingerprint === expected.inputFingerprint
    && cache.sourceMeta.windowStart === expected.windowStart
    && cache.sourceMeta.windowEnd === expected.windowEnd;
}

export const advisorConfigGet = () =>
  rawInvoke<AdvisorConfig>("advisor_config_get");

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
  taskState: ManualTaskStatus | undefined,
  chatStatus: TaskChatStatus | undefined,
): EffectiveTaskStatus {
  // taskState 显式优先
  if (taskState === "pending") return "pending";
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

/** P39 (5/22 P3.5.208-A 精细化 selector): 返 uid → raw manualStatus (TaskStatus)
 *  精确到 "done" vs "ignored" vs "snoozed", 不合并 chatStatus.
 *
 *  用于 AdvisorView filter — 只想藏 ignored, 保留 done/snoozed 让 sidebar 打
 *  勾/⏰. getEffectiveStatusByUid 因为把 done + ignored 都返 "resolved" 无法
 *  区分, 所以引入这个精细 selector.
 *
 *  不带 chatStatus 合并 (chat 里判"resolved" 不等同于员工"不做" ignored). */
export function getManualStatusByUid(
  cache: AdvisorCache | null,
): Map<string, TaskStatus> {
  const m = new Map<string, TaskStatus>();
  const sums = cache?.taskChatSummaries;
  if (!sums) return m;
  for (const [uid, s] of Object.entries(sums)) {
    if (s?.manualStatus && s.manualStatus !== "pending") {
      m.set(uid, s.manualStatus);
    }
  }
  return m;
}

/** P3.5.208-A: 员工卡片按钮点 → 写 manualStatus 到 taskChatSummaries.
 *  内部 load-modify-save, race 概率极低 (员工点按钮频率 < 1/s + P3.4.C
 *  atomic write). null = 员工显式重新打开，写入 pending 覆盖 chatStatus.
 *
 *  P39 (5/22 SSOT 收敛): 新状态保存在 advisor cache；旧 taskState 仅在撤销
 *    时清理残留，避免历史迁移数据重新覆盖“重新打开”状态。
 */
export async function setTaskManualStatus(
  taskUid: string,
  taskTitle: string,
  status: TaskStatus | null,
): Promise<void> {
  // 新状态写入 cache SSOT。旧 taskState 只在撤销时清理残留，防止迁移/旧
  // filter 把已经撤销的 done 又搬回来。
  if (status === null) {
    await advisorTaskStateClear(taskTitle).catch((e) => {
      console.warn("[P3.5.208-A setTaskManualStatus] 清理旧 taskState 失败:", e);
    });
  }

  // 主写 taskChatSummaries[uid].manualStatus (SSOT)
  const cache = await advisorCacheGet();
  if (!cache) {
    console.warn(
      "[P3.5.208-A setTaskManualStatus] 无 advisorCache, 无法保存手工状态.",
    );
    throw new Error("advisorCache 不存在，无法保存手工状态");
  }
  const sums: Record<string, TaskChatSummary> = cache.taskChatSummaries ?? {};
  const entry = sums[taskUid] ?? {
    summary: "",
    jsonlSize: 0,
    computedAt: new Date().toISOString(),
  };
  if (status === null) {
    // 不能简单 delete：chat summary 仍可能是 resolved，删除后撤销按钮
    // 看起来无效。pending 是员工对 chat 判定的明确覆盖。
    entry.manualStatus = "pending";
    entry.manualStatusTs = new Date().toISOString();
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
