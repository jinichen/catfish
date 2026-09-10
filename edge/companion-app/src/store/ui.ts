import { create } from "zustand";

import { useChatStore } from "./chat";
import { sessionCreate, sessionMessageAppend } from "../lib/tauri";

export interface PendingEmailChat {
  requestId: string;
  emailId: string;
  prompt: string;
}

// "sessions" tab 已并入 "chat" 的 sidebar (P0-3.1 后), 不再单独 tab.
// "email" tab 5/18 BL-COMPANION-EMAIL-TAB 加 — AI 邮件管家独立 tab, 跟工作台 / 仪表盘同级.
// "briefing" tab 5/20 BL-COMPANION-DAILY-BRIEFING-MVP 加 — 早安播报独立 tab,
//   开窗一眼看今日 (邮件 / 日历 / 工作计划 / 建议). 跟 Dashboard 解耦免跟"我的画像 /
//   服务"等系统信息混淆.
// BL-CATFISH-WIKI-MODE P3.3 (6/4): "知识体系" tab — 3 列 wiki UI (tree/preview/sigma)
// P50 (9/10): "collab" 协同 tab — 请同事的小鲶帮忙 / 等我点头. 鸿波: 塞工作台「今日」区不人性.
export type TabId = "chat" | "console" | "dashboard" | "email" | "briefing" | "wiki" | "collab";

interface UIState {
  activeTab: TabId;
  darkMode: boolean;
  /** 5/18 BL-COMPANION-ABOUT-HIJACK: 关于鲶鱼模态开关, 顶部 chip + 菜单 emit 共享. */
  aboutOpen: boolean;
  /** 5/6 鸿波: prefill 输入框逻辑废弃, 主动闲聊改成"桌宠直接以 assistant 身份说话".
   *  这个字段保留 (兼容 ChatInput consumeChatPrefill 调用), 但永远空字符串.
   *  pendingChatPrefill = "" 永远 → ChatInput 不会自动 prefill. */
  pendingChatPrefill: string;
  /**
   * 邮件页交给工作台的一次性用户请求。
   *
   * 不能复用 startProactiveChat: 后者是给系统通知用的 assistant 消息路径,
   * 会绕过 useChat.send() 自己落 session, 因而会产生会话竞态和“看到了但没处理”。
   */
  pendingEmailChat: PendingEmailChat | null;
  activeEmailChatId: string | null;
  /** P3.3.55 (6/12 鸿波 "信合规审计闭环"): 员工自己开/关审计视图 tab.
   *  默认关. 关时 Dashboard 隐私 section 没第 5 tab "审计视图"; 开时显.
   *  跟 manifesto 公理 1 (员工主权): 员工**自己**决定何时给审计员看. 持久 localStorage. */
  auditViewEnabled: boolean;
  setAuditViewEnabled: (enabled: boolean) => void;
  setActiveTab: (tab: TabId) => void;
  toggleDarkMode: () => void;
  openAbout: () => void;
  closeAbout: () => void;
  /** 5/6 鸿波要求: 桌宠主动闲聊 = 一条 assistant message 直接出现在 chat 里
   *  (不再塞员工输入框让员工自己按发送). 切到 chat tab + addMessage 当前会话末尾.
   *  顺手持久化 (sessionCreate / sessionMessageAppend), 切走会话回来还能看到. */
  startProactiveChat: (starter: string) => void;
  startEmailChat: (emailId: string, prompt: string) => void;
  consumeEmailChat: () => PendingEmailChat | null;
  setActiveEmailChatId: (emailId: string | null) => void;
  consumeChatPrefill: () => string;
}

export const useUIStore = create<UIState>((set, get) => ({
  // 5/20 BL-COMPANION-TAB-ORDER-SWAP: default tab 改 briefing.
  // 5/21 鸿波 Phase 7 开工: 早安 Phase 6 (WorkplanView) 砍, 改做"智能参谋"
  //   (docs/CATFISH-ADVISOR-DESIGN.md). 开发期临时切回 chat 避半成品.
  // 5/22 Phase 7 跑通 (智能参谋主菜 + cold start 三件 + race fix + 硬编码修),
  //   切回早安默认. 员工打开第一眼看主菜 + 选项 + 草稿.
  activeTab: "briefing",
  darkMode: false,
  aboutOpen: false,
  pendingChatPrefill: "",
  pendingEmailChat: null,
  activeEmailChatId: null,
  // P3.3.55: 从 localStorage 恢复, 默认 false
  auditViewEnabled: (() => {
    try {
      return localStorage.getItem("catfish:auditViewEnabled") === "1";
    } catch {
      return false;
    }
  })(),
  setAuditViewEnabled: (enabled) => {
    try {
      localStorage.setItem("catfish:auditViewEnabled", enabled ? "1" : "0");
    } catch {
      /* ignore */
    }
    set({ auditViewEnabled: enabled });
  },
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
  startEmailChat: (emailId, prompt) => {
    const state = get();
    // 同一封邮件在尚未消费或正在处理时, 重复点击只切回工作台, 不再排第二次请求。
    if (
      state.pendingEmailChat?.emailId === emailId ||
      state.activeEmailChatId === emailId
    ) {
      set({ activeTab: "chat" });
      return;
    }
    set({
      activeTab: "chat",
      pendingEmailChat: {
        requestId: `email-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
        emailId,
        prompt,
      },
    });
  },
  consumeEmailChat: () => {
    const action = get().pendingEmailChat;
    if (action) set({ pendingEmailChat: null });
    return action;
  },
  setActiveEmailChatId: (emailId) => set({ activeEmailChatId: emailId }),
  consumeChatPrefill: () => {
    const s = get().pendingChatPrefill;
    if (s) set({ pendingChatPrefill: "" });
    return s;
  },
}));
