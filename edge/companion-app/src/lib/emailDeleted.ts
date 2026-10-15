/**
 * 删掉的邮件别再冒出来 (10/15 鸿波「在 Windows 上的邮箱, 邮件删掉后, 又回跑出来」)。
 *
 * 删除成功后原来只从当前列表里 filter 掉一下, 有两条路会把它带回来:
 *   1. 跨挂载缓存 (store.listCache) 没更新 —— 切走再切回, 先显示的是删之前那份;
 *   2. 后台轮询 / 刷新在删除**之前**就发出去了 —— Windows 上 IMAP 拉一次列表要好几秒,
 *      它带着旧列表回来, setItems 整份覆盖, 刚删的那封又回到列表里。
 *
 * 所以记一张「刚删掉的」墓碑表 (localStorage, 重启也在), 任何来源的列表进界面前都过一遍。
 * 墓碑按 id 记, 也按 RFC Message-ID 记 —— 同一封信换了来源 / id 迁移后照样认得。
 *
 * 什么时候撤墓碑: 一份**完整的收件箱列表** (不是只看未读, 不是别的文件夹) 里已经没有它,
 * 并且删除已经过去 10 分钟以上 (比任何一轮在途拉取都长) —— 说明服务器那边确实没了。
 * 最长留 30 天, 防表无限长。
 */

export interface DeletedTombstones {
  /** key → 删除时间 (epoch ms)。key 是邮件 id, 或 "mid:" + Message-ID */
  [key: string]: number;
}

interface ListItem {
  id: string;
  message_id?: string;
}

export const TOMBSTONE_SETTLE_MS = 10 * 60 * 1000;
export const TOMBSTONE_MAX_AGE_MS = 30 * 24 * 60 * 60 * 1000;

function midKey(messageId: string | undefined): string | null {
  const m = (messageId ?? "").trim().toLowerCase();
  return m ? `mid:${m}` : null;
}

export function tombstoneKeys(item: ListItem): string[] {
  const keys = item.id ? [item.id] : [];
  const mk = midKey(item.message_id);
  if (mk) keys.push(mk);
  return keys;
}

export function isDeleted(item: ListItem, tombs: DeletedTombstones): boolean {
  return tombstoneKeys(item).some((k) => k in tombs);
}

export function hideDeleted<T extends ListItem>(list: T[], tombs: DeletedTombstones | undefined): T[] {
  if (!tombs || Object.keys(tombs).length === 0) return list;
  return list.filter((it) => !isDeleted(it, tombs));
}

/**
 * 拿一份刚拉回来的列表决定哪些墓碑可以撤。
 * complete=false (只看未读 / 别的文件夹 / 列表被截断) 时只做 30 天过期, 不按"不在列表里"撤。
 */
export function pruneTombstones(
  tombs: DeletedTombstones,
  fresh: ListItem[],
  complete: boolean,
  now: number = Date.now(),
): DeletedTombstones {
  const present = new Set<string>();
  if (complete) for (const it of fresh) for (const k of tombstoneKeys(it)) present.add(k);
  const out: DeletedTombstones = {};
  for (const [k, at] of Object.entries(tombs)) {
    const age = now - at;
    if (age > TOMBSTONE_MAX_AGE_MS) continue;
    if (complete && age > TOMBSTONE_SETTLE_MS && !present.has(k)) continue;
    out[k] = at;
  }
  return out;
}
