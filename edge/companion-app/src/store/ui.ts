import { create } from "zustand";

// "sessions" tab 已并入 "chat" 的 sidebar (P0-3.1 后), 不再单独 tab
export type TabId = "chat" | "console" | "dashboard";

interface UIState {
  activeTab: TabId;
  darkMode: boolean;
  /** 主动闲聊 BL-E13: ProactiveCard / 通知点击时塞一句进 chat 输入框, ChatInput 读后清空. */
  pendingChatPrefill: string;
  setActiveTab: (tab: TabId) => void;
  toggleDarkMode: () => void;
  /** 起一个话题: 切到 chat tab + 预填问题. ChatInput useEffect 读后清空 pendingChatPrefill. */
  startProactiveChat: (starter: string) => void;
  consumeChatPrefill: () => string;
}

export const useUIStore = create<UIState>((set, get) => ({
  // 默认进入对话 tab —— 这是员工最常用的功能
  activeTab: "chat",
  darkMode: false,
  pendingChatPrefill: "",
  setActiveTab: (tab) => set({ activeTab: tab }),
  toggleDarkMode: () => set((s) => ({ darkMode: !s.darkMode })),
  startProactiveChat: (starter) =>
    set({ activeTab: "chat", pendingChatPrefill: starter }),
  consumeChatPrefill: () => {
    const s = get().pendingChatPrefill;
    if (s) set({ pendingChatPrefill: "" });
    return s;
  },
}));
