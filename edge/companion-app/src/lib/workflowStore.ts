/**
 * 等员工确认的工作流对外步骤 (10/17, docs/WORKFLOW-SKILL-PLAN.md §5)。
 *
 * 订阅即每 15 秒问一次 tool-bridge 有哪些运行停在「对外步骤前」。新出现的发系统通知 ——
 * 定时任务跑到这一步时员工多半不在鲶鱼里。确认只能在界面上点 (WorkflowConfirmBanner), 小鲶没有这个动作。
 */
import { useSyncExternalStore } from "react";

import { sendNotification } from "./tauri_app";
import { workflowPending, type RunSummary } from "./tauri_workflows";

export const WORKFLOW_POLL_INTERVAL_MS = 15_000;

let pending: RunSummary[] = [];
let notified = new Set<string>();
const listeners = new Set<() => void>();
let timer: ReturnType<typeof setInterval> | null = null;

function emit(next: RunSummary[]) {
  pending = next;
  for (const l of listeners) l();
}

export async function refreshPending(): Promise<void> {
  let runs: RunSummary[];
  try {
    runs = (await workflowPending()).runs;
  } catch {
    return; // tool-bridge 还没起来 / 重启中: 下一轮再问, 不清掉已显示的
  }
  for (const r of runs) {
    if (!notified.has(r.run_id)) {
      notified.add(r.run_id);
      const what = r.pending?.label ?? "对外步骤";
      void sendNotification("工作流在等你确认", `${r.title}: 下一步「${what}」会对外发送, 在鲶鱼里点确认`).catch(() => {});
    }
  }
  emit(runs);
}

export function subscribe(cb: () => void): () => void {
  listeners.add(cb);
  if (!timer) {
    void refreshPending();
    timer = setInterval(() => void refreshPending(), WORKFLOW_POLL_INTERVAL_MS);
  }
  return () => {
    listeners.delete(cb);
    if (listeners.size === 0 && timer) {
      clearInterval(timer);
      timer = null;
    }
  };
}

export const getSnapshot = (): RunSummary[] => pending;

export function useWorkflowPending(): RunSummary[] {
  return useSyncExternalStore(subscribe, getSnapshot);
}

/** 确认 / 不发之后马上从列表里拿掉, 不等下一轮轮询。 */
export function dropPending(runId: string): void {
  emit(pending.filter((r) => r.run_id !== runId));
}

export function __resetWorkflowStore(): void {
  if (timer) clearInterval(timer);
  timer = null;
  listeners.clear();
  pending = [];
  notified = new Set();
}
