/**
 * 会中小鲶 (11/15, docs/MEETING-MINUTES-PLAN.md §8) —— Rust commands/meeting_live_cmd.rs。
 *
 * 录音时 Rust 起实时转写进程, 写会议目录的 live_transcript.jsonl; 问小鲶 / 主动提示走 tool-bridge meeting_live.py,
 * 结果存 live_notes.json。都只在本机, 只有会中文字经网关去大模型。
 */
import { invoke as rawInvoke } from "@tauri-apps/api/core";

import type { MeetingMeta } from "./tauri_meeting";

export interface LiveLine { start: number; end: number; text: string; track: "mic" | "system" }

export type LiveStatus = "loading" | "running" | "lagging" | "finishing" | "done" | "error";
export interface LiveState {
  status: LiveStatus;
  /** 落后录音多少秒 */
  lag_secs: number;
  /** 还没定下来的半句 */
  partial: string;
  load_secs?: number;
  message?: string;
  updated?: number;
}

export type HintType = "decision" | "todo" | "question" | "conflict" | "followup";
export type NoteState = "new" | "pinned" | "ignored";
export interface HintItem { id: string; type: HintType; text: string; state: NoteState }
export interface AskNote { id: string; kind: "ask"; q: string; answer: string; state: NoteState; at: number; created: number }
export interface HintNote { id: string; kind: "hint"; items: HintItem[]; from: number; at: number; created: number }
export type LiveNote = AskNote | HintNote;
export interface LiveNotes { notes: LiveNote[]; hint_cursor: number }

export const HINT_LABEL: Record<HintType, string> = {
  decision: "决议", todo: "待办", question: "没人答的问题", conflict: "跟资料对不上", followup: "可以追问",
};

export const meetingLiveRead = (id: string, since: number) =>
  rawInvoke<{ lines: LiveLine[]; total: number; state: LiveState | null }>("meeting_live_read", { id, since });
export const meetingLiveAsk = (id: string, question: string) =>
  rawInvoke<{ note: AskNote }>("meeting_live_assist", { id, kind: "ask", question });
/** 主动提示: 新说的话不多 / 没什么可提示的 → note 是 null */
export const meetingLiveHint = (id: string) =>
  rawInvoke<{ note: HintNote | null; skipped?: string }>("meeting_live_assist", { id, kind: "hint", question: null });
export const meetingLiveNotes = (id: string) => rawInvoke<LiveNotes>("meeting_live_notes", { id });
export const meetingLiveNoteSet = (id: string, noteId: string, itemId: string | null, state: NoteState) =>
  rawInvoke<LiveNotes>("meeting_live_note", { id, noteId, itemId, state, delete: false });
export const meetingLiveNoteDelete = (id: string, noteId: string, itemId: string | null = null) =>
  rawInvoke<LiveNotes>("meeting_live_note", { id, noteId, itemId, state: null, delete: true });
export const meetingLiveHintsSet = (id: string, on: boolean) => rawInvoke<MeetingMeta>("meeting_live_hints_set", { id, on });

/** 状态一句话 (界面上淡色显示); null = 不用说 */
export function liveStatusText(state: LiveState | null, lines: number, recordingSecs: number): string | null {
  if (!state) {
    return recordingSecs > 40 && !lines
      ? "没有实时文字: 会议组件包还没装好 (会后转写那个)。装好后下一场会议开始就有; 现在照样能问小鲶, 但它只能按你说的答。"
      : "实时文字准备中…";
  }
  switch (state.status) {
    case "loading": return "小鲶在准备实时文字 (加载识别模型, 约 20 秒)…";
    case "lagging": return `这台电脑转得慢, 实时文字落后约 ${Math.round(state.lag_secs)} 秒; 问小鲶照常, 会后转写不受影响。`;
    case "error": return `实时文字没起来${state.message ? ` (${state.message})` : ""} —— 录音不受影响, 会后照常转写。`;
    case "finishing": return "录音结束了, 在把最后一段转完…";
    default: return null;
  }
}

/** mm:ss / h:mm:ss */
export function clock(sec: number): string {
  const s = Math.max(0, Math.floor(sec));
  const mm = String(Math.floor((s % 3600) / 60)).padStart(2, "0");
  const ss = String(s % 60).padStart(2, "0");
  return s >= 3600 ? `${Math.floor(s / 3600)}:${mm}:${ss}` : `${mm}:${ss}`;
}

/** 点了实时文字里的一句: 问题框里先填「关于 [12:30]「…」: 」 */
export function quoteLine(l: LiveLine): string {
  const t = l.text.length > 40 ? `${l.text.slice(0, 40)}…` : l.text;
  return `关于 [${clock(l.start)}]「${t}」: `;
}
