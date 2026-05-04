import { useEffect } from "react";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { listen } from "@tauri-apps/api/event";

import AuthBanner from "./components/AuthBanner";
import DevUserSwitcher from "./components/DevUserSwitcher";
import FocusModeView from "./components/FocusModeView";
import LoginGate from "./components/LoginGate";
import OnboardingWizard from "./components/OnboardingWizard";
import TabBar from "./components/TabBar";
import ChatTab from "./tabs/Chat/ChatTab";
import ConsoleTab from "./tabs/Console/ConsoleTab";
import DashboardTab from "./tabs/Dashboard/DashboardTab";
import { useUIStore } from "./store/ui";
import { useAgentStore } from "./store/agent";
import { useFocusStore } from "./store/focus";
import { useProactiveScheduler } from "./hooks/useProactiveScheduler";

export default function App() {
  const activeTab = useUIStore((s) => s.activeTab);
  const loadAgentPrefs = useAgentStore((s) => s.loadAgentPrefs);
  const focusActive = useFocusStore((s) => s.active);
  const toggleFocus = useFocusStore((s) => s.toggle);

  // BL-E11 命名权: 启动拉一次 agent prefs (员工自定义鲶鱼名 + 人设),
  // ChatPanel / Onboarding / 通知等多处 UI 共用. Onboarding 改了立即更新 store.
  useEffect(() => {
    void loadAgentPrefs();
  }, [loadAgentPrefs]);

  // BL-E13 主动闲聊: 每分钟看一次, 9:30 / 14:00 / 17:30 自动 macOS 通知 + 起话题.
  useProactiveScheduler();

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
    return <FocusModeView />;
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
        {activeTab === "console" && <ConsoleTab />}
        {activeTab === "dashboard" && <DashboardTab />}
      </main>
    </div>
  );
}
