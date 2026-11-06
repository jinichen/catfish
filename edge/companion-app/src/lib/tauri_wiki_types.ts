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
export const wikiPropsApply = (items: { rel_path: string; props: Record<string, string> }[]) =>
  invoke<WikiPropsApplyResult>("wiki_props_apply", { items });
