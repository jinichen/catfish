/** 工作流 skill (10/17, docs/WORKFLOW-SKILL-PLAN.md) —— Rust commands/workflows.rs 的前端封装。规则都在 tool-bridge。 */
import { invoke } from "@tauri-apps/api/core";

export type Level = "read" | "local" | "outward";
export type ReturnType = "text" | "number" | "list" | "bool" | "date";

export interface CatalogAction {
  name: string;
  label: string;
  level: Level;
  group: string;
  params: string[];
  required: string[];
  outputs: string[];
}

export interface CatalogSkill {
  skill_path: string;
  description: string;
  version: string;
  params: { name: string; required: boolean; default: unknown }[];
}

export interface WorkflowStep {
  id: string;
  skill?: string;
  action?: string;
  xiaonian?: string;
  params?: Record<string, unknown>;
  input?: string;
  returns?: Record<string, ReturnType>;
  for_each?: string;
  expect?: Record<string, unknown>;
  confirm?: boolean;
}

export interface WorkflowParam {
  type?: "text" | "number" | "date" | "bool" | "list";
  default?: unknown;
  required?: boolean;
  label?: string;
}

export interface Workflow {
  kind: "workflow";
  version?: string;
  params?: Record<string, WorkflowParam>;
  steps: WorkflowStep[];
}

/** 步骤链上的一环 (10/19): 中文名 + 会不会停下等确认 */
export interface ChainStep {
  id: string;
  label: string;
  block: "skill" | "action" | "xiaonian" | "";
  outward: boolean;
  each: boolean;
}

export interface ParamSpec {
  key: string;
  label: string;
  type: string;
  default: unknown;
  required: boolean;
}

export interface WorkflowSummary {
  skill_path: string;
  /** 给人看的中文名 / 一句话说明 (10/19) */
  title: string;
  summary: string;
  chain: ChainStep[];
  params: ParamSpec[];
  description: string;
  version: string;
  steps: number;
  auto_confirm_outward: boolean;
  has_outward: boolean;
}

export interface DraftSummary {
  name: string;
  title?: string;
  namespace: string;
  description: string;
  saved_at: string;
  steps: number;
}

export interface Pending {
  index: number;
  step: string;
  label: string;
  preview: unknown;
  for_each_count: number | null;
}

export type RunStatus = "running" | "done" | "failed" | "waiting_confirm" | "declined" | "invalid";

export interface RunSummary {
  run_id: string;
  skill_path: string;
  title: string;
  status: RunStatus;
  created_at: string;
  updated_at: string;
  failed_step: string | null;
  failed_index?: number | null;
  /** 原始报错 (给 IT) / 员工看得懂的说法 / 怎么办 (10/19) */
  error: string | null;
  error_human?: string | null;
  error_fix?: "conditions" | "workflow" | "retry" | "login" | null;
  /** 跑完了做成了什么 */
  outcome?: string | null;
  /** trial 试跑 / ui 界面上点的 / chat 小鲶跑的 (对话里或定时) */
  trigger?: "trial" | "ui" | "chat" | null;
  pending: Pending | null;
}

export interface RunResult {
  ok: boolean;
  run_id?: string;
  status: RunStatus;
  summary?: string;
  error?: string;
  problems?: string[];
  pending?: Pending;
  files?: string[];
}

export interface RunDetail extends RunSummary {
  params: Record<string, unknown>;
  steps: { id: string; block?: string; status: string; error?: string; ms?: number; result?: unknown }[];
}

const call = <T>(action: string, params: Record<string, unknown> = {}) => invoke<T>("workflow_call", { action, params });

export const workflowCatalog = () => call<{ actions: CatalogAction[]; skills: CatalogSkill[] }>("catalog");
export const workflowList = () => call<{ workflows: WorkflowSummary[]; drafts: DraftSummary[] }>("list");
export const workflowGet = (skillPath: string) =>
  call<{ skill_path: string; description: string; triggers: string[]; workflow: Workflow; auto_confirm_outward: boolean }>(
    "get", { skill_path: skillPath });
export const workflowUpdate = (skillPath: string, workflow: Workflow) =>
  call<{ ok: boolean; problems?: string[] }>("update", { skill_path: skillPath, workflow });
export const workflowDraftGet = (name: string) =>
  call<{ name: string; namespace: string; description: string; triggers: string[]; workflow: Workflow }>("draft_get", { name });
export const workflowDraftSave = (d: { name: string; namespace: string; description: string; triggers: string[]; workflow: Workflow }) =>
  call<{ ok: boolean; problems: string[] }>("draft_save", d);
export const workflowDraftDelete = (name: string) => call<{ ok: boolean }>("draft_delete", { name });
export const workflowTrial = (name: string, params: Record<string, unknown> = {}) => call<RunResult>("trial", { name, params });
export const workflowSave = (name: string) => call<{ ok: boolean; skill_path?: string; error?: string; problems?: string[] }>("save", { name });
export const workflowRun = (skillPath: string, params: Record<string, unknown> = {}) =>
  call<RunResult>("run", { skill_path: skillPath, params });
export const workflowRuns = (limit = 30) => call<{ runs: RunSummary[] }>("runs", { limit });
export const workflowRunGet = (runId: string) => call<RunDetail>("run_get", { run_id: runId });
export const workflowPending = () => call<{ runs: RunSummary[] }>("pending");
export const workflowConfirm = (runId: string, approve: boolean) => call<RunResult>("confirm", { run_id: runId, approve });
export const workflowResume = (runId: string) => call<RunResult>("resume", { run_id: runId });
/** 定时: 建一个 hermes 定时任务, 到点让小鲶跑这个工作流 (schedule = 5 段 cron) */
export const workflowScheduleSet = (skillPath: string, title: string, schedule: string) =>
  invoke<void>("workflow_schedule_set", { skillPath, title, schedule });
export const workflowSetAutoConfirm = (skillPath: string, on: boolean) =>
  call<{ auto_confirm_outward: boolean }>("set_auto_confirm", { skill_path: skillPath, on });
