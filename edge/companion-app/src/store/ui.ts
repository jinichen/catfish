import { create } from "zustand";

import { useChatStore } from "./chat";
import { sessionCreate, sessionMessageAppend } from "../lib/tauri";

// "sessions" tab 已并入 "chat" 的 sidebar (P0-3.1 后), 不再单独 tab.
// "email" tab 5/18 BL-COMPANION-EMAIL-TAB 加 — AI 邮件管家独立 tab, 跟工作台 / 仪表盘同级.
// "briefing" tab 5/20 BL-COMPANION-DAILY-BRIEFING-MVP 加 — 早安播报独立 tab,
//   开窗一眼看今日 (邮件 / 日历 / 工作计划 / 建议). 跟 Dashboard 解耦免跟"我的画像 /
//   服务"等系统信息混淆.
export type TabId = "chat" | "console" | "dashboard" | "email" | "briefing";

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
  // 5/20 BL-COMPANION-TAB-ORDER-SWAP: default tab 从 chat 改 briefing —
  // 开窗第一眼看今日总览 (邮件未读 + 日历 + 工作计划 + LLM 建议), 跟员工真实
  // 早起开 Companion 的需求对齐. 老 default=chat 历史: BL-E11 五一 sprint 默认对话.
  // startProactiveChat 等仍切到 chat (员工对话场景), 这里只改首次启动.
  activeTab: "briefing",
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
