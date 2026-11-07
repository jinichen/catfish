/** 急邮件到达 → 提醒条 (11/07)。
 *
 * Rust 邮件调度评出「急」的新邮件 → emit `catfish:email-urgent` (App.tsx 监听, 调这里)。
 * 以前这里先让 LLM 写一句「张三那封紧的来了…」, 再 startProactiveChat 塞进当前打开的对话、
 * 切到工作台 —— 跟待办提醒同样的毛病 (污染对话、拽走人)。现在挂到输入框上面的提醒条,
 * 字是事实 (几封、谁发的、什么主题), 点「让小鲶看看」才新开对话逐封过。
 *
 * 系统通知 Rust 那边已经发了 (email_notify.send_notification, 24h 去重), 这里不重复发;
 * 人不在 Companion 里时记一笔, 切回来落到工作台 (提醒条在那)。
 */
import { useAgentStore } from "../store/agent";
import { useEmailStore } from "../store/email";
import { useRemindersStore } from "../store/reminders";
import { urgentEmailReminder } from "./reminders";

export interface UrgentEmailPayload {
  count: number;
  starter: string;
  ids: string[];
  items?: Array<{ subject: string; sender: string }>;
}

/** 挂上提醒条返 true; 全都读过了 / 空的 / 已挂着 返 false */
export function handleUrgentEmail(payload: UrgentEmailPayload, now: Date = new Date()): boolean {
  const { count, ids = [], items = [] } = payload;
  if (!count) return false;

  // BL-COMPANION-EMAIL-DIGEST-STEP5 sub-task 2 (5/20): 员工已点开过的急邮件不再叫醒.
  // Rust scheduler 24h push_history dedup 只防"刚 push 过", 不知道员工是否真读了;
  // 前端 useEmailStore.readIds (localStorage 持久化) 记录员工真读过的 id, 这里 filter.
  // ids 和 items 是 Rust 按同一顺序 zip 出来的, 按下标一起筛。
  const readSet = useEmailStore.getState().readIds;
  const keep = ids.length > 0 ? ids.map((id, i) => ({ id, item: items[i] })).filter((x) => !readSet.has(x.id)) : [];
  if (ids.length > 0 && keep.length === 0) {
    console.log(`[email-urgent] ${ids.length} 封急邮件员工都已读过, 跳过`);
    return false;
  }
  const keptIds = keep.map((x) => x.id);
  const keptItems = ids.length > 0
    ? keep.map((x) => x.item).filter((x): x is { subject: string; sender: string } => !!x)
    : items;

  const agentName = useAgentStore.getState().name || "小鲶";
  const reminder = urgentEmailReminder(keptItems, keptIds, now, agentName);
  if (!reminder) return false;
  const store = useRemindersStore.getState();
  if (!store.add(reminder, now.getTime())) return false;
  if (typeof document !== "undefined" && !document.hasFocus()) store.noteNotified(now.getTime());
  return true;
}
