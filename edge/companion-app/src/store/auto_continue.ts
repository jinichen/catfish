/** Auto-continue toggle (5/13 鸿波"长程任务咋办" — gateway 删 BL-FIX23 retry 后客户端补).
 *
 * 取代之前 gateway 层 BL-FIX23 mid_task plan-only retry. gateway 现在干净
 * 转发, 长任务中途 LLM `finish_reason=stop` 不调 tool 时, 由用户在客户端
 * 主动开 toggle 控制要不要"自动续跑".
 *
 * 开启时:
 *   - 状态进 zustand store + localStorage 持久化
 *   - useChat outer loop 看到 LLM stop + 没 tool_call + 这一 send() 跑过 tool
 *     → 自动 append 一条 user "继续", 续到下一轮
 *   - 上限 MAX_AUTO_CONTINUES (3) 防死循环 — 跑到上限就停
 *
 * 关闭时 (默认):
 *   - 跟 ChatGPT 一样: LLM stop 就 stop, 用户自己打"继续" 续跑
 *
 * 跟 gateway BL-FIX23 retry 的关键区别:
 *   - 这是**客户端**逻辑, 用户开/关自己控
 *   - 只影响开 toggle 的 session, 不影响其他员工
 *   - 看到自动发"继续" 是可见的 (UI 显淡色 user msg), 不像 gateway 偷偷重发
 *   - 不灌 hint 进 prompt, 只多发一条短 "继续", context 涨得少
 */

import { create } from "zustand";

const STORAGE_KEY = "catfish.auto_continue";

/** 自动续跑上限 — 防死循环. 3 轮够大部分长任务接力, 太多就该手动介入. */
export const MAX_AUTO_CONTINUES = 3;

/** 自动续跑发的消息 — 保持短, 不灌 hint, 让 LLM 接前文继续. */
export const AUTO_CONTINUE_PROMPT = "继续";

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

interface AutoContinueState {
  on: boolean;
  toggle: () => void;
  setOn: (on: boolean) => void;
}

export const useAutoContinueStore = create<AutoContinueState>((set) => ({
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
