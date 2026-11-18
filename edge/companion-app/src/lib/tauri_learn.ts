/** 小鲶学习 (11/18, docs/LEARNING-PLAN.md) —— Rust learn_cmd → tool-bridge learn/* (learning.py)。
 *  写操作立即返回 (模型在后台跑), 界面在 running 时每 1.5 秒拉一次 learnGet。 */
import { invoke as rawInvoke } from "@tauri-apps/api/core";

export type LearnMode = "quick" | "deep";
export type LearnPhase = "probe" | "plan_review" | "teach" | "done";

export interface PlanNode { id: string; title: string }
export interface Plan { nodes: PlanNode[]; edges: [string, string][] }

export interface LearnOption { value: string; label: string }
export interface LearnQuestion {
  type: "choice" | "recall" | "ask";
  role: "probe" | "predict" | "check" | "ask";
  node?: string;
  question: string;
  options?: LearnOption[];
  multi?: boolean;
  /** 答完才有 (没答的题界面拿不到标准答案) */
  answer?: string[] | string;
  explanation?: string;
}

export interface Source { kind: string; title: string; where: string }

export type LearnEvent = { id: string; at: number; role: "tutor" | "learner" } & (
  | { kind: "say"; text: string; sources?: Source[] }
  | { kind: "question"; qid: string; q: LearnQuestion }
  | { kind: "answer"; qid: string; picked?: string[]; text?: string; dont_know?: boolean; confidence?: number;
      thinking?: string; verdict?: "correct" | "wrong" | "partial" | "dont_know"; skipped?: boolean }
  | { kind: "grade"; qid: string; verdict: "correct" | "partial" | "wrong"; feedback: string }
  | { kind: "assessment"; goal: string; summary: string;
      strands: { strand: string; solid?: string; gap?: string; misconceptions?: string; build_from?: string }[] }
  | { kind: "plan"; plan: Plan; approach?: string; revised?: boolean }
  | { kind: "finish"; summary: string }
);

export interface LearnSession {
  id: string;
  goal: string;
  mode: LearnMode;
  phase: LearnPhase;
  created: number;
  updated: number;
  plan: Plan | null;
  pending: { qid: string } | null;
  last_error: string | null;
  current?: string;
  visited?: string[];
  sources: Source[];
  stalled: boolean;
}

export interface LearnView { session: LearnSession; events: LearnEvent[]; running: boolean }

export interface LearnSummary {
  id: string; goal: string; mode: LearnMode; phase: LearnPhase; created: number; updated: number; nodes: number; visited: number;
}

export interface LearnAnswer {
  qid: string;
  picked?: string[];
  text?: string;
  dont_know?: boolean;
  confidence?: number;
  thinking?: string;
}

export const learnList = () => rawInvoke<{ sessions: LearnSummary[] }>("learn_list").then((r) => r.sessions);
export const learnGet = (id: string) => rawInvoke<LearnView>("learn_get", { id });
export const learnCreate = (goal: string, mode: LearnMode) => rawInvoke<LearnView>("learn_create", { goal, mode });
export const learnAnswer = (id: string, answer: LearnAnswer) => rawInvoke<LearnView>("learn_answer", { id, answer });
export const learnSay = (id: string, text: string, start = false) => rawInvoke<LearnView>("learn_say", { id, text, start });
export const learnRetry = (id: string) => rawInvoke<LearnView>("learn_retry", { id });
export const learnDelete = (id: string) => rawInvoke<{ ok: boolean }>("learn_delete", { id });

export const PHASE_LABEL: Record<LearnPhase, string> = {
  probe: "摸底中", plan_review: "看路线图", teach: "讲解中", done: "学完了",
};
