/** 小鲶学习 (11/18, docs/LEARNING-PLAN.md) —— Rust learn_cmd → tool-bridge learn/* (learning.py)。
 *  写操作立即返回 (模型在后台跑), 界面在 running 时每 1.5 秒拉一次 learnGet。 */
import { invoke as rawInvoke } from "@tauri-apps/api/core";

export type LearnMode = "quick" | "deep" | "defense" | "language";
export type LearnPhase = "probe" | "plan_review" | "teach" | "done" | "practice" | "drill" | "lang";

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
  | { kind: "graph"; added: number; linked: number; chains: number }
  | { kind: "examiner_q"; qi: number; text: string; followup?: boolean; total?: number }
  | { kind: "oral_answer"; qi: number; text: string; secs?: number; skipped?: boolean }
  | { kind: "drill_eval"; qi: number; covered: number[]; missed: number[]; wrong: { said: string; fix: string }[]; feedback: string;
      points: string[]; followup: string | null }
  | { kind: "drill_summary"; score: number; total: number; cards: number;
      items: { qi: number; question: string; covered: number; total: number; missed: string[] }[] }
  | { kind: "lang_scene"; activity: "dialogue" | "shadow"; setting: string; role_name?: string }
  | { kind: "lang_line"; who: "role" | "learner"; text: string; zh?: string; secs?: number }
  | { kind: "lang_fix"; items: { said: string; better: string; why: string }[] }
  | { kind: "shadow_set"; sentences: { text: string; zh: string; note: string }[] }
  | { kind: "shadow_result"; idx: number; target: string; heard: string; missed: string[]; extra: string[]; score: number; secs?: number }
  | { kind: "lang_recap"; summary: string; cards: number; expressions: { text: string; zh: string }[];
      mistakes: { said: string; better: string; why: string }[] }
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
  /** 答辩陪练: 第几题 / 共几题 / 问完没有 (评分点不给界面) */
  drill?: { idx: number; total: number; done: boolean } | null;
  /** 外语: 目标语言 / 正在练什么 / 在复盘 */
  lang?: { code: string; activity: "dialogue" | "shadow" | null; ending: boolean } | null;
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
  probe: "摸底中", plan_review: "看路线图", teach: "讲解中", done: "学完了", practice: "练习中", drill: "答辩陪练", lang: "外语练习",
};

// ── 第 2 步 (11/18): 练习 / 复习卡 / 复习 ──
export const learnPractice = (id: string, next = false) => rawInvoke<LearnView>("learn_practice", { id, next });
export const learnAttempt = (id: string, text: string, thinking?: string) => rawInvoke<LearnView>("learn_attempt", { id, text, thinking });
export const learnAction = (id: string, action: "hint" | "give_up" | "teach" | "cards" | "ingest") => rawInvoke<LearnView>("learn_action", { id, action });

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

// ── 第 3 步 (11/18): 我学过的要点 ──
export interface GraphPoint {
  id: string; name: string; statement: string; wiki: string; wiki_title: string;
  /** 除了出生的那份, 还有哪些材料也提到 */
  also: string[];
  /** 推出它的推理链 (「A + B ⇒ 本条」) */
  chains: string[];
}
export interface GraphSource { source: string; label: string; kind: "learn" | "file" | "meeting" | string; points: GraphPoint[] }
export interface GraphView {
  sources: GraphSource[]; points: number; chains: number;
  /** 正在导入的资料名 */
  running: string | null;
  last: { name?: string; added?: number; linked?: number; chains?: number; error?: string; deleted?: { points: number; chains: number } } | null;
}
export const learnGraphView = () => rawInvoke<GraphView>("learn_graph", { action: "view" });
export const learnGraphImport = (stageId: string, name: string) =>
  rawInvoke<GraphView & { chars: number; parts: number }>("learn_graph", { action: "import", params: { stage_id: stageId, name } });
export const learnGraphDelete = (source: string) => rawInvoke<GraphView>("learn_graph", { action: "delete", params: { source } });
export const GRAPH_ACCEPT = ".docx,.pdf,.txt,.md,.pptx,.xlsx";

// ── 第 4 步 (11/18): 答辩陪练 ──
export const learnDrill = (id: string, action: "start" | "answer" | "skip" | "next", text?: string, secs?: number) =>
  rawInvoke<LearnView>("learn_drill", { id, action, text, secs });
/** 🎤 语音输入 (跟聊天框同一套: 本机录音 + 会议组件的 FunASR 转写, 冷启动十几秒) */
export const speechStart = () => rawInvoke<void>("speech_start_recording");
export const speechStopAndTranscribe = () => rawInvoke<string>("speech_stop_and_transcribe");
export const speechCancel = () => rawInvoke<void>("speech_cancel_recording");

// ── 第 5 步 (11/18): 外语 ──
export const LANG_NAMES: Record<string, string> = { en: "英语", ja: "日语", ko: "韩语", fr: "法语", de: "德语", es: "西班牙语", ru: "俄语" };
/** 朗读 / 选系统声音用的 BCP-47 语言 */
export const LANG_BCP47: Record<string, string> = { en: "en-US", ja: "ja-JP", ko: "ko-KR", fr: "fr-FR", de: "de-DE", es: "es-ES", ru: "ru-RU", zh: "zh-CN" };
export const learnCreateLang = (goal: string, lang: string) => rawInvoke<LearnView>("learn_create_lang", { goal, lang });
export const learnLang = (id: string, action: "start" | "say" | "shadow" | "end", params?: Record<string, unknown>) =>
  rawInvoke<LearnView>("learn_lang", { id, action, params });
export const speechStopAndTranscribeLang = (language: string) => rawInvoke<string>("speech_stop_and_transcribe_lang", { language });
