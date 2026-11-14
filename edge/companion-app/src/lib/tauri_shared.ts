/** 装的共享内容 (技能 / 专家包 / 部门知识页) 有没有新版、被撤回了没 (11/14)。
 *  Rust services/shared_check.rs 后台每 6 小时查一次; 结果在 ~/.catfish/shared_check.json。 */
import { invoke } from "@tauri-apps/api/core";

export type SharedKind = "skill" | "expert" | "wiki";

export interface SharedItem {
  kind: SharedKind;
  key: string;
  title: string;
  status: string;
  installed_version?: string;
  latest_version?: string | null;
  withdrawn_by?: string | null;
  withdrawn_at?: string | null;
  note?: string | null;
  namespace?: string;
  name?: string;
  id?: string;
  file_id?: string;
}

export interface SharedCheckResult {
  ok?: boolean;
  checked_at?: string;
  errors?: string[];
  apply_errors?: string[];
  updates?: SharedItem[];
  withdrawn?: SharedItem[];
  hidden?: SharedItem[];
}

export const sharedCheckLast = () => invoke<SharedCheckResult>("shared_check_last");
export const sharedCheckRun = () => invoke<SharedCheckResult>("shared_check_run");
