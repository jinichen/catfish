/** 派任务给多位同事 (11/4, docs/COLLEAGUE-TASK-PLAN.md) —— Rust commands/colleague_tasks.rs 的前端封装。规则都在 tool-bridge colleague_tasks.py。 */
import { invoke } from "@tauri-apps/api/core";

export type IncomingState = "待处理" | "在做" | "已交回" | "不接" | "已取消";
export type OwnerState = "未发" | "等回应" | "在做" | "已完成" | "逾期完成" | "不接" | "超时未回" | "超时未交" | "已取消";

export interface IncomingTaskSummary {
  task_id: string;
  title: string;
  owner: string;
  deadline: string | null;
  state: IncomingState;
  late: boolean;
  received_at: string | null;
  reminded_at: string | null;
}

export interface OutgoingTaskSummary {
  task_id: string;
  title: string;
  status: "draft" | "open" | "cancelled";
  deadline: string | null;
  created_at: string | null;
  progress: Partial<Record<OwnerState, number>>;
  recipients: number;
}

export interface TaskList {
  incoming: IncomingTaskSummary[];
  outgoing: OutgoingTaskSummary[];
}

export interface TaskBody {
  title: string;
  text: string;
  deadline: string | null;
}

export interface IncomingTask {
  task_id: string;
  owner: string;
  status: "open" | "cancelled";
  received_at: string;
  task: TaskBody;
  accepted_at?: string;
  reminded_at?: string;
  remind_text?: string;
  draft?: { result: string; updated_at: string };
  done?: { result: string; at: string };
  declined?: { reason: string; at: string };
}

export interface OwnerRow {
  email: string;
  state: OwnerState;
  invited_at: string | null;
  reminded_at: string | null;
  accepted_at: string | null;
  done_at: string | null;
  result: string | null;
  reason: string | null;
}

export interface OutgoingTask extends TaskBody {
  task_id: string;
  owner: string;
  status: "draft" | "open" | "cancelled";
  created_at: string;
  recipients: Record<string, { invited_at: string | null; reminded_at: string | null }>;
}

export type TaskDetail =
  | { role: "owner"; task: OutgoingTask; progress: { rows: OwnerRow[]; counts: Partial<Record<OwnerState, number>>; total: number } }
  | { role: "recipient"; record: IncomingTask; state: IncomingState };

export interface TaskEvent {
  kind: "invite" | "remind" | "cancel" | "accepted" | "done" | "declined";
  task_id: string;
  from: string;
  title: string;
}

export interface TaskSyncResult {
  events: TaskEvent[];
}

type Sent = { ok: boolean; failed: { to: string; error: string }[] };

const call = <T>(action: string, params: Record<string, unknown> = {}) =>
  invoke<T>("colleague_task_call", { action, params });

export const taskList = () => call<TaskList>("list");
export const taskGet = (taskId: string) => call<TaskDetail>("get", { task_id: taskId });
export const taskSync = () => call<TaskSyncResult>("sync");
export const taskCreate = (p: { title?: string; text: string; deadline?: string | null; recipients: string[] }) =>
  call<{ task: OutgoingTask }>("create", p);
export const taskAdd = (taskId: string, recipients: string[]) => call<{ task: OutgoingTask }>("add", { task_id: taskId, recipients });
export const taskSend = (taskId: string) => call<Sent & { sent: string[] }>("send", { task_id: taskId });
export const taskRemind = (taskId: string, text = "") => call<Sent & { reminded: string[] }>("remind", { task_id: taskId, text });
export const taskCancel = (taskId: string) => call<Sent>("cancel", { task_id: taskId });
export const taskAccept = (taskId: string) => call<{ accepted_at: string }>("accept", { task_id: taskId });
export const taskDraft = (taskId: string, result: string) => call<{ chars: number }>("draft", { task_id: taskId, result });
export const taskSubmit = (taskId: string, result: string) =>
  call<{ done_at: string; late: boolean }>("submit", { task_id: taskId, result });
export const taskDecline = (taskId: string, reason: string) => call<{ ok: boolean }>("decline", { task_id: taskId, reason });
