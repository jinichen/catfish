/**
 * 一起填表的前端状态 (10/17, docs/FORM-COLLECT-PLAN.md)。
 *
 * 订阅即开始轮询: 每 20 秒 `form_sync` 取一次邮筒 (中央取走即清空, 落进本机账本), 再 `form_list` 刷列表。
 * 导航常驻订阅 (TabBar), 所以员工不进「协同」页也能看到红点、收到系统通知。
 * 收到新邀请 / 被催 → 系统通知 (「请同事帮忙」那边只有红点; 填表跨好几天, 只靠红点会被忘掉)。
 */
import { useSyncExternalStore } from "react";

import { sendNotification } from "./tauri_app";
import { formList, formSync, type FormList, type SyncResult } from "./tauri_forms";

export const FORMS_POLL_INTERVAL_MS = 20_000;

export interface FormsState extends FormList {
  loaded: boolean;
  /** 最近一次取邮筒失败的原因 (没登录 / 中央连不上); 成功一次就清掉 */
  syncError: string | null;
}

let state: FormsState = { incoming: [], outgoing: [], loaded: false, syncError: null };
const listeners = new Set<() => void>();
let timer: ReturnType<typeof setInterval> | null = null;
let inflight: Promise<void> | null = null;

function emit(next: FormsState) {
  state = next;
  for (const l of listeners) l();
}

export function notifyFor(r: SyncResult): void {
  for (const inv of r.new_invites) {
    void sendNotification("收到一张要填的表", `${inv.from} 请你填《${inv.title}》, 在「协同」页里填`).catch(() => {});
  }
  if (r.remind > 0) {
    void sendNotification("有表在催你", `${r.remind} 张表的发起人催你填了, 在「协同」页里看`).catch(() => {});
  }
}

/** 取一次邮筒 + 刷列表。并发调用合并成一次。 */
export function refreshForms(): Promise<void> {
  if (inflight) return inflight;
  inflight = (async () => {
    let syncError: string | null = null;
    try {
      notifyFor(await formSync());
    } catch (e) {
      syncError = e instanceof Error ? e.message : String(e);
    }
    try {
      const list = await formList();
      emit({ ...list, loaded: true, syncError });
    } catch (e) {
      emit({ ...state, loaded: true, syncError: syncError ?? (e instanceof Error ? e.message : String(e)) });
    }
  })().finally(() => {
    inflight = null;
  });
  return inflight;
}

export function subscribe(cb: () => void): () => void {
  listeners.add(cb);
  if (!timer) {
    void refreshForms();
    timer = setInterval(() => void refreshForms(), FORMS_POLL_INTERVAL_MS);
  }
  return () => {
    listeners.delete(cb);
    if (listeners.size === 0 && timer) {
      clearInterval(timer);
      timer = null;
    }
  };
}

export const getSnapshot = (): FormsState => state;

export function useForms(): FormsState {
  return useSyncExternalStore(subscribe, getSnapshot);
}

/** 导航红点: 收到的表里还要我动手的 (待填 / 草稿, 且没结束)。 */
export function pendingFormCount(s: FormsState = state): number {
  return s.incoming.filter((f) => !f.closed && (f.state === "待填" || f.state === "草稿")).length;
}

/** 测试用: 回到初始状态。 */
export function __resetFormsStore(): void {
  if (timer) clearInterval(timer);
  timer = null;
  inflight = null;
  listeners.clear();
  state = { incoming: [], outgoing: [], loaded: false, syncError: null };
}
