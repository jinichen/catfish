import { create } from "zustand";

export type TabId = "console" | "sessions" | "dashboard";

interface UIState {
  activeTab: TabId;
  darkMode: boolean;
  setActiveTab: (tab: TabId) => void;
  toggleDarkMode: () => void;
}

export const useUIStore = create<UIState>((set) => ({
  activeTab: "console",
  darkMode: false,
  setActiveTab: (tab) => set({ activeTab: tab }),
  toggleDarkMode: () => set((s) => ({ darkMode: !s.darkMode })),
}));
