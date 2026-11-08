/** 分享治理 · 审批人「装到我这里试用」(11/08, docs/SHARE-GOVERNANCE-PLAN.md §2.3) —— Rust commands/share_trial.rs 的前端封装。
 *
 * 验签、trial.json、回传结果、「不在待审就自动卸」都在 tool-bridge skill_trial.py; 这里只是 RPC 壳 + 类型。
 */
import { invoke } from "@tauri-apps/api/core";

import type { PackInstallResult } from "./tauri_experts";
import type { RunSummary } from "./tauri_workflows";

const call = <T>(action: string, params: Record<string, unknown> = {}) => invoke<T>("share_trial_call", { action, params });

/** hub `/skills/review` 里的一条待审技能 (skill_review.public) */
export interface PendingSkill {
  namespace: string;
  name: string;
  version: string;
  scope: "department" | "company";
  departments: string[];
  status: "pending" | "approved" | "rejected" | "withdrawn";
  submitted_by: string;
  submitted_dept?: string;
  submitted_at: string;
  submit_note?: string;
  file_count?: number;
}

/** 本机一条试用装的技能 (skills/<ns>/<name>/trial.json) */
export interface SkillTrial {
  skill_path: string;
  path: string;
  title: string;
  summary: string;
  namespace: string;
  name: string;
  version: string;
  by: string;
  /** unix 秒 */
  expires: number;
  installed_at: string;
  expired: boolean;
}

/** 刷新时被自动卸掉的试用: status = approved (可转正式) / rejected / withdrawn / expired / gone */
export interface RemovedSkillTrial extends SkillTrial {
  status: string;
}
export interface RemovedExpertTrial {
  id: string;
  name: string;
  version: string;
  status: string;
}

export const trialSkillPending = () => call<{ skills: PendingSkill[] }>("skill_pending");
export const trialSkillInstall = (namespace: string, name: string, version: string) =>
  call<{ ok: boolean; installed_path: string; summary: string }>("skill_install", { namespace, name, version });
export const trialSkillList = () => call<{ trials: SkillTrial[] }>("skill_list");
export const trialSkillUninstall = (skillPath: string) =>
  call<{ ok: boolean; restored: string | null }>("skill_uninstall", { skill_path: skillPath });
export const trialSkillResult = (skillPath: string, ok: boolean, summary: string, runId?: string | null) =>
  call<{ ok: boolean; summary: string }>("skill_result", { skill_path: skillPath, ok, summary, run_id: runId ?? null });
export const trialSkillSync = () => call<{ removed: RemovedSkillTrial[] }>("skill_sync");

export const trialExpertPending = () => call<{ experts: import("./tauri_experts").HubExpert[] }>("expert_pending");
export const trialExpertInstall = (id: string, version: string, confirmed = false) =>
  call<PackInstallResult & { summary?: string }>("expert_install", { id, version, confirmed });
export const trialExpertResult = (id: string, ok: boolean, summary: string, runId?: string | null) =>
  call<{ ok: boolean; summary: string }>("expert_result", { id, ok, summary, run_id: runId ?? null });
export const trialExpertSync = () => call<{ removed: RemovedExpertTrial[] }>("expert_sync");

/** 回传结果时可挑的运行记录: skill_path 以 prefix 开头 (技能 `ns/name`, 专家包 `pack-<id>/`) */
export const trialRuns = (prefix: string) => call<{ runs: RunSummary[] }>("runs", { prefix });

export const TRIAL_STATUS_CN: Record<string, string> = {
  approved: "已通过", rejected: "被驳回", withdrawn: "已撤回", expired: "试用到期", gone: "中央上已没有这个版本",
};

/** 剩几天到期 (给「试用」标的 title 用) */
export function trialDaysLeft(expires: number): number {
  return Math.max(0, Math.ceil((expires * 1000 - Date.now()) / 86400000));
}
