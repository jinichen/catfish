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
 *     (push_history dedup window 24h 内仍生效, 但主动闲聊 BL-E13 跳过)
 *
 * 不持久化的:
 *   - 单封邮件 body cache (隐私, 跟 Rust 端红线一致 — 不缓存邮件正文)
 *   - 评级历史 (审计用, 留 Rust scheduler 那边)
 */

import { create } from "zustand";

import { emailIdMigrationMap, emailListCacheLoad, emailListCacheSave, emailUrgencyMap as rustUrgencyMap } from "../lib/tauri";
import { hideDeleted, pruneTombstones, tombstoneKeys, type DeletedTombstones } from "../lib/emailDeleted";

/** 9/27: 照改名表把以 id 为键的本地记录改名 (Rust 侧缓存由 Rust 自己改)。 */
export function renameIds<T>(record: Record<string, T>, mapping: Record<string, string>): Record<string, T> {
  const out: Record<string, T> = {};
  for (const [id, value] of Object.entries(record)) {
    const next = mapping[id] ?? id;
    if (!(next in out) || next === id) out[next] = value;
  }
  return out;
}

const LS_URGENCY_KEY = "catfish.email.urgency.v1";
const LS_READ_KEY = "catfish.email.read.v1";
const LS_DELETED_KEY = "catfish.email.deleted.v1";   // 10/15 删掉的邮件墓碑 (lib/emailDeleted.ts)

function loadPersistedDeleted(): DeletedTombstones {
  try {
    const raw = localStorage.getItem(LS_DELETED_KEY);
    const parsed = raw ? JSON.parse(raw) : null;
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) return parsed as DeletedTombstones;
  } catch {
    /* ignore corrupt */
  }
  return {};
}

function savePersistedDeleted(t: DeletedTombstones): void {
  try {
    localStorage.setItem(LS_DELETED_KEY, JSON.stringify(t));
  } catch {
    /* ignore */
  }
}

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
  /** 标某封已读 — 加进 readIds, 持久化. 主动闲聊路径检查这个 set 跳过. */
  markRead: (id: string) => void;
  /** 批量标已读 (e.g. 员工在 EmailTab 选多封) */
  markReadBulk: (ids: string[]) => void;
  /** 检查某 id 是否被标过 — 主动闲聊 / 通知 dedup 用 */
  isRead: (id: string) => boolean;
  /** 清掉 readIds — 员工想 reset 时 (不常用, 隐私逃生) */
  clearReadHistory: () => void;

  // ── 8/21 治本配套: 列表跨挂载缓存 (纯内存, 故意不 persist) ──────────
  //
  // EmailTab 是条件渲染 (App.tsx:268 `activeTab === "email" && <EmailTab/>`),
  // 切走即卸载, items 这些 useState 全销毁 —— 切回就要重拉重等。这是
  // 「切回邮件页要等很久」的前端一半 (后端一半是 AS 全量抽取, 已由
  // catfish-email 的 emlx 索引治掉)。
  //
  // 放 store 里就跨挂载存活: 切回瞬间先显缓存, loadList 后台刷新。
  // EmailTab.tsx:393 `loading && items.length === 0` 才显示"加载中",
  // 所以有缓存时刷新是静默的。
  //
  // **故意不进 localStorage**: 500 封元数据 ~200KB, 会挤占 urgency 那份配额。
  // 10/31: 「冷启动后端毫秒级」的前提 9/27 就没了 (emlx 索引撤掉, 回到 AppleScript,
  // 439 封 19 秒), 所以改成也落到 ~/.catfish/email_list_cache.json (Rust 读写),
  // 重启后先显示上次的列表 —— 见 hydrateListCache。
  listCache: {
    items: unknown[];
    sentItems: unknown[];
    accounts: unknown[];
    savedAt: number;
  } | null;
  setListCache: (c: { items: unknown[]; sentItems: unknown[]; accounts: unknown[] }) => void;
  /** 10/31: 邮件页挂载时把上次落盘的列表读进来 (内存里已经有就不动); 读不到就算了。 */
  hydrateListCache: () => Promise<void>;

  // ── 8/21 分诊升级: id → {action, deadline} ──────────────────────────
  //
  // **merge, 不整份覆盖** —— 8/15 永动机的三处修复之一就是 setUrgencyMap
  // 从覆盖改 merge (Rust 缓存裁剪后返回的小 map 覆盖掉本地大 map → unrated
  // 变多 → 再评 → 循环)。新 map 生下来就是 merge, 别再踩一遍。
  // 纯内存不进 localStorage: Rust 侧有 email_action.json persist, 重启后
  // classify_now 全 cache 命中会把完整 actions 返回来。
  actionMap: Record<string, { action: string; deadline?: string }>;
  mergeActionMap: (m: Record<string, { action: string; deadline?: string }>) => void;

  // ── 10/15 删掉的邮件墓碑 (见 lib/emailDeleted.ts) ──────────────────────
  deleted: DeletedTombstones;
  /** 删除成功后调: 记墓碑 + 同步从跨挂载缓存里拿掉 */
  markDeleted: (item: { id: string; message_id?: string }) => void;
  /** 一份刚拉回的列表来了: 撤掉服务器已确认没了的墓碑 (complete 时), 返回藏掉墓碑后的列表 */
  acceptList: <T extends { id: string; message_id?: string }>(fresh: T[], complete: boolean) => T[];
}

