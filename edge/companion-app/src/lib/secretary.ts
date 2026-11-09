/** 小鲶秘书的几个能力, 登记在一处 (11/09)。
 *
 * 鸿波「自主能力的几个能力是强耦合还是松耦合, 容易加入和退出?」—— 原来每个能力各自一个 hook 挂在 App,
 * 开关文案写死在仪表盘卡片里, 群里 @我 那个还直接写在群聊 store 里; 而且关掉「提前准备」, 已经排上的准备任务照样会跑。
 * 现在加一个能力 = 在下面加一项; 拿掉 = 删一项:
 *   · schedule       定时跑一轮 (hooks/useSecretary 统一起计时器, 同一能力不叠两轮)
 *   · onRoomEvents   群消息到了 (lib/roomEventBus, 群聊不认识秘书)
 *   · prefKey        仪表盘「小鲶秘书」里的开关; null = 不设开关 (定时任务做完就报: 那是员工自己建的任务)
 *   · onDisable      关掉时把它已经排上 / 挂上的撤掉, 关了就是真停
 * 提醒条按 Reminder 本身渲染 (标题 / 正文 / 按钮), 新能力不用改提醒条。
 */
import { advancePrepTick, cancelPendingPreps } from "../hooks/useAdvancePrep";
import { cronWatchTick } from "../hooks/useCronWatcher";
import { routineDiscoveryTick } from "../hooks/useRoutineDiscovery";
import { useRemindersStore } from "../store/reminders";
import { processRoomMentions } from "./roomAskFlow";
import { MEETING_PREP_KEY, prefEnabled, ROOM_ASK_KEY, ROUTINE_OFFER_KEY, setPref } from "./secretaryPrefs";
import type { RoomEvent } from "./tauri_colleague_rooms";

export interface SecretaryAbility {
  id: string;
  icon: string;
  label: string;
  hint?: string;
  prefKey: string | null;
  schedule?: { tick: () => Promise<unknown>; everyMs: number; firstMs: number };
  onRoomEvents?: (events: RoomEvent[]) => Promise<unknown>;
  /** 关掉时撤掉已排上 / 已挂上的; 返回给员工看的一句话 (没撤什么就 null) */
  onDisable?: () => Promise<string | null>;
}

const MIN = 60_000;

const dropReminders = (kind: string, what: string) => async () => {
  const before = useRemindersStore.getState().items.length;
  useRemindersStore.getState().drop((r) => r.kind === kind);
  const n = before - useRemindersStore.getState().items.length;
  return n > 0 ? `提醒条上 ${n} 条${what}也撤了` : null;
};

export const SECRETARY: SecretaryAbility[] = [
  {
    id: "cron-done",
    icon: "✅",
    label: "定时任务做完就报",
    prefKey: null,
    schedule: { tick: () => cronWatchTick(), everyMs: MIN, firstMs: 0 },
  },
  {
    id: "routine-offer",
    icon: "🔁",
    label: "发现周期性的事，问要不要托管",
    hint: "点「好」才建定时任务",
    prefKey: ROUTINE_OFFER_KEY,
    schedule: { tick: () => routineDiscoveryTick(), everyMs: 15 * MIN, firstMs: MIN },
    onDisable: dropReminders("routine", "托管提议"),
  },
  {
    id: "advance-prep",
    icon: "📋",
    label: "提前准备",
    hint: "会议、出差拜访、快到期的待办、邮件里要办 / 要回的; 前一天 16:00, 只起草不发送",
    prefKey: MEETING_PREP_KEY,
    schedule: { tick: () => advancePrepTick(), everyMs: 15 * MIN, firstMs: 1.5 * MIN },
    onDisable: async () => {
      const n = await cancelPendingPreps();
      return n > 0 ? `已经排上、还没做的 ${n} 条准备任务撤了` : null;
    },
  },
  {
    id: "room-ask",
    icon: "🙋",
    label: "群里 @我 交代的事挂到提醒条",
    hint: "可一键让小鲶先做 / 记成待办",
    prefKey: ROOM_ASK_KEY,
    onRoomEvents: (events) => processRoomMentions(events),
    onDisable: dropReminders("ask", "群里交代的事"),
  },
];

export function abilityEnabled(a: SecretaryAbility): boolean {
  return a.prefKey === null || prefEnabled(a.prefKey);
}

/** 仪表盘开关: 关掉时顺带撤掉它已经排上 / 挂上的。返回给员工看的一句话。 */
export async function setAbilityEnabled(a: SecretaryAbility, on: boolean): Promise<string | null> {
  if (a.prefKey === null) return null;
  setPref(a.prefKey, on);
  if (on || !a.onDisable) return null;
  try {
    return await a.onDisable();
  } catch (e) {
    return `关了, 但撤已排上的事时出错: ${e instanceof Error ? e.message : String(e)}`;
  }
}
