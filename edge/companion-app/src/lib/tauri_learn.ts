/** 小鲶学习 (11/18, docs/LEARNING-PLAN.md) —— Rust learn_cmd → tool-bridge learn/* (learning.py)。
 *  写操作立即返回 (模型在后台跑), 界面在 running 时每 1.5 秒拉一次 learnGet。 */
import { invoke as rawInvoke } from "@tauri-apps/api/core";

export type LearnMode = "quick" | "deep";
export type LearnPhase = "probe" | "plan_review" | "teach" | "done" | "practice";

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
  | { kind: "problem"; pid: string; p: { statement: string; targets: string[]; mode: PracticeMode; solution?: string } }
  | { kind: "attempt"; pid: string; text: string; thinking?: string }
  | { kind: "hint_request"; pid: string }
  | { kind: "hint"; pid: string; rung: string; text: string }
  | { kind: "give_up"; pid: string }
  | { kind: "conclude"; pid: string; outcome: "solved" | "solvedWithHints" | "movedOn"; verdict: string; rung: string | null;
      miss: string | null; retest: boolean }
  | { kind: "cards"; added: number; skipped: number }
);

export type PracticeMode = "recall" | "discrimination" | "composition";
export interface PracticeSummary { total: number; solved: number; solvedWithHints: number; movedOn: number; misses: Record<string, number> }

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
  /** 练习中正在做的题 */
  problem?: { pid: string } | null;
  practice?: PracticeSummary;
  /** 这个学习做了多少张复习卡 */
  cards?: number;
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
  probe: "摸底中", plan_review: "看路线图", teach: "讲解中", done: "学完了", practice: "练习中",
};

// ── 第 2 步 (11/18): 练习 / 复习卡 / 复习 ──
export const learnPractice = (id: string, next = false) => rawInvoke<LearnView>("learn_practice", { id, next });
export const learnAttempt = (id: string, text: string, thinking?: string) => rawInvoke<LearnView>("learn_attempt", { id, text, thinking });
export const learnAction = (id: string, action: "hint" | "give_up" | "teach" | "cards") => rawInvoke<LearnView>("learn_action", { id, action });

export interface ReviewCard { id: number; type: string; question: string; options: Record<string, string>; tags: string[] }
export interface ReviewStats { total: number; new: number; wrong: number; due_today: number; reviewed_today: number; mastered: number }
export type Rating = "again" | "hard" | "good" | "easy";
export type Previews = Record<Rating, string>;
export interface ReviewNext { questions: ReviewCard[]; stats: ReviewStats; previews: Record<string, Previews> }
export interface ReviewResult {
  needs_rating?: boolean; correct?: boolean | null; rating?: Rating; answer?: string; explanation?: string; source?: string;
  next_review?: string; interval_days?: number; previews?: Previews;
}
export const learnReviewNext = (n = 20) => rawInvoke<ReviewNext>("learn_review_next", { n });
export const learnReviewAnswer = (qid: number, userAnswer?: string, rating?: Rating) =>
  rawInvoke<ReviewResult>("learn_review_answer", { qid, userAnswer, rating });
export const meetingMinutesCards = (id: string) => rawInvoke<{ added: number; skipped: number }>("meeting_minutes_cards", { id });

export const RATING_LABEL: Record<Rating, string> = { again: "忘了", hard: "想得很费劲", good: "想起来了", easy: "很轻松" };
