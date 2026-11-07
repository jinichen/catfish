/** 提醒条的状态 (11/07) —— 挂着哪些提醒、哪些在「稍后」、哪些今天处理过。
 *
 * 为什么要持久化: 提醒是「到点了该做的事」, 员工可能看到通知后才打开 Companion,
 * 或者中途重启了 —— 提醒不能跟着进程没了。localStorage 读写全包 try/catch,
 * 读不到 / 写不进 (隐私模式、配额满) 就只在这次进程里生效, 不报错。
 *
 * 「处理过」(handled) 单独记: 点了主按钮 / ✕ 的今天不再出, 标完成的 30 天不再出。
 * 不记的话, 任务库 10 分钟缓存里那条还是 pending, 下一轮信号又把它挂回来。
 *
 * 文案和排序在 lib/reminders.ts (纯函数), 这里只管增删改和落盘。
 */
import { useEffect, useMemo, useState } from "react";
import { create } from "zustand";

import { nextLocalMidnight, visibleReminders, type Reminder } from "../lib/reminders";
import { PROACTIVE_DISMISS_TS_KEY } from "../lib/triggers";

const STORAGE_KEY = "catfish:reminders:v1";
/** 标完成的这么久内不再出 (任务库那边已经关了, 这只是防 10 分钟缓存窗口里又挂回来) */
const DONE_SUPPRESS_MS = 30 * 24 * 3600_000;
/** 通知发出后多久内切回 Companion 算「是点通知回来的」 */
const NOTIFY_RETURN_WINDOW_MS = 30 * 60_000;

interface Persisted {
  items: Reminder[];
  handled: Record<string, number>;
  notifiedAt: number | null;
}

interface RemindersState extends Persisted {
  /** 挂一条提醒。已挂着 / 稍后中 / 处理过的同 id 不动, 返 false */
  add: (r: Reminder, now?: number) => boolean;
  snooze: (id: string, until: number) => void;
  /** ✕: 今天不再提醒这条; 同时喂给 triggers 的 30 分钟静默 */
  dismiss: (id: string, now?: number) => void;
  /** 点了主按钮 (已新开对话去处理): 今天不再提醒这条 */
  markStarted: (id: string, now?: number) => void;
  /** 「已完成」写回任务库成功之后 */
  markDone: (id: string, now?: number) => void;
  /** 发了系统通知 (窗口不在前台时) */
  noteNotified: (now?: number) => void;
  /** 窗口重新获焦: 刚发过通知、还有提醒挂着 → true (调用方切到工作台), 只认一次 */
  consumeNotifyReturn: (now?: number) => boolean;
  /** 信号检测时跳过这些 dedupe_key: 已挂着的、稍后中的、今天处理过的 */
  activeKeys: (now?: number) => Set<string>;
}

function load(): Persisted {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) {
      const p = JSON.parse(raw) as Partial<Persisted>;
      return prune({
        items: Array.isArray(p.items) ? p.items.filter((r) => r && typeof r.id === "string") : [],
        handled: p.handled && typeof p.handled === "object" ? p.handled : {},
        notifiedAt: typeof p.notifiedAt === "number" ? p.notifiedAt : null,
      }, Date.now());
    }
  } catch {
    /* 坏数据 / 读不了: 从空开始 */
  }
  return { items: [], handled: {}, notifiedAt: null };
}

/** 过期的提醒、到期的 handled 记录清掉, 免得 localStorage 越攒越多 */
function prune(p: Persisted, now: number): Persisted {
  const handled: Record<string, number> = {};
  for (const [k, until] of Object.entries(p.handled)) {
    if (typeof until === "number" && until > now) handled[k] = until;
  }
  return { ...p, items: p.items.filter((r) => r.expiresAt > now), handled };
}

function save(p: Persisted): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(p));
  } catch {
    /* 写不进就只在这次进程里生效 */
  }
}

export const useRemindersStore = create<RemindersState>((set, get) => {
  const commit = (next: Persisted) => {
    save(next);
    set(next);
  };
  const snapshot = (): Persisted => {
    const { items, handled, notifiedAt } = get();
    return { items, handled, notifiedAt };
  };
  const handle = (id: string, until: number, now: number) => {
    const s = prune(snapshot(), now);
    commit({ ...s, items: s.items.filter((r) => r.id !== id), handled: { ...s.handled, [id]: until } });
  };
  return {
    ...load(),
    add: (r, now = Date.now()) => {
      const s = prune(snapshot(), now);
      if (r.expiresAt <= now) return false;
      if ((s.handled[r.id] ?? 0) > now) return false;
      if (s.items.some((x) => x.id === r.id)) return false;
      commit({ ...s, items: [...s.items, r] });
      return true;
    },
    snooze: (id, until) => {
      const s = snapshot();
      commit({ ...s, items: s.items.map((r) => (r.id === id ? { ...r, snoozedUntil: until } : r)) });
    },
    dismiss: (id, now = Date.now()) => {
      try {
        localStorage.setItem(PROACTIVE_DISMISS_TS_KEY, String(now));
      } catch {
        /* 30 分钟静默这次就不生效, 不影响关掉这条 */
      }
      handle(id, nextLocalMidnight(now), now);
    },
    markStarted: (id, now = Date.now()) => handle(id, nextLocalMidnight(now), now),
    markDone: (id, now = Date.now()) => handle(id, now + DONE_SUPPRESS_MS, now),
    noteNotified: (now = Date.now()) => commit({ ...snapshot(), notifiedAt: now }),
    consumeNotifyReturn: (now = Date.now()) => {
      const s = snapshot();
      if (!s.notifiedAt) return false;
      const fresh = now - s.notifiedAt < NOTIFY_RETURN_WINDOW_MS;
      commit({ ...s, notifiedAt: null });
      return fresh && visibleReminders(s.items, now).length > 0;
    },
    activeKeys: (now = Date.now()) => {
      const s = prune(snapshot(), now);
      return new Set([...s.items.map((r) => r.id), ...Object.keys(s.handled)]);
    },
  };
});

/** 测试用: 清空并按 localStorage 重新加载 (模拟重启) */
export function reloadRemindersForTest(): void {
  useRemindersStore.setState(load());
}

/** 现在该显示的提醒 (最急的在前)。每 30 秒重算一次 —— 「稍后」到点了要自己冒出来。 */
export function useVisibleReminders(): Reminder[] {
  const items = useRemindersStore((s) => s.items);
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = window.setInterval(() => setNow(Date.now()), 30_000);
    return () => window.clearInterval(t);
  }, []);
  // items 变了 (新挂 / 处理掉) 也要按当前时刻算, 不能用 30 秒前的 now
  return useMemo(() => visibleReminders(items, Math.max(now, Date.now())), [items, now]);
}
