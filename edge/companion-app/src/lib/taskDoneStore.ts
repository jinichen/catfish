/**
 * 后台任务「做完了 / 没做成, 你还没看」的未读计数 (9/12)。
 *
 * 桌宠 (BL-E27) 删掉之后, 这个信号的出口从"桌宠头顶变色"换成两处现成的东西:
 *   - 左侧导航「工作台」项上的红点 (跟「协同」的红点同一套 CSS)
 *   - Dock 图标角标 (Tauri `setBadgeCount`)
 *
 * 数据源: tool-bridge `catfish_task_list` (task_manager 内存表, 24h TTL)。
 * 「已看」的定义: 员工**在工作台 tab 且窗口有焦点** —— 这时任务结果就在他眼前的
 * 对话里。TabBar 每次满足这个条件就调 markSeen(), seen_ts 落 localStorage,
 * 重启 Companion 不会把昨天看过的又标回未读。
 *
 * 跟 roomLinkStore 同一个单例 + useSyncExternalStore 模式: 有订阅者才轮询,
 * 最后一个订阅者走了就停。tool-bridge 没起 / 调用失败 → 计数保持上一次, 不抛。
 */
import { useSyncExternalStore } from "react";

import { toolBridgeCallTool } from "./tauri";

export const TASK_DONE_POLL_INTERVAL_MS = 15_000;
const SEEN_TS_KEY = "catfish:task_done_seen_ts";

export interface DoneTask {
  task_id: string;
  kind: string;
  label: string;
  status: "completed" | "failed";
  finished_at: number;
}

export interface TaskDoneState {
  /** 已结束、非测试、finished_at 晚于 seen_ts 的任务, 新的在前。 */
  unseen: DoneTask[];
}

const EMPTY: TaskDoneState = { unseen: [] };
let state: TaskDoneState = EMPTY;
const listeners = new Set<() => void>();
let timer: ReturnType<typeof setInterval> | null = null;
/** 最近一次拉到的全部已结束任务 (markSeen 后重算 unseen 用, 不用再拉一次)。 */
let lastFinished: DoneTask[] = [];

function emit(next: TaskDoneState) {
  state = next;
  for (const l of listeners) l();
}

function readSeenTs(): number {
  try {
    const raw = localStorage.getItem(SEEN_TS_KEY);
    const n = raw ? Number(raw) : 0;
    return Number.isFinite(n) ? n : 0;
  } catch {
    return 0;
  }
}

function writeSeenTs(ts: number) {
  try {
    localStorage.setItem(SEEN_TS_KEY, String(ts));
  } catch {
    /* localStorage 不可用 (测试 / 隐私模式) → 本次进程内仍生效, 见 seenTsMem */
  }
  seenTsMem = ts;
}

let seenTsMem = 0;
function seenTs(): number {
  return Math.max(seenTsMem, readSeenTs());
}

/** 跟 tool-bridge task_manager_notify._is_test_task 同一套规则 —— 测试任务不打扰。 */
export function isTestTask(kind: string, label: string): boolean {
  const k = kind.toLowerCase();
  const l = label.toLowerCase();
  return (
    k.includes("_test") ||
    k.includes("test_") ||
    k === "test" ||
    label.includes("测试") ||
    l.startsWith("test ")
  );
}

/** 把 catfish_task_list 的原始返回收成 DoneTask[] (只留已结束且非测试)。 */
export function pickFinished(raw: unknown): DoneTask[] {
  const tasks = (raw as { tasks?: unknown[] } | null)?.tasks;
  if (!Array.isArray(tasks)) return [];
  const out: DoneTask[] = [];
  for (const t of tasks) {
    const o = t as Record<string, unknown>;
    const status = o.status;
    if (status !== "completed" && status !== "failed") continue;
    if (typeof o.finished_at !== "number" || typeof o.task_id !== "string") continue;
    const kind = typeof o.kind === "string" ? o.kind : "";
    const label = typeof o.label === "string" ? o.label : "";
    if (isTestTask(kind, label)) continue;
    out.push({ task_id: o.task_id, kind, label, status, finished_at: o.finished_at });
  }
  out.sort((a, b) => b.finished_at - a.finished_at);
  return out;
}

function recompute() {
  const since = seenTs();
  const unseen = lastFinished.filter((t) => t.finished_at > since);
  // 内容没变就不 emit, 免得 TabBar 每 15s 重渲染一次
  if (
    unseen.length === state.unseen.length &&
    unseen.every((t, i) => t.task_id === state.unseen[i].task_id)
  ) {
    return;
  }
  emit({ unseen });
}

async function tick() {
  try {
    const r = await toolBridgeCallTool("catfish_task_list", {});
    if (!r.ok) return;
    lastFinished = pickFinished(r.result);
    recompute();
  } catch {
    /* tool-bridge 没起 / RPC 超时: 保持上一次的计数 */
  }
}

export function subscribe(cb: () => void): () => void {
  listeners.add(cb);
  if (!timer) {
    timer = setInterval(() => void tick(), TASK_DONE_POLL_INTERVAL_MS);
    void tick();
  }
  return () => {
    listeners.delete(cb);
    if (listeners.size === 0 && timer) {
      clearInterval(timer);
      timer = null;
    }
  };
}

export const getSnapshot = (): TaskDoneState => state;

export function useTaskDone(): TaskDoneState {
  return useSyncExternalStore(subscribe, getSnapshot);
}

export function unseenCount(s: TaskDoneState = state): number {
  return s.unseen.length;
}

/** 员工正看着工作台 → 到此刻为止的都算看过。幂等, 没未读时不写 localStorage。 */
export function markSeen(now: number = Date.now() / 1000): void {
  if (state.unseen.length === 0) return;
  writeSeenTs(now);
  recompute();
}

/** 测试用: 清空。 */
export function _resetForTest(): void {
  if (timer) clearInterval(timer);
  timer = null;
  listeners.clear();
  lastFinished = [];
  seenTsMem = 0;
  state = EMPTY;
}
