/** BL-ADVISOR-DECISIONS (5/21 Phase 7 第 2 步): 决策留痕 — TS wrapper.
 *
 * 设计稿 §6.2: ~/.catfish/decisions.jsonl
 */

import { invoke as rawInvoke } from "@tauri-apps/api/core";

export interface OptionMeta {
  label: string;       // "A" / "B" / "C"
  tone: string;        // "strict" / "balanced" / ...
  summary?: string;
}

export interface DecisionRecord {
  ts: string;          // ISO-8601 (Rust 端自动填, 前端传空即可)
  mainTaskId: number;
  taskTitle: string;
  optionsOffered: OptionMeta[];
  aiLean?: string;     // "B"
  userChoice?: string; // "B" / null = 没选/重写了
  userAction?: string; // "sent" / "drafted_but_held" / "overridden" / "ignored"
  complianceFlagsAtDecision: string[];
  contextRefs: string[];
  draftPathChosen?: string;
}

/** Append 一条决策记录. ts 在 Rust 端自动填. */
export const decisionRecord = (record: Omit<DecisionRecord, "ts">) =>
  rawInvoke<void>("decision_record", { record: { ...record, ts: "" } });

/** 列最近 N 条 (debug / UI 复盘). */
export const decisionListRecent = (limit = 50) =>
  rawInvoke<DecisionRecord[]>("decision_list_recent", { limit });

/** LLM tool `catfish_recall_decision_history` 后端: 按 topic + person + project 检索. */
export const decisionSearch = (
  topic: string,
  person?: string,
  project?: string,
  limit = 5,
) =>
  rawInvoke<DecisionRecord[]>("decision_search", {
    topic,
    person,
    project,
    limit,
  });
