/** 群里 @我 交代的事 → 提醒条 (11/09 小鲶秘书)。规则和文案在 lib/roomAsks.ts。
 *
 * colleagueRoomsStore 每次取到群消息后调 processRoomMentions: 对 @我 的普通文字消息, 规则筛过再问一次模型,
 * 是交代事就挂提醒。@我 本身的系统通知照旧由 colleagueRoomsStore 发, 这里不再另发。
 */
import { askReminderId, ASK_SYSTEM_PROMPT, askUserPrompt, looksLikeAsk, parseAsk, roomAskReminder } from "./roomAsks";
import { prefEnabled, ROOM_ASK_KEY } from "./secretaryPrefs";
import { serviceLlm } from "./serviceLlm";
import { roomGet, type RoomEvent } from "./tauri_colleague_rooms";
import { useAgentStore } from "../store/agent";
import { useChatStore } from "../store/chat";
import { useRemindersStore } from "../store/reminders";

/** 一次最多看这么多条 (离线一天回来一下子几十条 @我, 不能一口气调几十次模型) */
const MAX_PER_SYNC = 5;

/** 返回挂上提醒条的条数。export 给测试。 */
export async function processRoomMentions(events: RoomEvent[], now = new Date()): Promise<number> {
  if (!prefEnabled(ROOM_ASK_KEY)) return 0;
  const store = useRemindersStore.getState();
  const skip = store.activeKeys(now.getTime());
  const cands = events
    .filter((e) => e.kind === "mention" && e.msg_kind === "text" && e.joined !== false && e.msg_id)
    .filter((e) => !skip.has(askReminderId(e.room_id, e.msg_id!)))
    .filter((e) => looksLikeAsk(e.text ?? ""))
    .slice(0, MAX_PER_SYNC);
  if (cands.length === 0) return 0;
  const model = useChatStore.getState().model;
  const agentName = useAgentStore.getState().name || "小鲶";
  let added = 0;
  for (const ev of cands) {
    // 事件里的字截了 80 个, 取本机群记录里的全文
    let text = ev.text ?? "";
    let me = "";
    try {
      const d = await roomGet(ev.room_id);
      me = d.me;
      const m = d.messages.find((x) => x.msg_id === ev.msg_id);
      if (m) text = m.text;
    } catch (e) {
      console.warn("[room-ask] 读群记录失败, 用通知里的字:", e);
    }
    const answer = await serviceLlm("room-ask", ASK_SYSTEM_PROMPT,
      askUserPrompt({ roomTitle: ev.title, from: ev.from, text, me }, now), model, { maxTokens: 200, timeoutMs: 30_000 });
    const ask = answer === null ? null : parseAsk(answer, now);
    if (!ask) continue;
    const r = roomAskReminder({ roomId: ev.room_id, roomTitle: ev.title, msgId: ev.msg_id!, from: ev.from, text, ...ask }, now, agentName);
    if (store.add(r, now.getTime())) added++;
  }
  return added;
}
