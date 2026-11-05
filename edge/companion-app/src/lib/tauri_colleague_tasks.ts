/** 派任务给多位同事 (11/4, docs/COLLEAGUE-TASK-PLAN.md) —— Rust commands/colleague_tasks.rs 的前端封装。规则都在 tool-bridge colleague_tasks.py。 */
import { invoke } from "@tauri-apps/api/core";

export type IncomingState =
  | "待处理" | "在做" | "已交回" | "不接" | "已取消"
  // 11/4 第二步: 定时 / 周期
  | "已安排" | "进行中" | "待核对" | "已结束" | "已退出" | "要重新同意" | "没做成" | "定时没了"
  // 11/5: 到点时有要我确认的步骤没做
  | "待确认";
export type OwnerState =
  | "未发" | "等回应" | "在做" | "已完成" | "逾期完成" | "不接" | "超时未回" | "超时未交" | "已取消"
  | "已安排" | "进行中" | "已结束" | "已退出" | "等重新同意" | "等对方确认";

/** 空 = 马上做。once: 定时做一次; cron: 周期, until / times 至少一个。 */
export type Schedule =
  | { kind: "once"; at: string }
  | { kind: "cron"; expr: string; until?: string | null; times?: number | null };

export interface IncomingTaskSummary {
  task_id: string;
  title: string;
  owner: string;
  deadline: string | null;
  state: IncomingState;
  late: boolean;
  received_at: string | null;
  reminded_at: string | null;
  /** 定时 / 周期的中文说明; 马上做的是 null */
  schedule: string | null;
  /** 待我核对的期数 */
  to_review: number;
  /** 有步骤等我在场确认的期数 */
  to_confirm?: number;
}

export interface OutgoingTaskSummary {
  task_id: string;
  title: string;
  status: "draft" | "open" | "cancelled";
  deadline: string | null;
  created_at: string | null;
  progress: Partial<Record<OwnerState, number>>;
  recipients: number;
  schedule: string | null;
}

/** 多阶段流程 (11/4 第三步) 的列表行 */
export interface PipelineSummary {
  pipeline_id: string;
  title: string;
  status: "open" | "done" | "cancelled";
  created_at: string | null;
  stages: number;
  current: number;
  current_name: string;
  progress: Partial<Record<OwnerState, number>>;
  /** 这一段都回了, 等我放行 */
  waiting_release: boolean;
}

export interface TaskList {
  incoming: IncomingTaskSummary[];
  outgoing: OutgoingTaskSummary[];
  pipelines?: PipelineSummary[];
}

export interface StageDraft {
  name?: string;
  text: string;
  recipients: string[];
  deadline?: string | null;
}

export interface PipelineStage {
  index: number;
  name: string;
  text: string;
  recipients: string[];
  deadline: string | null;
  task_id: string | null;
  input: string | null;
  started_at: string | null;
  released_at: string | null;
  finished: boolean;
  progress: { rows: OwnerRow[]; counts: Partial<Record<OwnerState, number>>; total: number } | null;
}

export interface PipelineDetail {
  pipeline: { pipeline_id: string; title: string; owner: string; status: PipelineSummary["status"]; current: number; stages: PipelineStage[] };
  /** 默认交给下一段的内容 (这一段每人交回的结果) */
  handoff: string;
}

export interface TaskBody {
  title: string;
  text: string;
  deadline: string | null;
  schedule?: Schedule | null;
  /** 多阶段: 发起人放行的上一段结果 */
  context?: string | null;
}

/** 收件人这边的一期 */
export interface MyPeriod {
  period: number;
  result: string;
  at: string;
  late_minutes: number;
  state: "待确认" | "待核对" | "已交回";
  sent_at?: string;
  /** 到点没人确认、这次没做的步骤 */
  skipped?: string[];
}

/** 发起人收到的一期 */
export interface OwnerPeriod {
  period: number;
  result?: string;
  at?: string;
  late_minutes?: number;
  /** 对方那边还差几步等他确认 (交回后清掉) */
  waiting?: number;
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
  // 定时 / 周期
  rev?: number;
  job_id?: string;
  auto_return?: boolean;
  periods?: MyPeriod[];
  quit?: { reason: string; at: string };
  changed_at?: string;
  missed?: boolean;
  job_lost?: boolean;
  ended_at?: string;
  /** 发起人请我重做时写的要求 */
  redo_note?: string;
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
  periods: OwnerPeriod[];
}

