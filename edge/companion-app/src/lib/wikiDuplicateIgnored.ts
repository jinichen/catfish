/**
 * 「可能重复」里点过「不是同一个」的组 (11/08): 记住, 以后不再提示。
 * key = 组里各条 rel_path 排序后用 | 连起来。存本机 localStorage —— 知识库本身就在本机, 不跨设备。
 * 跟 wikiUntypedKept 同一个做法。
 */
import { useSyncExternalStore } from "react";

const KEY = "catfish.wiki.duplicateIgnored.v1";
const EVENT = "catfish:wiki-duplicate-ignored";

export const ignoredKey = (paths: readonly string[]) => [...paths].sort().join("|");

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

export function getIgnoredDuplicates(): Set<string> {
  return snapshot;
}

export function addIgnoredDuplicate(paths: readonly string[]): void {
  if (paths.length < 2) return;
  snapshot = new Set([...snapshot, ignoredKey(paths)]);
  try {
    localStorage.setItem(KEY, JSON.stringify([...snapshot]));
  } catch {
    // 存不下也继续: 本次会话里照样不提示
  }
  window.dispatchEvent(new Event(EVENT));
}

function subscribe(callback: () => void): () => void {
  window.addEventListener(EVENT, callback);
  return () => window.removeEventListener(EVENT, callback);
}

export function useIgnoredDuplicates(): Set<string> {
  return useSyncExternalStore(subscribe, getIgnoredDuplicates, getIgnoredDuplicates);
}
