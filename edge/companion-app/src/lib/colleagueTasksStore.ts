/**
 * 派任务给同事的前端状态 (11/4, docs/COLLEAGUE-TASK-PLAN.md)。跟 formsStore 同一套:
 * 订阅即开始轮询, 每 20 秒 `sync` 取一次邮筒 (中央取走即清空, 落进本机账本), 再 `list` 刷列表。
 * 导航常驻订阅 (TabBar), 员工不进「协同」页也能看到红点、收到系统通知。
 */
import { useSyncExternalStore } from "react";

import { useRemindersStore } from "../store/reminders";
import { colleagueDueSoonReminder, colleagueOverdueReminder, type Reminder } from "./reminders";
import { sendNotification } from "./tauri_app";
import { taskList, taskSync, type TaskEvent, type TaskList, type TaskSyncResult } from "./tauri_colleague_tasks";

export const TASKS_POLL_INTERVAL_MS = 20_000;

export interface TasksState extends TaskList {
  loaded: boolean;
  /** 最近一次取邮筒失败的原因 (没登录 / 中央连不上 / 中央太旧); 成功一次就清掉 */
  syncError: string | null;
}

let state: TasksState = { incoming: [], outgoing: [], pipelines: [], loaded: false, syncError: null };
const listeners = new Set<() => void>();
let timer: ReturnType<typeof setInterval> | null = null;
let inflight: Promise<void> | null = null;

function emit(next: TasksState) {
  state = next;
  for (const l of listeners) l();
}

const short = (iso?: string | null) => (iso ? `${+iso.slice(5, 7)}/${+iso.slice(8, 10)}${iso.length > 10 ? ` ${iso.slice(11, 16)}` : ""}` : "");
const who = (people?: string[]) => {
  const n = (people ?? []).map((e) => e.split("@")[0]);
  return n.length > 3 ? `${n.slice(0, 3).join("、")} 等 ${n.length} 人` : n.join("、");
};

const NOTE: Record<string, (from: string, title: string, period?: number | null, ev?: TaskEvent) => [string, string] | null> = {
  invite: (f, t) => ["同事请你帮忙", `${f} 派给你一个任务「${t}」, 在「协同」页里看`],
  remind: (f, t) => ["有任务在催你", `${f} 催你交回「${t}」`],
  cancel: (f, t) => ["任务取消了", `${f} 取消了「${t}」, 不用做了`],
  done: (f, t) => ["同事交回了结果", `${f} 交回了「${t}」`],
  declined: (f, t) => ["同事没接任务", `${f} 没接「${t}」, 在「协同」页看原因`],
  accepted: () => null,   // 接下了不打扰, 进度表上看得到
  // 11/4 第二步: 定时 / 周期
  changed: (f, t) => ["任务改了", `${f} 改了「${t}」, 要你重新看一下接不接`],
  period: (f, t, n) => ["同事交回了一次结果", `${f} 交回了「${t}」第 ${n ?? "?"} 次`],
  quit: (f, t) => ["同事退出了任务", `${f} 退出了「${t}」, 在「协同」页看原因`],
  confirm_needed: (_f, t, n) => ["有步骤等你确认", `「${t}」第 ${n ?? "?"} 次有要你确认的步骤没做, 在「协同」页点「我在了, 接着做」`],
  waiting: () => null,   // 发起人那边: 进度表上显示「等对方确认」, 不另外打扰
  review: (_f, t, n) => ["小鲶做完了一次, 等你核对", `「${t}」第 ${n ?? "?"} 次做好了, 核对后点交回`],
  missed: (_f, t) => ["定时任务没做成", `「${t}」到点电脑没开, 可以在「协同」页让小鲶现在做一次`],
  job_lost: (_f, t) => ["任务的定时被删了", `「${t}」的定时没了, 在「协同」页重新安排或退出`],
  // 11/4 第三步: 多阶段
  stage_done: (_f, t, n) => ["这一段都回了", `「${t}」第 ${n ?? "?"} 段都回了, 核对后放行到下一段`],
  // 11/5: 接下就开工
  drafted: (f, t) => ["小鲶做好了, 等你核对", `${f} 请你做的「${t}」小鲶做好了, 看一下再点交回`],
  start_failed: (_f, t) => ["小鲶没做出来", `「${t}」小鲶在后台没做出来, 在「协同」页点「让小鲶做」在对话里做, 或者自己做`],
  // 11/07: 截止前提醒 / 逾期提醒 / 延期 (提醒条上也挂一条, 见 remindFor)
  due_soon: (f, t, _n, ev) => ["任务快到截止了", `${f} 派的「${t}」${short(ev?.deadline)} 截止, 还没交回`],
  overdue: (_f, t, _n, ev) => ["有同事没按时交", `「${t}」过了截止, ${who(ev?.people)} 还没交`],
  extended: (f, t, _n, ev) => ["任务延期了", `${f} 把「${t}」的截止改到 ${short(ev?.deadline)}`],
};

