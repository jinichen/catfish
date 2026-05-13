/** BL-LEAN-SESSION (5/13 鸿波拍板 "客户无法跑命令行") — 教学模式 toggle.
 *
 * 取代 CATFISH_LEAN_INJECT shell env. Companion 用户点 toggle 即开/关.
 *
 * 开启时:
 *   - 这个 toggle 状态进 zustand store
 *   - lib/chat.ts 发请求时带 X-Catfish-Teaching-Mode: 1 header
 *   - gateway 看到 header 走 LEAN 模式 (关 9 个 inject + L8 feedback retry)
 *   - mid_task retry 不受影响 (BL-FIX23-L8-fix 5/13)
 *
 * 关闭时:
 *   - 不带 header → gateway 走完整注入 (副手"懂员工")
 *   - 状态写 localStorage 持久化 (Companion 重启后保留员工选择)
 *
 * 跨 session 隔离 — 哪个 chat session 开就哪个走 LEAN, 不需要重启 gateway.
 */

import { create } from "zustand";

const STORAGE_KEY = "catfish.teaching_mode";

function loadFromStorage(): boolean {
  try {
    return localStorage.getItem(STORAGE_KEY) === "1";
  } catch {
    return false;
  }
}

function saveToStorage(on: boolean): void {
  try {
    if (on) localStorage.setItem(STORAGE_KEY, "1");
    else localStorage.removeItem(STORAGE_KEY);
  } catch {
    // localStorage 满 / 禁用 — 静默, 当前 session 内 zustand 仍工作
  }
}

interface TeachingState {
  on: boolean;
  toggle: () => void;
  setOn: (on: boolean) => void;
}

export const useTeachingStore = create<TeachingState>((set) => ({
  on: loadFromStorage(),
  toggle: () => set((s) => {
    const next = !s.on;
    saveToStorage(next);
    return { on: next };
  }),
  setOn: (on: boolean) => {
    saveToStorage(on);
    set({ on });
  },
}));
