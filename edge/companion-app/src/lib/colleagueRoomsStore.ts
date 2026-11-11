/**
 * 同事群聊的前端状态 (11/5, docs/GROUP-CHAT-PLAN.md)。跟 colleagueTasksStore 同一套:
 * 订阅即开始轮询 `sync` (中央取走即清空, 落进本机账本), 再 `list` 刷列表。
 * 平时 20 秒取一次; 正打开某个群时 3 秒一次 (setActiveRoom), 聊天不用干等。
 * 导航常驻订阅 (TabBar), 不进「协同」页也能看到红点、收到系统通知。
 */
import { useSyncExternalStore } from "react";

import { sendNotification } from "./tauri_app";
import { roomList, roomSync, type RoomEvent, type RoomSummary } from "./tauri_colleague_rooms";
import { emitRoomEvents } from "./roomEventBus";
import { REVISE_PENDING_EVERY_MS, dueToNotify, loadNotified, noticeKey, noticeText, saveNotified } from "./revisePending";
import { revisePending, type RevisePendingItem } from "./tauri_room_revise";

export const ROOMS_POLL_IDLE_MS = 20_000;
export const ROOMS_POLL_ACTIVE_MS = 3_000;

export interface RoomsState {
  me: string;
  rooms: RoomSummary[];
  loaded: boolean;
  /** 最近一次取邮筒失败的原因 (没登录 / 中央连不上 / 中央太旧); 成功一次就清掉 */
  syncError: string | null;
  /** 每次取到新东西 +1, 打开的群据此刷新消息 */
  version: number;
  /** 11/11 群里请我改、我还没交回的 (协同事项待办 + 红点) */
  revise: RevisePendingItem[];
}

let state: RoomsState = { me: "", rooms: [], loaded: false, syncError: null, version: 0, revise: [] };
let reviseAt = 0;

/** 一起改文档的待办: 有新群消息时、或隔一分钟取一次 (算进度要读群记录, 不跟着 3 秒的聊天轮询跑); 快截止的弹一次通知。 */
async function refreshRevise(force: boolean): Promise<RevisePendingItem[]> {
  if (!force && Date.now() - reviseAt < REVISE_PENDING_EVERY_MS) return state.revise;
  reviseAt = Date.now();
  try {
    const items = (await revisePending()).items;
    const notified = loadNotified();
    const due = dueToNotify(items, notified, new Date());
    for (const x of due) {
      const [t, b] = noticeText(x);
      void sendNotification(t, b).catch(() => {});
      notified.add(noticeKey(x));
    }
    if (due.length) saveNotified(notified);
    return items;
  } catch {
    return state.revise;   // 老版本 tool-bridge 没有这个动作 / 没登录: 不影响群聊
  }
}

/** 交回 / 不改了之后马上刷新待办 (不等一分钟) */
export function refreshRevisePending(): Promise<void> {
  return refreshRevise(true).then((revise) => emit({ ...state, revise }));
}
const listeners = new Set<() => void>();
let timer: ReturnType<typeof setInterval> | null = null;
let inflight: Promise<void> | null = null;
let activeRoom: string | null = null;

function emit(next: RoomsState) {
  state = next;
  for (const l of listeners) l();
}

const NOTE: Partial<Record<RoomEvent["kind"], (ev: RoomEvent) => [string, string] | null>> = {
  invite: (e) => ["同事拉你进群", `${e.from} 邀请你加入群「${e.title}」, 在「协同」页 → 群聊里看`],
  mention: (e) => [`「${e.title}」里有人@你`, `${e.from}: ${e.text ?? ""}`],
  msg: (e) => [`「${e.title}」有新消息`, `${e.from}: ${e.text ?? ""}`],
  removed: (e) => ["你被移出了群", `你被群主移出了「${e.title}」`],
  disband: (e) => ["群解散了", `群主解散了「${e.title}」`],
};

/** 系统通知: 正开着的群里的普通消息不打扰; 还没加入的群里的消息不打扰。 */
export function notifyFor(events: RoomEvent[], active: string | null = activeRoom): void {
  for (const ev of events) {
    if ((ev.kind === "msg" || ev.kind === "mention") && (ev.room_id === active || ev.joined === false)) continue;
    const n = NOTE[ev.kind]?.(ev);
    if (n) void sendNotification(n[0], n[1]).catch(() => {});
  }
}

/** 取一次邮筒 + 刷列表。并发调用合并成一次。 */
export function refreshRooms(): Promise<void> {
  if (inflight) return inflight;
  inflight = (async () => {
    let syncError: string | null = null;
    let got = 0;
    try {
      const r = await roomSync();
      got = r.events.length;
      notifyFor(r.events);
      // 谁要群消息自己订 (小鲶秘书「群里 @我 交代的事」在 lib/secretary.ts 里订)
      emitRoomEvents(r.events);
    } catch (e) {
      syncError = e instanceof Error ? e.message : String(e);
    }
    try {
      const list = await roomList();
      const revise = await refreshRevise(got > 0);
      emit({ me: list.me, rooms: list.rooms, loaded: true, syncError, version: state.version + (got ? 1 : 0), revise });
    } catch (e) {
      emit({ ...state, loaded: true, syncError: syncError ?? (e instanceof Error ? e.message : String(e)) });
    }
  })().finally(() => {
    inflight = null;
  });
  return inflight;
}

function restart() {
  if (timer) clearInterval(timer);
  timer = listeners.size ? setInterval(() => void refreshRooms(), activeRoom ? ROOMS_POLL_ACTIVE_MS : ROOMS_POLL_IDLE_MS) : null;
}

/** 正在看哪个群 (null = 没在看): 在看就 3 秒取一次, 这个群的普通消息不弹系统通知。 */
export function setActiveRoom(roomId: string | null): void {
  if (activeRoom === roomId) return;
  activeRoom = roomId;
  restart();
}

export function subscribe(cb: () => void): () => void {
  listeners.add(cb);
  if (!timer) {
    void refreshRooms();
    restart();
  }
  return () => {
    listeners.delete(cb);
    if (listeners.size === 0 && timer) {
      clearInterval(timer);
      timer = null;
    }
  };
}

export const getSnapshot = (): RoomsState => state;

export function useColleagueRooms(): RoomsState {
  return useSyncExternalStore(subscribe, getSnapshot);
}

/** 导航红点: 等我决定加不加入的群 + 已加入群里的未读。 */
export function pendingRoomCount(s: RoomsState = state): number {
  return s.rooms.reduce((n, r) => n + (r.my_state === "invited" ? 1 : r.my_state === "joined" ? r.unread : 0), 0);
}

/** 11/11 群里请我改、还没交回的 (显示在「协同事项」, 也算导航红点) */
export function pendingReviseCount(s: RoomsState = state): number {
  return s.revise.length;
}

/** 测试用: 回到初始状态。 */
export function __resetRoomsStore(): void {
  if (timer) clearInterval(timer);
  timer = null;
  inflight = null;
  activeRoom = null;
  listeners.clear();
  reviseAt = 0;
  state = { me: "", rooms: [], loaded: false, syncError: null, version: 0, revise: [] };
}
