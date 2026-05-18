import { create } from "zustand";

import { useChatStore } from "./chat";
import { sessionCreate, sessionMessageAppend } from "../lib/tauri";

// "sessions" tab 已并入 "chat" 的 sidebar (P0-3.1 后), 不再单独 tab.
// "email" tab 5/18 BL-COMPANION-EMAIL-TAB 加 — AI 邮件管家独立 tab, 跟工作台 / 仪表盘同级.
export type TabId = "chat" | "console" | "dashboard" | "email";

interface UIState {
  activeTab: TabId;
  darkMode: boolean;
  /** 5/18 BL-COMPANION-ABOUT-HIJACK: 关于鲶鱼模态开关, 顶部 chip + 菜单 emit 共享. */
  aboutOpen: boolean;
  /** 5/6 鸿波: prefill 输入框逻辑废弃, 主动闲聊改成"桌宠直接以 assistant 身份说话".
   *  这个字段保留 (兼容 ChatInput consumeChatPrefill 调用), 但永远空字符串.
   *  pendingChatPrefill = "" 永远 → ChatInput 不会自动 prefill. */
  pendingChatPrefill: string;
  setActiveTab: (tab: TabId) => void;
  toggleDarkMode: () => void;
  openAbout: () => void;
  closeAbout: () => void;
  /** 5/6 鸿波要求: 桌宠主动闲聊 = 一条 assistant message 直接出现在 chat 里
   *  (不再塞员工输入框让员工自己按发送). 切到 chat tab + addMessage 当前会话末尾.
   *  顺手持久化 (sessionCreate / sessionMessageAppend), 切走会话回来还能看到. */
  startProactiveChat: (starter: string) => void;
  consumeChatPrefill: () => string;
}

export const useUIStore = create<UIState>((set, get) => ({
  // 默认进入对话 tab —— 这是员工最常用的功能
  activeTab: "chat",
  darkMode: false,
  aboutOpen: false,
  pendingChatPrefill: "",
  setActiveTab: (tab) => set({ activeTab: tab }),
  toggleDarkMode: () => set((s) => ({ darkMode: !s.darkMode })),
  openAbout: () => set({ aboutOpen: true }),
  closeAbout: () => set({ aboutOpen: false }),
  startProactiveChat: (starter) => {
    set({ activeTab: "chat" });
    // in-memory: 直接在当前 chat 末尾插一条 assistant message
    const id = `proactive-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    const ts = new Date().toISOString();
    useChatStore.getState().addMessage({
      id,
      role: "assistant",
      content: starter,
      ts,
      status: "done",
    });
    // 异步持久化 — 先 ensure persistedSessionId, 再 append.
    // 失败静默 (不影响 in-memory 已显示的消息).
    void (async () => {
      try {
        let sid = useChatStore.getState().persistedSessionId;
        if (!sid) {
          const out = await sessionCreate({
            model: useChatStore.getState().model,
          });
          sid = out.id;
          useChatStore.getState().setPersistedSessionId(sid);
        }
        await sessionMessageAppend({
          sessionId: sid,
          role: "assistant",
          content: starter,
        });
      } catch (e) {
        console.warn("[proactive] 持久化主动闲聊 message 失败:", e);
      }
    })();
  },
  consumeChatPrefill: () => {
    const s = get().pendingChatPrefill;
    if (s) set({ pendingChatPrefill: "" });
    return s;
  },
}));
