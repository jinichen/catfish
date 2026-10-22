/**
 * 员工点过「保持关联」的关系 (10/23): 这些不再算「没标类型」, 不会一直挂在整理任务里。
 *
 * key = `${条目 rel_path}::${对方名字}`。存本机 localStorage —— 知识库本身就在本机, 不跨设备。
 * 没写进条目 frontmatter: 写入侧 (buildConfirmedWikiContent) 按已知字段重建 frontmatter,
 * 自定义字段会被丢掉。读写失败 (隐私模式等) 就当没有, 任务照常显示, 不影响别的。
 */
import { useSyncExternalStore } from "react";

const KEY = "catfish.wiki.untypedKept.v1";
const EVENT = "catfish:wiki-untyped-kept";

export const keptKey = (relPath: string, name: string) => `${relPath}::${name.trim()}`;

function read(): string[] {
  try {
    const raw = localStorage.getItem(KEY);
    const arr = raw ? JSON.parse(raw) : [];
    return Array.isArray(arr) ? arr.filter((x) => typeof x === "string") : [];
  } catch {
    return [];
  }
}

let snapshot: Set<string> = new Set(read());

export function getKeptUntyped(): Set<string> {
  return snapshot;
}

export function addKeptUntyped(keys: string[]): void {
  if (keys.length === 0) return;
  snapshot = new Set([...snapshot, ...keys]);
  try {
    localStorage.setItem(KEY, JSON.stringify([...snapshot]));
  } catch {
    // 存不下也继续: 本次会话里照样不显示
  }
  window.dispatchEvent(new Event(EVENT));
}

function subscribe(callback: () => void): () => void {
  window.addEventListener(EVENT, callback);
  return () => window.removeEventListener(EVENT, callback);
}

export function useKeptUntyped(): Set<string> {
  return useSyncExternalStore(subscribe, getKeptUntyped, getKeptUntyped);
}
