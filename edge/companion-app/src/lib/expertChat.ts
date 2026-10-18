/**
 * 工作台「找谁」(10/18, docs/EXPERT-PACK-PLAN.md §3.3)。
 *
 * 选了专家, 这个会话的请求走 hermes 的 `/p/<专家 profile>/`; 换人 = 新开一个会话 (不把两个人的对话混在一起)。
 * 记住每个会话是跟谁聊的 (localStorage, 只是本机的便利 —— 丢了就按小鲶打开, 不影响数据), 从侧栏点回来接着跟同一个专家聊。
 */
import { useSyncExternalStore } from "react";

import { expertChatList, type ChatExpert } from "./tauri_experts";

const MAP_KEY = "catfish.sessionExpert";

interface State {
  experts: ChatExpert[];
  selected: string | null;
}

let state: State = { experts: [], selected: null };
const listeners = new Set<() => void>();

function emit(next: State) {
  state = next;
  for (const l of listeners) l();
}

function readMap(): Record<string, string> {
  try {
    return JSON.parse(localStorage.getItem(MAP_KEY) || "{}") as Record<string, string>;
  } catch {
    return {};
  }
}

function writeMap(m: Record<string, string>) {
  try {
    localStorage.setItem(MAP_KEY, JSON.stringify(m));
  } catch {
    /* 隐私模式 / 存储满: 只是少了「点回来接着跟专家聊」, 不影响别的 */
  }
}

export async function refreshExperts(): Promise<void> {
  try {
    const experts = await expertChatList();
    const selected = state.selected && experts.some((e) => e.profileId === state.selected) ? state.selected : null;
    emit({ experts, selected });
  } catch {
    /* tool-bridge 没起来: 保留上次的列表 */
  }
}

export function selectExpert(profileId: string | null): void {
  emit({ ...state, selected: profileId });
}

/** 新会话刚建好 (ensureSessionId): 记下是跟谁聊的。 */
export function noteSessionCreated(sessionId: string): void {
  if (!state.selected) return;
  writeMap({ ...readMap(), [sessionId]: state.selected });
}

/** 从侧栏打开一个旧会话: 切回当时聊的那个人 (没记录 = 小鲶)。 */
export function noteSessionLoaded(sessionId: string): void {
  emit({ ...state, selected: readMap()[sessionId] ?? null });
}

/** 发送时用: 选中的专家 (profile + 固定模型); 没选 / 选了个已经不在的 → null 走小鲶。 */
export function currentExpertRoute(): { profile: string; fixedModel: string | null; name: string } | null {
  if (!state.selected) return null;
  const e = state.experts.find((x) => x.profileId === state.selected);
  return { profile: state.selected, fixedModel: e?.fixedModel ?? null, name: e?.name ?? state.selected };
}

export function subscribe(cb: () => void): () => void {
  listeners.add(cb);
  if (listeners.size === 1) void refreshExperts();
  return () => listeners.delete(cb);
}

export const getSnapshot = (): State => state;

export function useExpertChat(): State {
  return useSyncExternalStore(subscribe, getSnapshot);
}

export function __resetExpertChat(): void {
  listeners.clear();
  state = { experts: [], selected: null };
}
