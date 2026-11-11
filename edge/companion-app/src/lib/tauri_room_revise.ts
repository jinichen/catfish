/** 群里一起改文档 (11/11, docs/DOC-REVISE-MERGE-PLAN.md) —— tool-bridge colleague_rooms_revise.py 的前端封装 (走 colleague_room_call)。 */
import { invoke } from "@tauri-apps/api/core";

/** 一轮修改的卡片 (群消息 kind=revise 带的) */
export interface ReviseCard {
  rev_id: string; doc_id: string; title: string; version: number;
  /** docx / xlsx / pptx / text / csv 能自动合并; whole = 只能整份选用 */
  fmt: string; name: string; owner: string; editors: string[]; deadline: string | null; note: string;
}
export type ReviseState = "editing" | "returned" | "unchanged" | "declined" | "remind" | "merged" | "closed";
export interface ReviseStatus { rev_id: string; doc_id: string; title: string; state: ReviseState; version?: number }
export interface ReviseReturn { rev_id: string; doc_id: string; version: number; unchanged: boolean; note: string }

export interface RevisePerson {
  email: string; name: string;
  state: "pending" | "editing" | "returned" | "unchanged" | "declined";
  at: string | null; late: boolean; after_merge?: boolean; note?: string; plain?: boolean;
  /** 发起人自己「我也改」的那一行 (不算进「已交 x/y」) */
  self?: boolean;
}
export interface ReviseProgress {
  card: ReviseCard; owner: boolean; me: string; people: RevisePerson[];
  merged: { version: number; at: string; msg_id: string } | null; closed: boolean;
  /** 请的人都交了 / 说了不改 */
  all_in: boolean; can_merge: boolean;
  my_state: RevisePerson["state"] | null; local_path: string | null; msg_id: string;
}
export interface ReviseOption { key: string; who: string[]; names: string[]; text: string }
export interface ReviseConflict {
  cid: string; where: string; kind: "text" | "value" | "slide" | "whole"; base: string; options: ReviseOption[];
  /** 两人改的是同一段里不同的地方: 按字拼好的 */
  combined: string | null;
  /** text 类可以「我自己改」 */
  custom?: boolean;
  /** 小鲶给过的建议 (text=null 时 reason 说为什么不给) */
  suggest?: { text: string | null; reason: string | null } | null;
}
export interface ReviseDecision { pick: string; text?: string }
export interface ReviseAnalysis {
  conflicts: ReviseConflict[]; changes: Record<string, number>; same: string[]; warnings: string[];
  names: Record<string, string>; late: boolean; decisions: Record<string, ReviseDecision>; returned: string[];
  /** 发起人自己改的那份 (合并面板里选的) 的文件名; 没加 = null */
  self_file?: string | null;
}
export interface ReviseVersion {
  version: number; at: string; by: string; kind: "start" | "merged"; msg_id: string;
  files: { id: string; name: string; size: number }[]; text?: string;
}

const call = <T,>(action: string, params: Record<string, unknown>) => invoke<T>("colleague_room_call", { action, params });

export const reviseStart = (roomId: string, p: {
  msg_id?: string; file_id?: string; files?: { name: string; stage_id: string }[];
  editors: string[]; deadline: string | null; note: string; title?: string; doc_id?: string;
}) => call<{ rev_id: string; hint: string | null }>("revise_start", { room_id: roomId, ...p });
export const reviseOpen = (roomId: string, revId: string) =>
  call<{ path: string; again?: boolean }>("revise_open", { room_id: roomId, rev_id: revId });
export const reviseReturn = (roomId: string, revId: string, p: {
  note?: string; confirm_unchanged?: boolean; confirm_open?: boolean; stage_id?: string; name?: string;
} = {}) => call<{ ok: boolean; need_confirm?: "unchanged" | "open"; self?: boolean; saved_elsewhere?: SavedElsewhere }>(
  "revise_return", { room_id: roomId, rev_id: revId, ...p });

/** 11/11 Mac 上用 Keynote / Pages / Numbers 打开改的: 改动存成了 .key / .pages / .numbers, 下载的那份没变 */
export interface SavedElsewhere { app: string; office: string; saved: string; target: string }

/** 「协同事项」里的待办: 各群里请我改、我还没交回的 (截止早的在前; days_left 0 = 今天截止, 负数 = 过了) */
export interface RevisePendingItem {
  room_id: string; room_name: string; rev_id: string; title: string; version: number; owner_name: string;
  deadline: string | null; days_left: number | null; state: "pending" | "editing"; local_path: string | null; msg_id: string; at: string;
}
export const revisePending = () => call<{ items: RevisePendingItem[] }>("revise_pending", {});
export const reviseDecline = (roomId: string, revId: string, note: string) =>
  call<{ ok: boolean }>("revise_decline", { room_id: roomId, rev_id: revId, note });
export const reviseProgress = (roomId: string, revId: string) =>
  call<ReviseProgress>("revise_progress", { room_id: roomId, rev_id: revId });
export const reviseRemind = (roomId: string, revId: string) => call<{ ok: boolean }>("revise_remind", { room_id: roomId, rev_id: revId });
export const reviseClose = (roomId: string, revId: string) => call<{ ok: boolean }>("revise_close", { room_id: roomId, rev_id: revId });
export const reviseMerge = (roomId: string, revId: string) => call<ReviseAnalysis>("revise_merge", { room_id: roomId, rev_id: revId });
export const reviseDecide = (roomId: string, revId: string, cid: string, pick: string | null, text?: string) =>
  call<{ decisions: Record<string, ReviseDecision> }>("revise_decide", { room_id: roomId, rev_id: revId, cid, pick, text: text ?? null });
export const reviseSelfClear = (roomId: string, revId: string) => call<{ ok: boolean }>("revise_self_clear", { room_id: roomId, rev_id: revId });
export const revisePublish = (roomId: string, revId: string, note: string) =>
  call<{ version: number; warnings: string[] }>("revise_publish", { room_id: roomId, rev_id: revId, note });
export const reviseVersions = (roomId: string, docId: string) =>
  call<{ title: string; versions: ReviseVersion[] }>("revise_versions", { room_id: roomId, doc_id: docId });
export const reviseDiff = (roomId: string, docId: string, a: number, b: number) =>
  call<{ path: string }>("revise_diff", { room_id: roomId, doc_id: docId, a, b });
/** 拖进 / 选进输入框的文件是不是某一轮请我改的交回稿 */
export const reviseDetect = (roomId: string, stageId: string, name: string) =>
  call<{ rev_id: string | null; title?: string; version?: number; owner_name?: string }>("revise_detect", { room_id: roomId, stage_id: stageId, name });
/** 冲突处让小鲶给建议 (调大模型, 要登录) */
export const reviseSuggest = (roomId: string, revId: string, cid: string) =>
  invoke<{ text: string | null; reason: string | null }>("colleague_room_revise_suggest", { roomId, revId, cid });

export const PERSON_STATE_WORD: Record<RevisePerson["state"], string> = {
  pending: "还没动", editing: "在改", returned: "交回了", unchanged: "看过没改", declined: "不改了",
};

/** 能自动合并的格式的一句话 (卡片 / 发起框上显示) */
export function fmtNote(fmt: string): string {
  if (fmt === "pptx") return "按页合并: 不同人改不同页自动合, 同一页都改了要你选一版";
  if (fmt === "whole") return "这种格式不能自动合并, 交回后整份选用一份";
  return "大家交回后自动合并, 改到同一处的列出来让你定";
}
