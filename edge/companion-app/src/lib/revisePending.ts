/**
 * 群里一起改文档的待办 (11/11 鸿波「都做」第 3、4 条): 请我改、我还没交回的, 放进「协同事项」, 也算导航红点;
 * 今天 / 明天截止还没交的, 在这台电脑上弹一次系统通知。
 *
 * 截止提醒只在被请的人自己电脑上弹, 不由发起人那边往群里自动发「催一下」——
 * 群里的消息都得员工自己点了才发 (docs/CENTRAL-EDGE-DATA-BOUNDARY.md 第 4 条), 发起人要催还是点卡片上的「催一下」。
 * 纯函数在这里, 取数和弹通知挂在 colleagueRoomsStore 的轮询上 (不另开定时器)。
 */
import { QUIET_HOURS_END, QUIET_HOURS_START } from "./triggers";
import type { RevisePendingItem } from "./tauri_room_revise";

export const REVISE_PENDING_EVERY_MS = 60_000;
const NOTIFIED_KEY = "catfish.revise.dueNotified";

/** 截止还剩几天的说法 */
export function dueLabel(days: number | null): string {
  if (days === null) return "不限时";
  if (days < 0) return `已过截止 ${-days} 天`;
  if (days === 0) return "今天截止";
  if (days === 1) return "明天截止";
  return `${days} 天后截止`;
}

/** 这次该弹哪几条: 今天 / 明天截止、还没弹过的; 夜里 (22 点到 7 点) 先不弹, 早上再弹。 */
export function dueToNotify(items: RevisePendingItem[], notified: Set<string>, now: Date): RevisePendingItem[] {
  const h = now.getHours();
  if (h >= QUIET_HOURS_START || h < QUIET_HOURS_END) return [];
  return items.filter((x) => x.days_left !== null && x.days_left >= 0 && x.days_left <= 1 && !notified.has(noticeKey(x)));
}

export function noticeKey(x: RevisePendingItem): string {
  return `${x.rev_id}:${x.deadline}:${x.days_left}`;
}

export function loadNotified(): Set<string> {
  try {
    return new Set(JSON.parse(localStorage.getItem(NOTIFIED_KEY) ?? "[]") as string[]);
  } catch {
    return new Set();
  }
}

export function saveNotified(s: Set<string>): void {
  try {
    localStorage.setItem(NOTIFIED_KEY, JSON.stringify([...s].slice(-200)));
  } catch {
    /* 存不下就下次再弹一次, 不影响 */
  }
}

export function noticeText(x: RevisePendingItem): [string, string] {
  return [`《${x.title}》${dueLabel(x.days_left)}`, `${x.owner_name} 请你修改 (群「${x.room_name}」), 你还没交回 —— 在「协同」页的待办里点「交回」`];
}
