/** 11/07 知识库类型定义 (属性 / 状态 / 类型上的行动 / 整理属性) —— 后端 commands/wiki_types.rs → tool-bridge wiki_types.py */
import { invoke } from "@tauri-apps/api/core";

export interface WikiTypeProperty {
  key: string;
  label: string;
  type: string;
  hint: string;
  value: string;
  /** 11/26 约束 */
  required?: boolean;
  options?: string[];
  format?: string;
  /** 已经写着的值不合规时的说法 (老数据) */
  problem?: string | null;
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
  /** 11/26 必填没填的属性名 / 属性之间对不上的说法 */
  missing_required?: string[];
  rule_problems?: string[];
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
  /** 11/26 改属性时顺手补上的关系 / 没补的原因 */
  aligned?: string[];
}

// ─── 11/26 约束检查 ─────────────────────────────────────────────────────────

export type ConstraintKind =
  | "missing_required" | "bad_value" | "duplicate" | "rule"
  | "relation_max" | "relation_cycle" | "link_relation" | "link_prop" | "link_conflict";

export interface ConstraintFix {
  op: "set_prop" | "add_relation" | "replace_relation";
  key?: string;
  value?: string;
  rel?: string;
  name?: string;
  old?: string;
  /** 补关系写在哪一页 (默认本条) */
  on?: string;
}

export interface ConstraintIssue {
  rel_path: string;
  title: string;
  type: string;
  type_label: string;
  kind: ConstraintKind;
  key?: string;
  label?: string;
  value?: string;
  message: string;
  options?: string[];
  fix?: ConstraintFix | null;
  alt_fix?: ConstraintFix | null;
}

export interface ConstraintReport {
  ok: boolean;
  summary: Partial<Record<ConstraintKind, number>>;
  total: number;
  types: { key: string; label: string; entries: number; missing: Record<string, number> }[];
  issues: ConstraintIssue[];
}

export const wikiConstraintsReport = (typeKey?: string) => invoke<ConstraintReport>("wiki_constraints_report", { typeKey: typeKey ?? null });
export const wikiConstraintsFix = (items: (ConstraintFix & { rel_path: string })[]) =>
  invoke<{ ok: boolean; done: number; errors: { rel_path: string; error: string }[] }>("wiki_constraints_fix", { items });

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
  /** 11/26 约束: 取值范围 / 必填 / 同类不重复 / 格式 / 日期不晚于今天 */
  options?: string[];
  required?: boolean;
  unique?: boolean;
  format?: string;
  not_future?: boolean;
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
  /** 11/26 内置的属性之间规则 / 关系「最多几个」, 只显示 */
  rules?: string[];
  relation_limits?: string[];
}

export interface TypeDefsView {
  ok: boolean;
  types: TypeDef[];
  /** 知识库里有、还没定义的细分类型 (新建类型从这里挑) */
  subtypes: { key: string; count: number }[];
  /** 11/26 可选的格式 {key: 中文名} */
  formats?: Record<string, string>;
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
