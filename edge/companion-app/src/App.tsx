import { useEffect } from "react";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { listen } from "@tauri-apps/api/event";

import AboutModal from "./components/AboutModal";
import AuthBanner from "./components/AuthBanner";
import DevUserSwitcher from "./components/DevUserSwitcher";
import FocusModeView from "./components/FocusModeView";
import LoginGate from "./components/LoginGate";
import OnboardingWizard from "./components/OnboardingWizard";
import TabBar from "./components/TabBar";
import ChatTab from "./tabs/Chat/ChatTab";
// BL-CONSOLE-TAB-KILL (5/16): 控制台 tab 砍, ConsoleTab.tsx 源码留着作 git 历史.
// import ConsoleTab from "./tabs/Console/ConsoleTab";
import DashboardTab from "./tabs/Dashboard/DashboardTab";
import { useUIStore } from "./store/ui";
import { useAgentStore } from "./store/agent";
import { useFocusStore } from "./store/focus";
import { useProactiveScheduler } from "./hooks/useProactiveScheduler";
import { useProactiveTriggers } from "./hooks/useProactiveTriggers";
import { usePetStatusBroadcast } from "./hooks/usePetStatusBroadcast";

export default function App() {
  const activeTab = useUIStore((s) => s.activeTab);
  const loadAgentPrefs = useAgentStore((s) => s.loadAgentPrefs);
  const focusActive = useFocusStore((s) => s.active);
  const toggleFocus = useFocusStore((s) => s.toggle);
  const openAbout = useUIStore((s) => s.openAbout);

  // BL-E27 一次到位: 桌宠状态联动 LLM (idle/thinking/running/done)
  usePetStatusBroadcast();

  // BL-E11 命名权: 启动拉一次 agent prefs (员工自定义鲶鱼名 + 人设),
  // ChatPanel / Onboarding / 通知等多处 UI 共用. Onboarding 改了立即更新 store.
  useEffect(() => {
    void loadAgentPrefs();
  }, [loadAgentPrefs]);

  // BL-E13 主动闲聊 (死时间兜底): 9:30 / 14:00 / 17:30
  useProactiveScheduler();

  // 5/6 BL-E13.5 真主动 Phase A: chat 沉默 / journal deadline / focus 切换 信号触发
  useProactiveTriggers();

  // BL-E15 专注模式: 监听 Tauri 后端 emit 的 toggle 事件 (Cmd+Shift+F 触发).
  // 切到 store, App 顶层根据 active 决定渲染 FocusModeView 还是正常 AppShell.
  useEffect(() => {
    let unlisten: (() => void) | null = null;
    void (async () => {
      unlisten = await listen("catfish:focus_mode_toggle", () => {
        toggleFocus();
      });
    })();
    return () => {
      if (unlisten) unlisten();
    };
  }, [toggleFocus]);

  // 5/18 BL-COMPANION-ABOUT-HIJACK: macOS app menu "鲶鱼 Companion → 关于鲶鱼"
  // 走自定义 React 模态, 不走原生 NSPanel. Rust 端 emit "show-about", 这里接.
  useEffect(() => {
    let unlisten: (() => void) | null = null;
    void (async () => {
      unlisten = await listen("show-about", () => {
        openAbout();
      });
    })();
    return () => {
      if (unlisten) unlisten();
    };
  }, [openAbout]);

  // 5/18 BL-COMPANION-EMAIL-DIGEST-STEP4: 邮件 scheduler 检测到"急"邮件 →
  // Rust 端 emit catfish:email-urgent (含 starter 字符串). 这里接 → 走桌宠
  // 主动闲聊路径 (跟 BL-E13 早9:30/午14:00 主动找你聊同一套 startProactiveChat).
  // 切到 chat tab + 一条 assistant message 自动出现"张三那封紧的来了, 帮你看?"
  const startProactiveChat = useUIStore((s) => s.startProactiveChat);
  useEffect(() => {
    let unlisten: (() => void) | null = null;
    void (async () => {
      unlisten = await listen<{ count: number; starter: string; ids: string[] }>(
        "catfish:email-urgent",
        (event) => {
          const { starter, count } = event.payload;
          if (starter && count > 0) {
            startProactiveChat(starter);
          }
        },
      );
    })();
    return () => {
      if (unlisten) unlisten();
    };
  }, [startProactiveChat]);

  // 五一 sprint 5/5: Esc 隐藏浮窗 (配合 Cmd+Shift+Space 召唤)
  // 输入框聚焦时 Esc 由组件自己处理 (e.g. 关闭弹层); 这里只在 body 聚焦时拦截.
  // BL-E15: 专注模式激活时这个 Esc-hide 不能跑, FocusModeView 自己 capture Esc 退专注.
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      // 专注模式打开时, FocusModeView 用 capture 阶段抢先处理 Esc, 这里不该再 hide
      if (useFocusStore.getState().active) return;
      const target = e.target as HTMLElement | null;
      const tag = target?.tagName?.toLowerCase() ?? "";
      // 输入态 (textarea/input/contenteditable) 不抢, 让组件用 (清空输入 / 关弹窗).
      if (tag === "textarea" || tag === "input" || target?.isContentEditable) return;
      e.preventDefault();
      void getCurrentWindow().hide();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, []);

  // BL-E15: 专注模式激活 → 整个 App 替换成 FocusModeView (不显聊天/Dashboard 杂讯).
  // 注意放在 LoginGate 之前: 即使没登录, 按 Cmd+Shift+F 也能进专注 (员工常用场景:
  // 临时打开 Companion 没登, 按快捷键挡屏).
  if (focusActive) {
    // 5/18 BL-COMPANION-ABOUT-HIJACK: AboutModal 在专注模式也要能弹 (员工
    // 从 macOS menu 触发, 不应被专注 view 吞掉).
    return (
      <>
        <FocusModeView />
        <AboutModal />
      </>
    );
  }

  // SSO Phase 1C: LoginGate 包整个 App. 没登录时挡住, 让员工先点登录.
  // dev_token 模式下 try_load_session 自动返已登录, gate 直接放过.
  //
  // OnboardingWizard 五一 sprint 5/2 BL-F3 加: 首次启动 4 步引导, 走完写 localStorage,
  // 不再显. 任何步骤"稍后再说" 也写 onboarded=true.
  return (
    <LoginGate>
      <AppShell activeTab={activeTab} />
      <OnboardingWizard />
      {/* 5/18 BL-COMPANION-ABOUT-HIJACK: 顶部 chip + macOS app menu 共享的关于模态 */}
      <AboutModal />
    </LoginGate>
  );
}

function AppShell({ activeTab }: { activeTab: string }) {
  // "会话" tab 已并入 "对话" 左侧 sidebar (P0-3.1).
  // 对话 tab 自己管 padding/scroll, 不复用 .app-main padding
  // 品牌 (鲶鱼 Companion) 已在 macOS 原生标题栏显示, 应用内不再加 BrandHeader.
  // 版本号 v0.1.0 移到 Dashboard IdentityCard 的"版本"行.
  if (activeTab === "chat") {
    return (
      <div className="app-shell">
        <AuthBanner />
        <DevUserSwitcher />
        <TabBar />
        <main style={{ flex: 1, minHeight: 0, overflow: "hidden" }}>
          <ChatTab />
        </main>
      </div>
    );
  }

  return (
    <div className="app-shell">
      <AuthBanner />
      <DevUserSwitcher />
      <TabBar />
      <main className="app-main">
        {/* BL-CONSOLE-TAB-KILL (5/16): {activeTab === "console" && <ConsoleTab />} */}
        {activeTab === "dashboard" && <DashboardTab />}
      </main>
    </div>
  );
}
