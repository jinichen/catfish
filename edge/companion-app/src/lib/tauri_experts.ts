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
  /** 从专家包装的才有 (不能直接改, 只能升级 / 卸载) */
  pack: { version: string; origin: "hub" | "file"; signed: boolean; reviewed_by: string | null } | null;
  /** 自己搭的专家上次打包的版本 */
  packed_version: string | null;
}

/** 搭专家时能选的一项: 中文名 / 一句话说明 / 来源 (personal 我的 · department 部门 · external 外部) */
export interface CatalogItem {
  skill_path: string;
  description: string;
  title: string;
  summary: string;
  source: string;
}

export interface ExpertCatalog {
  workflows: CatalogItem[];
  skills: (CatalogItem & { runnable: boolean })[];
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
/** 让小鲶帮我填: 按名字 + 简介起草 (只会用你已有的工作流 / 技能; 不保存) */
export const expertSuggest = (name: string, description: string) =>
  call<{ suggestion: Pick<ExpertDef, "workflows" | "skills" | "routes" | "tool_groups" | "persona" | "quick_prompts"> }>(
    "suggest", { name, description });
/** 存好之后让它生效: 建 / 更新专家 profile、写人设和范围、网关托管 (会重启一次小鲶本机服务)。 */
export const expertApply = (id: string) => invoke<{ ok: boolean; profile_id: string }>("expert_local_apply", { id });
export const expertRemove = (id: string) => invoke<void>("expert_local_remove", { id });
export const expertChatList = () => invoke<ChatExpert[]>("expert_chat_list");

// ── 专家包 (10/18 第二 / 三阶段) ─────────────────────────────────

/** 权限摘要: 从包里实际内容算的 (不信清单自报) */
export interface PackPermissions {
  read: string[];
  local: string[];
  outward: string[];
  /** 对外积木的名字 (跑到时一律停下等确认) */
  outward_actions?: string[];
  browser: boolean;
  sites: string[];
  code: boolean;
  web: boolean;
}

export interface PackPreview {
  id: string;
  version: string;
  name: string;
  permissions: PackPermissions;
  sha256: string;
  size: number;
  workflows: string[];
  skills: string[];
  filename: string;
}

export interface PackInspect {
  id: string;
  version: string;
  name: string;
  description: string;
  routes: ExpertRoute[];
  workflows: string[];
  skills: string[];
  permissions: PackPermissions;
  sha256: string;
  problems: string[];
  /** new 新装 / upgrade 升级 / same 已装同版本 / older 比已装的旧 / conflict 本机有同 id 的自建专家 */
  state: "new" | "upgrade" | "same" | "older" | "conflict";
  installed_version: string | null;
  /** 升级时比旧版多出来的权限 */
  expanded: string[];
}

export type PackInstallResult =
  | { ok: true; id: string; profile_id: string; version: string; upgraded: boolean; signature: { status: string; reviewed_by?: string } }
  | { ok: false; need_confirm: true; inspect: PackInspect }
  | { ok: false; installed: true; id: string; error: string };

export interface HubExpert {
  id: string;
  version: string;
  name: string;
  description: string;
  status: "pending" | "approved" | "rejected" | "withdrawn";
  scope: "department" | "company";
  departments: string[];
  submitted_by: string;
  submitted_at: string;
  submit_note?: string;
  reviewed_by?: string;
  reviewed_at?: string;
  review_note?: string;
  summary?: Partial<PackPermissions>;
  sites?: string[];
  routes?: ExpertRoute[];
  /** 市场列表才有: 本机装的版本 / 能不能升级 / 升级多出来的权限 / 本机有同 id 的自建专家 */
  installed_version?: string | null;
  can_upgrade?: boolean;
  expanded?: string[];
  local_conflict?: boolean;
}

export const expertPackPreview = (id: string, version?: string) => call<PackPreview>("pack_preview", { id, version });
export const expertPackInspect = (path: string) => call<PackInspect>("pack_inspect", { path });
export const expertPackPublish = (p: { id: string; scope: "department" | "company"; departments: string[]; note: string; version?: string }) =>
  call<PackPreview & { status: string }>("pack_publish", p);
export const expertPackMarket = () => call<{ experts: HubExpert[] }>("pack_market");
export const expertPackMine = () => call<{ experts: HubExpert[] }>("pack_mine");
export const expertPackWithdraw = (id: string, version: string) => call<unknown>("pack_withdraw", { id, version });
export const expertPackPickFile = () => invoke<string | null>("expert_pack_pick_file");
/** 弹保存框导出; 取消返回 null */
export const expertPackExport = (id: string, version?: string) =>
  invoke<(PackPreview & { path: string }) | null>("expert_pack_export", { id, version: version ?? null });
/** 从文件 {path} 或市场 {from: "hub", id, version} 装; 没确认过返回 need_confirm + 权限摘要; 装好会顺手落 profile */
export const expertPackInstall = (params: { path?: string; from?: "hub"; id?: string; version?: string; confirmed?: boolean }) =>
  invoke<PackInstallResult>("expert_pack_install", { params });
