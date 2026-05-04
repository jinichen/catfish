/** BL-E15 专注模式 (五一 sprint 5/3 晚, 前 "领导来了" — 央企语境改名)
 *
 * 全局快捷键 Cmd+Shift+F 触发, Tauri 后端 emit "catfish:focus_mode_toggle" 事件,
 * App.tsx 顶层 listen, 调 toggle. true 时整个 AppShell 被替换成 FocusModeView (伪 IDE).
 *
 * 设计:
 *   - 不持久化 (重启后默认关, 防误进永久 stuck)
 *   - 只 toggle (按一次开 / 再按一次关)
 *   - 完全 client-side state, 不上报 gateway
 */

import { create } from "zustand";

interface FocusState {
  active: boolean;
  toggle: () => void;
  exit: () => void;
}

export const useFocusStore = create<FocusState>((set) => ({
  active: false,
  toggle: () => set((s) => ({ active: !s.active })),
  exit: () => set({ active: false }),
}));
