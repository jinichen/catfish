import { create } from "zustand";

// "sessions" tab 已并入 "chat" 的 sidebar (P0-3.1 后), 不再单独 tab
export type TabId = "chat" | "console" | "dashboard";

interface UIState {
  activeTab: TabId;
  darkMode: boolean;
  setActiveTab: (tab: TabId) => void;
  toggleDarkMode: () => void;
}

export const useUIStore = create<UIState>((set) => ({
  // 默认进入对话 tab —— 这是员工最常用的功能
  activeTab: "chat",
  darkMode: false,
  setActiveTab: (tab) => set({ activeTab: tab }),
  toggleDarkMode: () => set((s) => ({ darkMode: !s.darkMode })),
}));