export const useEmailStore = create<EmailState>((set, get) => ({
  listCache: null,
  hydrateListCache: async () => {
    try {
      const raw = await emailListCacheLoad();
      if (!raw || get().listCache) return;
      const c = JSON.parse(raw) as { items?: unknown[]; sentItems?: unknown[]; accounts?: unknown[]; savedAt?: number };
      if (!Array.isArray(c.items)) return;
      set({ listCache: { items: c.items, sentItems: c.sentItems ?? [], accounts: c.accounts ?? [], savedAt: c.savedAt ?? 0 } });
    } catch {
      // 没装 Tauri (测试) / 文件坏了 —— 不影响实时拉取
    }
  },
  setListCache: (c) => {
    const listCache = { ...c, savedAt: Date.now() };
    set({ listCache });
    // 10/31: 也落盘, 重启后先显示这份 (见 hydrateListCache)
    void emailListCacheSave(JSON.stringify(listCache)).catch(() => {});
  },
  actionMap: {},
  mergeActionMap: (m) => set({ actionMap: { ...get().actionMap, ...m } }),
  deleted: loadPersistedDeleted(),
  markDeleted: (item) => {
    const now = Date.now();
    const next = { ...get().deleted };
    for (const k of tombstoneKeys(item)) next[k] = now;
    const cache = get().listCache;
    set({
      deleted: next,
      listCache: cache
        ? { ...cache, items: cache.items.filter((it) => (it as { id?: string }).id !== item.id) }
        : cache,
    });
    savePersistedDeleted(next);
  },
  acceptList: (fresh, complete) => {
    const cur = get().deleted;
    if (Object.keys(cur).length === 0) return fresh;
    const next = pruneTombstones(cur, fresh, complete);
    if (Object.keys(next).length !== Object.keys(cur).length) {
      set({ deleted: next });
      savePersistedDeleted(next);
    }
    return hideDeleted(fresh, next);
  },
  urgencyMap: loadPersistedUrgency(),
  readIds: loadPersistedRead(),
  lastSyncedAt: null,

  reconcileFromRust: async () => {
    try {
      const rust = await rustUrgencyMap();
      // 9/27: 旧 id → 新 id。已读记录只存在前端, 不改名的话迁移后这些邮件会
      // 重新触发主动提醒; 评级镜像顺手也改, 免得本地留一堆永远用不上的旧键。
      // 改名表拿不到 (老 Companion 后端 / 调用失败) 不能拖垮评级同步
      let mapping: Record<string, string> = {};
      try {
        mapping = (await emailIdMigrationMap()) ?? {};
      } catch {
        /* ignore */
      }
      if (Object.keys(mapping).length > 0) {
        const read = get().readIds;
        const renamed = new Set(Array.from(read, (id) => mapping[id] ?? id));
        if (Array.from(read).some((id) => id in mapping)) {
          set({ readIds: renamed });
          savePersistedRead(renamed);
        }
        set({ urgencyMap: renameIds(get().urgencyMap, mapping) });
      }
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
    // 8/15: 从"直接覆盖"改成 merge。
    //
    // 原注释说「emailClassifyNow 返完整 map」—— 这个前提**不成立**:
    // Rust 侧 urgency_cache 有上限, 超了会丢一半 (email_scheduler.rs
    // URGENCY_CACHE_MAX)。当时上限 200 而列表能拉 500, 于是返回的 map
    // 常常比本地已有的还少, 覆盖下去等于**把已评过的记录抹掉**,
    // 连 localStorage 一起抹 —— 前端下次再把它们当"没评过"重评一遍。
    // 这是 8/15 那个评级永动机的第三处燃料。
    //
    // merge 的取舍: 新值覆盖同 id 的旧值 (后端是权威), 但后端没返的 id
    // 保留本地的。代价是已归档邮件的评级会一直留在 localStorage,
    // 那点体积换"不会重烧 token"是划算的。
    const merged = { ...get().urgencyMap, ...map };
    set({ urgencyMap: merged, lastSyncedAt: Date.now() });
    savePersistedUrgency(merged);
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
