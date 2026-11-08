/** 11/07 知识库类型定义 (属性 / 状态 / 类型上的行动 / 整理属性) —— 后端 commands/wiki_types.rs → tool-bridge wiki_types.py */
import { invoke } from "@tauri-apps/api/core";

export interface WikiTypeProperty {
  key: string;
  label: string;
  type: string;
  hint: string;
  value: string;
}

export interface WikiTypeStatus {
  code: "ok" | "warn" | "expired" | "unknown" | "done";
  label: string;
  value: string;
  days_left?: string;
}

export interface WikiTypeAction {
  id: string;
  label: string;
  description: string;
  skill_path: string;
  /** 属性已经填进去的那句「要做的事」 */
  instruction: string;
  available: boolean;
  reason: string | null;
  skill_dir: string | null;
  runnable: boolean;
}

export interface WikiTypeEntry {
  ok: boolean;
  rel_path: string;
  typed: boolean;
  type?: string;
  label?: string;
  properties?: WikiTypeProperty[];
  status?: WikiTypeStatus | null;
  actions?: WikiTypeAction[];
}

export interface WikiPropsProposal {
  rel_path: string;
  title: string;
  current: Record<string, string>;
  proposed: Record<string, string>;
  /** 每个值从哪来: pattern = 固定句式抽的, model = 小鲶读正文抽的 */
  source: Record<string, "pattern" | "model">;
}

export interface WikiPropsProposeResult {
  ok: boolean;
  type: string;
  label: string;
  properties: { key: string; label: string; type: string }[];
  scanned: number;
  items: WikiPropsProposal[];
  used_model: boolean;
  model_candidates: number;
  /** 11/08: 固定句式抽到、已经直接写进去的 (标题 + 属性) */
  written?: { rel_path: string; title: string; props: Record<string, string> }[];
}

export interface WikiPropsApplyResult {
  ok: boolean;
  written: number;
  errors: { rel_path: string; error: string }[];
  backup_dir: string | null;
}

export const wikiTypesList = () => invoke<{ ok: boolean; types: { key: string; label: string }[] }>("wiki_types_list");
export const wikiTypeEntry = (relPath: string) => invoke<WikiTypeEntry>("wiki_type_entry", { relPath });
export const wikiPropsPropose = (typeKey: string, useModel: boolean) =>
  invoke<WikiPropsProposeResult>("wiki_props_propose", { typeKey, useModel });
/** 11/08 保存前校验: 相似名 (新建时) / 关系类型不对 / 引用不存在的页。tool-bridge 没起来会抛, 调用方当没校验照常保存。 */
export interface WikiGuardResult {
  ok: boolean;
  bad: { name: string; rel: string; why: string }[];
  missing: string[];
  similar: { rel_path: string; title: string; matched: string }[];
  similar_message: string;
  missing_message: string;
}
export const wikiGuardCheck = (params: { title: string; subtype?: string | null; related: { name: string; rel: string | null }[];
                                         rel_path?: string; new?: boolean }) =>
  invoke<WikiGuardResult>("wiki_guard_check", { params });
export const wikiPropsApply = (items: { rel_path: string; props: Record<string, string> }[]) =>
  invoke<WikiPropsApplyResult>("wiki_props_apply", { items });

// ─── 11/07 类型定义页 ───────────────────────────────────────────────────────

export type DefOrigin = "builtin" | "local" | "builtin+local";

export interface TypeDefProperty {
  key: string;
  label: string;
  type: string;
  hint?: string;
  keywords?: string[];
  origin: DefOrigin;
  hidden: boolean;
}

export interface TypeDefAction {
  id: string;
  label: string;
  description?: string;
  instruction?: string;
  skill_path?: string;
  origin: DefOrigin;
  hidden: boolean;
}

export interface TypeDef {
  key: string;
  label: string;
  origin: "builtin" | "local";
  status: { label?: string; from?: string; warn_days?: number } | null;
  properties: TypeDefProperty[];
  actions: TypeDefAction[];
}

export interface TypeDefsView {
  ok: boolean;
  types: TypeDef[];
  /** 知识库里有、还没定义的细分类型 (新建类型从这里挑) */
  subtypes: { key: string; count: number }[];
}

export type TypeDefChange =
  | { op: "add_type"; type: string; label: string }
  | { op: "delete_type"; type: string }
  | { op: "rename_type"; type: string; label: string }
  | { op: "set_warn_days"; type: string; warn_days: number }
  | { op: "add_property"; type: string; property: Partial<TypeDefProperty> }
  | { op: "update_property"; type: string; key: string; property: Partial<TypeDefProperty> }
  | { op: "hide_property" | "unhide_property" | "delete_property"; type: string; key: string }
  | { op: "add_action"; type: string; action: Partial<TypeDefAction> }
  | { op: "update_action"; type: string; id: string; action: Partial<TypeDefAction> }
  | { op: "hide_action" | "unhide_action" | "delete_action"; type: string; id: string };

export const wikiTypeDefs = () => invoke<TypeDefsView>("wiki_type_defs");
export const wikiTypeDefsApply = (change: TypeDefChange) => invoke<TypeDefsView>("wiki_type_defs_apply", { change });
