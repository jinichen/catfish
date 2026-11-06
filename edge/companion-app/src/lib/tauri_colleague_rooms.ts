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
  /** kind: 任务状态 / 自动转发的 text 里已经带了名字, 列表预览不再加「名字:」 */
  last: { from: string; name: string; text: string; at: string; kind?: RoomMessage["kind"] } | null;
  activity: string;
  /** 11/6 本机记录保留几天、谁定的 (默认 / 公司规定 / 群主设置 / 你自己设的) */
  retention?: { days: number; source: "default" | "company" | "room" | "personal"; label: string };
  /** 群主设的保留天数 (空 = 跟公司 / 默认) */
  retention_days?: number | null;
}

/** 11/6 记录保留设置 (这台电脑): 群主 / 个人能设的上限 = ceiling */
export interface RetentionSettings {
  default: number; company: number | null; ceiling: number; ceiling_source: "company" | "default"; personal: number | null;
  removed?: number;
  /** 11/6 本机库加没加密 (null = 还没打开过) */
  local?: { encrypted: boolean | null; why?: string };
}

export interface RoomMember {
  email: string;
  name: string;
  state: MemberState;
  owner: boolean;
  me: boolean;
}

/** 群里派的任务卡 (GROUP-CHAT-PLAN 第二步) */
export interface RoomCard {
  task_id: string;
  title: string;
  deadline: string | null;
  /** 定时 / 周期的说明, 如「每周五 17:00」 */
  schedule: string | null;
  assignees: string[];
  owner: string;
  /** 第三步特例: 这个定时 / 周期任务的每次结果会自动发到群里 */
  auto_post?: boolean;
}

/** 发起人电脑自动转进群的一次结果 */
export interface RoomAuto { task_id: string; title: string; period: number; by: string }

export type RoomTaskState = "accepted" | "done" | "declined" | "quit";

export interface RoomStatus { task_id: string; title: string; state: RoomTaskState; period?: number }

export interface RoomMessage {
  msg_id: string;
  /** 本机库里的先后编号 (11/6): 往上翻 / 搜索结果定位用 */
  seq?: number;
  kind: "text" | "system" | "card" | "status" | "auto";
  card?: RoomCard;
  status?: RoomStatus;
  auto?: RoomAuto;
  from?: string;
  at: string;
  text: string;
  mentions?: string[];
  reply_to?: string;
  /** 我发的、有人没收到 */
  undelivered?: string[];
  /** 11/6: 老版本同事发来的明文 (没加密), 界面标「未加密」 */
  plain?: boolean;
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
export const roomGet = (roomId: string, opts: { before?: number; around?: number } = {}) =>
  call<RoomDetail>("get", { room_id: roomId, ...opts });
/** 群内搜索本机记录 (11/6): 新的在前, 最多 50 条 */
export const roomSearch = (roomId: string, q: string, from?: string) =>
  call<{ q: string; results: (RoomMessage & { name: string })[] }>("search", { room_id: roomId, q, from: from ?? null });
export const roomSettingsGet = () => call<RetentionSettings>("settings_get");
export const roomSettingsSet = (days: number | null) => call<RetentionSettings>("settings_set", { days });
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
export const roomUpdate = (roomId: string, name: string, purpose: string, retentionDays?: number | null) =>
  call<{ room: RoomSummary; failed: Failed[] }>("update", {
    room_id: roomId, name, purpose, ...(retentionDays !== undefined ? { retention_days: retentionDays } : {}),
  });
export const roomDisband = (roomId: string) => call<{ room: RoomSummary; failed: Failed[] }>("disband", { room_id: roomId });
export const roomLeave = (roomId: string) => call<{ room: RoomSummary; failed: Failed[] }>("leave", { room_id: roomId });
export const roomDelete = (roomId: string) => call<{ ok: boolean }>("delete", { room_id: roomId });

/** 第三步: 小鲶读本机群记录出草稿 (总结 / 待办 / 起草回复), 不发。instruction = 员工在输入框里写的要求 */
export type AssistKind = "summary" | "todos" | "reply";
export const roomAssist = (roomId: string, kind: AssistKind, instruction?: string, replyTo?: string | null) =>
  invoke<{ text: string; kind: AssistKind }>("colleague_room_assist", {
    roomId, kind, instruction: instruction ?? null, replyTo: replyTo ?? null,
  });

/** 投递失败汇总成一句人话 (空 = 都送到了)。 */
export function failedNotice(failed: Failed[], what = "没收到"): string | undefined {
  if (!failed.length) return undefined;
  return `${failed.length} 人${what}: ${failed.map((f) => `${f.email} — ${f.error}`).join("; ")}`;
}
