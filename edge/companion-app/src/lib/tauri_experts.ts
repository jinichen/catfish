/** 本机专家 (10/18, docs/EXPERT-PACK-PLAN.md 第一阶段) —— Rust commands/experts_local.rs 的前端封装。 */
import { invoke } from "@tauri-apps/api/core";

export interface ExpertRoute {
  when: string;
  /** workflow:<ns/name> | skill:<ns/name> | knowledge | decline */
  use: string;
  ask?: string[];
}

export interface ExpertDef {
  id: string;
  name: string;
  description: string;
  persona: string;
  quick_prompts: string[];
  workflows: string[];
  skills: string[];
  routes: ExpertRoute[];
  checks: { after: string; verify: string }[];
  tool_groups: string[];
  allow_web: boolean;
  allow_code: boolean;
  model: string;
  enabled: boolean;
}

export interface ExpertSummary {
  id: string;
  name: string;
  description: string;
  quick_prompts: string[];
  enabled: boolean;
  updated_at: string;
  profile_id: string;
  routes: number;
  workflows: number;
  skills: number;
}

export interface ExpertCatalog {
  workflows: { skill_path: string; description: string }[];
  skills: { skill_path: string; description: string; runnable: boolean }[];
  tool_groups: { id: string; label: string }[];
}

export interface ChatExpert {
  profileId: string;
  expertId: string;
  name: string;
  description: string;
  quickPrompts: string[];
  fixedModel: string | null;
  ready: boolean;
}

const call = <T>(action: string, params: Record<string, unknown> = {}) => invoke<T>("expert_local_call", { action, params });

export const expertCatalog = () => call<ExpertCatalog>("catalog");
export const expertList = () => call<{ experts: ExpertSummary[] }>("list");
export const expertGet = (id: string) => call<ExpertDef>("get", { id });
export const expertSave = (expert: ExpertDef) => call<{ expert: ExpertDef }>("save", { expert });
/** 存好之后让它生效: 建 / 更新专家 profile、写人设和范围、网关托管 (会重启一次小鲶本机服务)。 */
export const expertApply = (id: string) => invoke<{ ok: boolean; profile_id: string }>("expert_local_apply", { id });
export const expertRemove = (id: string) => invoke<void>("expert_local_remove", { id });
export const expertChatList = () => invoke<ChatExpert[]>("expert_chat_list");
