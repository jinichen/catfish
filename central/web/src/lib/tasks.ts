/** /api/tasks/me — Multi-Agent Kanban 单员工任务看板 (BL-HERMES013-RED-2 5/13).
 *
 * scope 1: 本地 catfish_run_task + a2a 收件聚合, 单员工单设备.
 * 跨员工跨设备等 BL-RBAC sprint 后做 task_manager 中心 DB 持久化.
 */

import { api } from "./api";

export type TaskStatus =
  | "pending"
  | "running"
  | "waiting"
  | "completed"
  | "failed";

export type TaskSource = "background" | "a2a_inbox";

export interface TaskCard {
  id: string;
  source: TaskSource;
  kind: string;
  title: string;
  status: TaskStatus;
  started_at: string | null;  // ISO 8601
  finished_at: string | null;
  elapsed_s: number | null;
  error: string | null;
  preview: string;
  /** a2a_inbox 才有 — 谁来求助 */
  from_sub?: string;
  /** a2a_inbox 才有 — purpose 全文 (例 "expert_consult:资质审核") */
  purpose?: string;
}

export interface TasksMeResponse {
  cards: TaskCard[];
  summary: Record<TaskStatus, number>;
  total: number;
  limit: number;
  hours_back: number;
  viewer: string;
}

export async function fetchMyTasks(
  hoursBack: number = 48,
  limit: number = 200,
  source?: TaskSource[],
): Promise<TasksMeResponse> {
  const qs = new URLSearchParams({
    hours_back: String(hoursBack),
    limit: String(limit),
  });
  if (source && source.length > 0) {
    qs.set("source", source.join(","));
  }
  return api.get<TasksMeResponse>(`/api/tasks/me?${qs.toString()}`);
}

/** Kanban 5 列顺序 (左 → 右), 跟 backend STATUSES 对齐 */
export const KANBAN_COLUMNS: { status: TaskStatus; label: string; emoji: string }[] = [
  { status: "pending", label: "待开始", emoji: "📋" },
  { status: "running", label: "跑中", emoji: "⚡" },
  { status: "waiting", label: "等待", emoji: "⏳" },
  { status: "completed", label: "完成", emoji: "✓" },
  { status: "failed", label: "失败", emoji: "⚠" },
];