/** 11/07 逾期 / 快到截止: 输入框上方的提醒条也挂一条 (系统通知点掉就没了, 提醒条上能直接催 / 延期 / 去交回) */
export function remindFor(r: TaskSyncResult, now: Date = new Date()): void {
  for (const ev of r.events) {
    const rem = ev.kind === "overdue" ? colleagueOverdueReminder(ev, now)
      : ev.kind === "due_soon" ? colleagueDueSoonReminder(ev, now) : null;
    if (rem) useRemindersStore.getState().add(rem, now.getTime());
  }
}

/** 事情自己了结了的同事任务提醒收掉: 都交了 / 延期了 / 取消了 (发起人); 交回了 / 不接了 / 截止改了 (收件人) */
export function settledReminder(rem: Reminder, list: TaskList): boolean {
  const c = rem.ctask;
  if (!c) return false;
  if (c.role === "owner") {
    const t = list.outgoing.find((x) => x.task_id === c.taskId);
    if (!t) return false;   // 多阶段里的一段不在这个列表里: 不动, 三天后自己过期
    const late = (t.progress["超时未交"] ?? 0) + (t.progress["超时未回"] ?? 0);
    return t.status !== "open" || t.deadline !== c.deadline || late === 0;
  }
  const t = list.incoming.find((x) => x.task_id === c.taskId);
  return !t || t.deadline !== c.deadline || !["待处理", "在做", "待核对"].includes(t.state);
}

export function notifyFor(r: TaskSyncResult): void {
  for (const ev of r.events) {
    const n = NOTE[ev.kind]?.(ev.from, ev.title, ev.period, ev);
    if (n) void sendNotification(n[0], n[1]).catch(() => {});
  }
}

/** 取一次邮筒 + 刷列表。并发调用合并成一次。 */
export function refreshTasks(): Promise<void> {
  if (inflight) return inflight;
  inflight = (async () => {
    let syncError: string | null = null;
    try {
      const synced = await taskSync();
      notifyFor(synced);
      remindFor(synced);
    } catch (e) {
      syncError = e instanceof Error ? e.message : String(e);
    }
    try {
      const list = await taskList();
      useRemindersStore.getState().drop((rem) => settledReminder(rem, list));
      emit({ ...list, loaded: true, syncError });
    } catch (e) {
      emit({ ...state, loaded: true, syncError: syncError ?? (e instanceof Error ? e.message : String(e)) });
    }
  })().finally(() => {
    inflight = null;
  });
  return inflight;
}

export function subscribe(cb: () => void): () => void {
  listeners.add(cb);
  if (!timer) {
    void refreshTasks();
    timer = setInterval(() => void refreshTasks(), TASKS_POLL_INTERVAL_MS);
  }
  return () => {
    listeners.delete(cb);
    if (listeners.size === 0 && timer) {
      clearInterval(timer);
      timer = null;
    }
  };
}

export const getSnapshot = (): TasksState => state;

export function useColleagueTasks(): TasksState {
  return useSyncExternalStore(subscribe, getSnapshot);
}

/** 导航红点: 同事派给我、还要我动手的。 */
const NEEDS_ME = new Set(["待处理", "在做", "待核对", "待确认", "要重新同意", "没做成", "定时没了"]);
export function pendingTaskCount(s: TasksState = state): number {
  // 多阶段: 这一段都回了、等我放行的也算
  return s.incoming.filter((t) => NEEDS_ME.has(t.state)).length + (s.pipelines ?? []).filter((p) => p.waiting_release).length;
}

/** 测试用: 回到初始状态。 */
export function __resetTasksStore(): void {
  if (timer) clearInterval(timer);
  timer = null;
  inflight = null;
  listeners.clear();
  state = { incoming: [], outgoing: [], pipelines: [], loaded: false, syncError: null };
}