export interface OutgoingTask extends TaskBody {
  task_id: string;
  owner: string;
  status: "draft" | "open" | "cancelled";
  created_at: string;
  rev?: number;
  recipients: Record<string, { invited_at: string | null; reminded_at: string | null }>;
}

export type TaskDetail =
  | { role: "owner"; task: OutgoingTask; schedule_text: string | null;
      progress: { rows: OwnerRow[]; counts: Partial<Record<OwnerState, number>>; total: number } }
  | { role: "recipient"; record: IncomingTask; state: IncomingState; schedule_text?: string; next_run?: string | null };

export interface TaskEvent {
  kind: "invite" | "changed" | "remind" | "cancel" | "accepted" | "done" | "declined" | "period" | "quit"
    | "review" | "missed" | "job_lost" | "stage_done" | "confirm_needed" | "waiting";
  task_id: string;
  from: string;
  title: string;
  period?: number | null;
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
/** room_id: 在群里派的 (GROUP-CHAT-PLAN 第二步), 发出后这条任务进群, 收件人的状态也回群; room_text = 群里显示的原话 */
export const taskCreate = (p: { title?: string; text: string; deadline?: string | null; schedule?: Schedule | null; recipients: string[]; room_id?: string; room_text?: string }) =>
  call<{ task: OutgoingTask }>("create", p);
export const taskAdd = (taskId: string, recipients: string[]) => call<{ task: OutgoingTask }>("add", { task_id: taskId, recipients });
/** room_notice: 群里派的任务发出去了, 但任务卡没进群的原因 */
export const taskSend = (taskId: string) => call<Sent & { sent: string[]; room_notice?: string }>("send", { task_id: taskId });
export const taskRemind = (taskId: string, text = "") => call<Sent & { reminded: string[] }>("remind", { task_id: taskId, text });
export const taskCancel = (taskId: string) => call<Sent>("cancel", { task_id: taskId });
export const taskAccept = (taskId: string, autoReturn = false) =>
  call<{ accepted_at: string; next_run?: string | null }>("accept", { task_id: taskId, auto_return: autoReturn });
export const taskUpdate = (taskId: string, p: { text?: string; deadline?: string | null; schedule?: Schedule | null }) =>
  call<Sent & { sent: string[] }>("update", { task_id: taskId, ...p });
export const taskPeriodSubmit = (taskId: string, period: number, result: string) =>
  call<{ period: number }>("period_submit", { task_id: taskId, period, result });
export const taskRedo = (taskId: string, email: string, note: string) => call<{ sent: string[] }>("redo", { task_id: taskId, email, note });
export const pipelineCreate = (p: { title: string; stages: StageDraft[]; room_id?: string; room_text?: string }) =>
  call<{ pipeline: { pipeline_id: string }; sent: string[]; failed: { to: string; error: string }[] }>("pipeline_create", p);
export const pipelineGet = (pipelineId: string) => call<PipelineDetail>("pipeline_get", { pipeline_id: pipelineId });
export const pipelineRelease = (pipelineId: string, text: string) =>
  call<{ done: boolean; stage?: number; sent?: string[]; failed?: { to: string; error: string }[]; cancelled: string[] }>(
    "pipeline_release", { pipeline_id: pipelineId, text });
export const pipelineCancel = (pipelineId: string) => call<{ ok: boolean }>("pipeline_cancel", { pipeline_id: pipelineId });
export const taskQuit = (taskId: string, reason: string) => call<{ ok: boolean }>("quit", { task_id: taskId, reason });
export const taskDraft = (taskId: string, result: string) => call<{ chars: number }>("draft", { task_id: taskId, result });
export const taskSubmit = (taskId: string, result: string) =>
  call<{ done_at: string; late: boolean }>("submit", { task_id: taskId, result });
export const taskDecline = (taskId: string, reason: string) => call<{ ok: boolean }>("decline", { task_id: taskId, reason });
