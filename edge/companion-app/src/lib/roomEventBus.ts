/** 群消息事件的小广播 (11/09)。colleagueRoomsStore 取到群消息后只管 emit, 谁要听自己订 ——
 *  群聊不再直接认识「小鲶秘书」(以前在 store 里直接调 roomAskFlow, 关掉 / 拿掉这个能力得改群聊代码)。 */
import type { RoomEvent } from "./tauri_colleague_rooms";

type Listener = (events: RoomEvent[]) => void;
const listeners = new Set<Listener>();

export function onRoomEvents(fn: Listener): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

export function emitRoomEvents(events: RoomEvent[]): void {
  if (events.length === 0) return;
  for (const fn of listeners) {
    try {
      fn(events);
    } catch (e) {
      console.warn("[room-events] 订阅方出错:", e);
    }
  }
}
