/** 同事群聊 (11/5, docs/GROUP-CHAT-PLAN.md) —— Rust commands/colleague_rooms.rs 的前端封装。规则都在 tool-bridge colleague_rooms.py。 */
import { invoke } from "@tauri-apps/api/core";

/** 我在群里的状态。disbanded = 群主解散了; removed = 被群主移出。 */
export type MyRoomState = "invited" | "joined" | "declined" | "left" | "removed" | "disbanded";
export type MemberState = "invited" | "joined" | "declined" | "left" | "removed";

export interface RoomSummary {
  room_id: string;
  name: string;
  purpose: string;
  owner: string;
  my_state: MyRoomState;
  /** 还在群里的人数 (已邀请 + 已加入) */
  members: number;
  joined: number;
  unread: number;
  /** 未读里 @了我的条数 */
  mentioned: number;
  last: { from: string; name: string; text: string; at: string } | null;
  activity: string;
}

export interface RoomMember {
  email: string;
  name: string;
  state: MemberState;
  owner: boolean;
  me: boolean;
}

export interface RoomMessage {
  msg_id: string;
  kind: "text" | "system";
  from?: string;
  at: string;
  text: string;
  mentions?: string[];
  reply_to?: string;
  /** 我发的、有人没收到 */
  undelivered?: string[];
}

export interface RoomDetail {
  me: string;
  room: RoomSummary;
  members: RoomMember[];
  messages: RoomMessage[];
  /** 更早的条数 (只显示最近 300 条) */
  older: number;
}

export interface RoomEvent {
  kind: "invite" | "join" | "declined" | "leave" | "member" | "removed" | "disband" | "msg" | "mention";
  room_id: string;
  from: string;
  title: string;
  text?: string;
  joined?: boolean;
}

export interface Failed { email: string; error: string }
export interface PersonIn { email: string; name?: string }

const call = <T,>(action: string, params: Record<string, unknown> = {}) =>
  invoke<T>("colleague_room_call", { action, params });

export const roomList = () => call<{ me: string; rooms: RoomSummary[] }>("list");
export const roomGet = (roomId: string) => call<RoomDetail>("get", { room_id: roomId });
export const roomSync = () => call<{ events: RoomEvent[]; gap: boolean }>("sync");
export const roomCreate = (name: string, purpose: string, members: PersonIn[]) =>
  call<{ room: RoomSummary; failed: Failed[] }>("create", { name, purpose, members });
export const roomJoin = (roomId: string) => call<{ room: RoomSummary; failed: Failed[] }>("join", { room_id: roomId });
export const roomDecline = (roomId: string) => call<{ room: RoomSummary; failed: Failed[] }>("decline", { room_id: roomId });
export const roomSend = (roomId: string, text: string, mentions: string[], replyTo?: string | null) =>
  call<{ message: RoomMessage; sent_to: number; failed: Failed[] }>("send", { room_id: roomId, text, mentions, reply_to: replyTo ?? null });
export const roomRead = (roomId: string) => call<{ ok: boolean }>("read", { room_id: roomId });
export const roomAdd = (roomId: string, members: PersonIn[]) =>
  call<{ room: RoomSummary; failed: Failed[] }>("add", { room_id: roomId, members });
export const roomRemove = (roomId: string, email: string) =>
  call<{ room: RoomSummary; failed: Failed[] }>("remove", { room_id: roomId, email });
export const roomUpdate = (roomId: string, name: string, purpose: string) =>
  call<{ room: RoomSummary; failed: Failed[] }>("update", { room_id: roomId, name, purpose });
export const roomDisband = (roomId: string) => call<{ room: RoomSummary; failed: Failed[] }>("disband", { room_id: roomId });
export const roomLeave = (roomId: string) => call<{ room: RoomSummary; failed: Failed[] }>("leave", { room_id: roomId });
export const roomDelete = (roomId: string) => call<{ ok: boolean }>("delete", { room_id: roomId });

/** 投递失败汇总成一句人话 (空 = 都送到了)。 */
export function failedNotice(failed: Failed[], what = "没收到"): string | undefined {
  if (!failed.length) return undefined;
  return `${failed.length} 人${what}: ${failed.map((f) => `${f.email} — ${f.error}`).join("; ")}`;
}
