/** Email urgency state (BL-COMPANION-EMAIL-DIGEST-STEP5 sub-task 1, 5/20 鸿波).
 *
 * 前端 urgency 评级镜像 + localStorage 持久化 + 标已读 push dedup.
 *
 * 现状 (5/20 之前):
 *   - Rust scheduler ~/.catfish/email_urgency.json 持久化评级 (v2 sub-task 2)
 *   - Companion 启动 → Rust load disk → 前端调 emailUrgencyMap() 拉
 *   - 问题: 启动初期 Rust 还没 load / 前端切 tab 重新 mount, urgencyMap state
 *     先空一下 → badge 闪烁 / 空白
 *
 * step5 (本提交):
 *   - localStorage 镜像 urgency map → 启动立即从 localStorage 取, badge 即出
 *   - 后台拉 Rust 真 map 后 reconcile (Rust > localStorage 权威)
 *   - emailMarkRead 后, 调 markUrgencyRead(id) — 让该 id 不再触发主动通知
 *     (push_history dedup window 24h 内仍生效, 但桌宠主动闲聊 BL-E13 跳过)
 *
 * 不持久化的:
 *   - 单封邮件 body cache (隐私, 跟 Rust 端红线一致 — 不缓存邮件正文)
 *   - 评级历史 (审计用, 留 Rust scheduler 那边)
 */

import { create } from "zustand";

import { emailUrgencyMap as rustUrgencyMap } from "../lib/tauri";

const LS_URGENCY_KEY = "catfish.email.urgency.v1";
const LS_READ_KEY = "catfish.email.read.v1";

/** localStorage 镜像 schema — {map: {id: "急"|"中"|"低"}, savedAt: epoch_ms} */
interface PersistedUrgency {
  map: Record<string, string>;
  savedAt: number;
}

/** 标过已读的 id set, push 通知去重. */
interface PersistedRead {
  ids: string[];
  savedAt: number;
}

function loadPersistedUrgency(): Record<string, string> {
  try {
    const raw = localStorage.getItem(LS_URGENCY_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw) as PersistedUrgency;
    if (parsed && typeof parsed === "object" && parsed.map && typeof parsed.map === "object") {
      return parsed.map;
    }
  } catch {
    /* ignore corrupt */
  }
  return {};
}

function savePersistedUrgency(map: Record<string, string>): void {
  try {
    const payload: PersistedUrgency = { map, savedAt: Date.now() };
    localStorage.setItem(LS_URGENCY_KEY, JSON.stringify(payload));
  } catch {
    /* localStorage 写满 / 隐私模式 — 静默 */
  }
}

function loadPersistedRead(): Set<string> {
  try {
    const raw = localStorage.getItem(LS_READ_KEY);
    if (!raw) return new Set();
    const parsed = JSON.parse(raw) as PersistedRead;
    if (parsed && Array.isArray(parsed.ids)) {
      return new Set(parsed.ids);
    }
  } catch {
    /* ignore */
  }
  return new Set();
}

function savePersistedRead(ids: Set<string>): void {
  try {
    // 防 set 越涨越大: 上限 1000 id (LRU 简化 — 直接截最近的). 老 id 已经
    // 不在 Mail.app inbox 通常 (用户已删 / archive), Rust scheduler 重启重建.
    const arr = Array.from(ids);
    const capped = arr.length > 1000 ? arr.slice(-1000) : arr;
    const payload: PersistedRead = { ids: capped, savedAt: Date.now() };
    localStorage.setItem(LS_READ_KEY, JSON.stringify(payload));
  } catch {
    /* ignore */
  }
}

interface EmailState {
  /** id → "急" | "中" | "低" — 启动时从 localStorage 初始化, 后台 reconcile Rust */
  urgencyMap: Record<string, string>;
  /** 员工标过已读的 id set — push 通知不再触发主动闲聊 */
  readIds: Set<string>;
  /** Rust map 上次拉成功的时间 (epoch ms), 用来决定要不要重拉. null = 没拉过 */
  lastSyncedAt: number | null;

  /** 后台拉 Rust map, 跟 local 合并 (Rust > local 权威). 返合并后 map. */
  reconcileFromRust: () => Promise<Record<string, string>>;
  /** 前端主动评级一批后写入 (e.g. emailClassifyNow 返完整 map). 同步 localStorage. */
  setUrgencyMap: (map: Record<string, string>) => void;
  /** 标某封已读 — 加进 readIds, 持久化. 桌宠主动闲聊路径检查这个 set 跳过. */
  markRead: (id: string) => void;
  /** 批量标已读 (e.g. 员工在 EmailTab 选多封) */
  markReadBulk: (ids: string[]) => void;
  /** 检查某 id 是否被标过 — 桌宠主动闲聊 / 通知 dedup 用 */
  isRead: (id: string) => boolean;
  /** 清掉 readIds — 员工想 reset 时 (不常用, 隐私逃生) */
  clearReadHistory: () => void;
}

export const useEmailStore = create<EmailState>((set, get) => ({
  urgencyMap: loadPersistedUrgency(),
  readIds: loadPersistedRead(),
  lastSyncedAt: null,

  reconcileFromRust: async () => {
    try {
      const rust = await rustUrgencyMap();
      // Rust 是权威 — local 里有但 Rust 没的 id 可能已 archive, 也保留
      // (Companion 还在显这封邮件就还有用). 简单做法: union, Rust 值覆盖 local.
      const merged = { ...get().urgencyMap, ...rust };
      set({ urgencyMap: merged, lastSyncedAt: Date.now() });
      savePersistedUrgency(merged);
      return merged;
    } catch {
      // Rust 调用挂 (启动初期 / scheduler 没起) → 保留 local
      return get().urgencyMap;
    }
  },

  setUrgencyMap: (map) => {
    // emailClassifyNow 返完整 map, 直接覆盖
    set({ urgencyMap: map, lastSyncedAt: Date.now() });
    savePersistedUrgency(map);
  },

  markRead: (id) => {
    if (!id) return;
    const next = new Set(get().readIds);
    if (next.has(id)) return;  // 已标过, 不重复 trigger persist
    next.add(id);
    set({ readIds: next });
    savePersistedRead(next);
  },

  markReadBulk: (ids) => {
    const next = new Set(get().readIds);
    let changed = false;
    for (const id of ids) {
      if (id && !next.has(id)) {
        next.add(id);
        changed = true;
      }
    }
    if (changed) {
      set({ readIds: next });
      savePersistedRead(next);
    }
  },

  isRead: (id) => get().readIds.has(id),

  clearReadHistory: () => {
    const empty = new Set<string>();
    set({ readIds: empty });
    savePersistedRead(empty);
  },
}));
